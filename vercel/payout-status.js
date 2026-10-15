// api/payout-status.js. GET, Authorization: Bearer $PAYOUT_STATUS_TOKEN.
// Returns {ok, all_ok, fresh, at, rigs:{id:{state,since}}}. No hashes, URLs, wallets or labels.
// all_ok is true only if the status is < 5 min old AND every rig is "ok", so a Kuma JSON-query monitor on `$.all_ok == true` alerts on any problem.
import crypto from "node:crypto";
import { statusPath } from "../lib/payout-check.js";
export default async function handler(req, res, { loadJson } = {}) {
  const want = process.env.PAYOUT_STATUS_TOKEN || "", got = String(req.headers.authorization || "").replace(/^Bearer /, "");
  if (req.method !== "GET" || want.length < 32 || got.length !== want.length || !crypto.timingSafeEqual(Buffer.from(got), Buffer.from(want)))
    return res.status(401).json({ ok: false });
  res.setHeader("cache-control", "no-store");
  const s = (await (loadJson || globalThis.gmLoadPrivateJson)(statusPath())) || { at: 0, rigs: {} };
  const fresh = Date.now() / 1000 - (s.at || 0) < 300, rigs = Object.fromEntries(Object.entries(s.rigs || {}).map(([k, v]) => [k, { state: v.state, since: v.since }]));
  const all_ok = fresh && Object.keys(rigs).length > 0 && Object.values(rigs).every(r => r.state === "ok");
  return res.status(200).json({ ok: true, all_ok, fresh, at: s.at || 0, rigs });
}

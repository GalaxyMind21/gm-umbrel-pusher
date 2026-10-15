// api/payout-status.js. GET, Authorization: Bearer $PAYOUT_STATUS_TOKEN.
// Returns {ok, all_ok, fresh, at, rigs:{id:{state,since}}}. No hashes, URLs, wallets or labels.
// all_ok is true only if the status is < 5 min old AND every rig is "ok" (Kuma JSON-query monitor: $.all_ok == true).
import crypto from "node:crypto";
import { statusPath } from "../lib/payout-check.js";
import { makeStore } from "../lib/payout-store.js";
const digest = s => crypto.createHash("sha256").update(String(s)).digest();  // fixed 32-byte buffers: no length mismatch throw
export function authorized(header, want) {
  const got = String(header || "").replace(/^Bearer /, "");
  return typeof want === "string" && want.length >= 32 && got.length > 0 && crypto.timingSafeEqual(digest(got), digest(want));
}
export function makeHandler(store = makeStore()) {
  return async function handler(req, res) {
    if (req.method !== "GET" || !authorized(req.headers.authorization, process.env.PAYOUT_STATUS_TOKEN)) return res.status(401).json({ ok: false });
    res.setHeader("cache-control", "no-store");
    let s;
    try { s = (await store.loadJson(statusPath())) || { at: 0, rigs: {} }; }
    catch { return res.status(503).json({ ok: false, all_ok: false }); }
    const fresh = Date.now() / 1000 - (s.at || 0) < 300;
    const rigs = Object.fromEntries(Object.entries(s.rigs || {}).map(([k, v]) => [k, { state: v.state, since: v.since }]));
    const all_ok = fresh && Object.keys(rigs).length > 0 && Object.values(rigs).every(r => r.state === "ok");
    return res.status(200).json({ ok: true, all_ok, fresh, at: s.at || 0, rigs });
  };
}
export default makeHandler();

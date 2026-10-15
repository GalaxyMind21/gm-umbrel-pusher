// api/payout-status.js — GET, Authorization: Bearer $PAYOUT_STATUS_TOKEN. Returns {at, rigs:{id:{state,since}}}. No hashes, URLs or wallets.
import crypto from "node:crypto";
export default async function handler(req, res) {
  const want = process.env.PAYOUT_STATUS_TOKEN || "", got = String(req.headers.authorization || "").replace(/^Bearer /, "");
  if (req.method !== "GET" || want.length < 32 || got.length !== want.length || !crypto.timingSafeEqual(Buffer.from(got), Buffer.from(want)))
    return res.status(401).json({ ok: false });
  const r = await fetch(`https://blob.vercel-storage.com?prefix=pi/status.json&limit=1`, { headers: { authorization: "Bearer " + process.env.BLOB_READ_WRITE_TOKEN, "x-api-version": "7" }, cache: "no-store" });
  const b = r.ok && (await r.json()).blobs?.[0];
  if (!b) return res.status(200).json({ ok: true, at: 0, rigs: {} });
  const s = await (await fetch(b.url + "?t=" + Date.now(), { cache: "no-store" })).json();
  res.setHeader("cache-control", "no-store");
  return res.status(200).json({ ok: true, at: s.at, rigs: Object.fromEntries(Object.entries(s.rigs || {}).map(([k, v]) => [k, { state: v.state, since: v.since }])) });
}

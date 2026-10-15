// Payout-integrity check, to be added to gm-discord-bot api/ingest.js (reviewed here, deployed separately).
// 1) validPool(): allow optional "payout": add "payout" to the optional keys and `("payout" in p && !validPayout(p.payout))`.
// 2) ingestPool(): when sender === "umbrel" && p.payout, call `await payoutCheck(p.payout, now)` (before saving state).
// Env (Vercel, sensitive): PAYOUT_EXPECTED_JSON = {"<rig>":[{"h":"<32hex>","from"?:ISO,"until"?:ISO,"note"?:str}]}
// Status lives in blob pi/status.json; served (rig ids + states only, no hashes) by api/payout-status.js to the box alerter.
export const validPayout = a => Array.isArray(a) && a.length <= 6 && a.every(x => x && typeof x === "object" && !Array.isArray(x) &&
  typeof x.id === "string" && /^[a-z0-9_-]{1,20}$/.test(x.id) &&
  ((Object.keys(x).length === 2 && typeof x.h === "string" && /^[0-9a-f]{32}$/.test(x.h)) ||
   (Object.keys(x).length === 2 && (x.s === "none" || x.s === "err"))));
const active = (e, t) => (!e.from || t >= Date.parse(e.from)) && (!e.until || t < Date.parse(e.until));
// Returns per-rig state: ok | mismatch | none | err | unconfigured. "mismatch" needs 2 consecutive pushes (~2 min) to fire.
export function judge(payout, expected, prev, nowMs) {
  const out = {};
  for (const x of payout) {
    const exp = (expected[x.id] || []).filter(e => active(e, nowMs));
    let s = !expected[x.id] ? "unconfigured" : x.s ? x.s : exp.some(e => e.h === x.h) ? "ok" : "mismatch";
    const p = prev[x.id] || {}, n = s === p.raw ? (p.n || 0) + 1 : 1;
    // err/none/mismatch only become the alerting state after 2 (mismatch) or 5 (err/none, ~5 min) in a row
    const need = s === "mismatch" ? 2 : s === "err" || s === "none" ? 5 : 1;
    out[x.id] = { raw: s, n, state: n >= need ? s : (p.state || "ok"), since: s === p.raw ? p.since : nowMs };
  }
  return out;
}
export async function payoutCheck(payout, now, { loadJson, putJson }) {
  const expected = JSON.parse(process.env.PAYOUT_EXPECTED_JSON || "{}");
  const prev = (await loadJson("pi/status.json")) || { rigs: {} };
  const rigs = judge(payout, expected, prev.rigs || {}, now * 1000);
  await putJson("pi/status.json", { at: now, rigs });
}

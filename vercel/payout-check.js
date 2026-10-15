// Payout-integrity check, to be added to gm-discord-bot api/ingest.js (reviewed here, deployed separately).
// Wiring (exact):
//   import { validPayout, payoutCheck } from "../lib/payout-check.js";
//   validPool(): add "payout" to optional keys and `|| ("payout" in p && !validPayout(p.payout))`
//   ingestPool(): if (sender === "umbrel") await payoutCheck(p.payout || [], now, { loadJson, putJson });
//     loadJson(path) -> parsed JSON or null; putJson(path, obj) -> writes it. Both must use the PRIVATE blob store
//     (see README) at statusPath(), never a public/guessable URL.
// Env (Vercel, all SENSITIVE): PAYOUT_EXPECTED_JSON = {"<rig>":[{"h":"<32hex>","from"?:ISO,"until"?:ISO,"label"?:"direct"|"mrr"|"home"}]}
//   (no renter names: any other key, incl. "note", is rejected), PAYOUT_BLOB_SECRET (64 hex), PAYOUT_STATUS_TOKEN (>=32 chars).
export const statusPath = () => {
  const s = process.env.PAYOUT_BLOB_SECRET || "";
  if (!/^[0-9a-f]{64}$/.test(s)) throw new Error("PAYOUT_BLOB_SECRET");
  return `pi/${s}/status.json`;
};
export const validPayout = a => Array.isArray(a) && a.length <= 6 && a.every(x => x && typeof x === "object" && !Array.isArray(x) &&
  Object.keys(x).length === 2 && typeof x.id === "string" && /^[a-z0-9_-]{1,20}$/.test(x.id) &&
  ((typeof x.h === "string" && /^[0-9a-f]{32}$/.test(x.h)) || x.s === "none" || x.s === "err"));
const ISO = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/;
export function loadExpected(raw) {
  const e = JSON.parse(raw || "{}");
  for (const [k, list] of Object.entries(e)) {
    if (!/^[a-z0-9_-]{1,20}$/.test(k) || !Array.isArray(list) || !list.length) throw new Error("PAYOUT_EXPECTED_JSON rig " + k);
    for (const x of list) if (!x || Object.keys(x).some(f => !["h", "from", "until", "label"].includes(f)) || !/^[0-9a-f]{32}$/.test(x.h) ||
      ("from" in x && !ISO.test(x.from)) || ("until" in x && !ISO.test(x.until)) || ("label" in x && !["direct", "mrr", "home"].includes(x.label)))
      throw new Error("PAYOUT_EXPECTED_JSON entry " + k);
  }
  return e;
}
const active = (e, t) => (!e.from || t >= Date.parse(e.from)) && (!e.until || t < Date.parse(e.until));
export const BAD = ["mismatch", "missing", "unconfigured", "none", "err"];
// Every expected rig AND every pushed rig gets a state. Debounce: mismatch/missing/unconfigured 2 pushes (~2 min), none/err 5.
export function judge(payout, expected, prev, nowMs) {
  const got = Object.fromEntries(payout.map(x => [x.id, x])), out = {};
  for (const id of new Set([...Object.keys(expected), ...Object.keys(got)])) {
    const x = got[id], exp = (expected[id] || []).filter(e => active(e, nowMs));
    const s = !x ? "missing" : !expected[id] ? "unconfigured" : x.s ? x.s : exp.some(e => e.h === x.h) ? "ok" : "mismatch";
    const p = prev[id] || {}, n = s === p.raw ? (p.n || 0) + 1 : 1, need = s === "ok" ? 1 : s === "none" || s === "err" ? 5 : 2;
    out[id] = { raw: s, n, state: n >= need ? s : (p.state || "ok"), since: s === p.raw ? p.since : nowMs };
  }
  return out;
}
export async function payoutCheck(payout, now, { loadJson, putJson }) {
  const expected = loadExpected(process.env.PAYOUT_EXPECTED_JSON);
  const prev = (await loadJson(statusPath())) || { rigs: {} };
  await putJson(statusPath(), { at: now, rigs: judge(payout, expected, prev.rigs || {}, now * 1000) });
}

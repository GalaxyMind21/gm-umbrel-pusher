"""Payout-integrity fingerprints for the Galaxy Mind pusher. OFF unless GM_PAYOUT_SALT (and GM_MINERS) are set.
Reads the SAME miners as miners.py, through the same validated config and the same read-only calls
(AxeOS GET /api/system/info, cgminer "pools"). Never writes, restarts or reconfigures a miner. Nothing listens.
For each rig it canonicalises EVERY pool slot in order (AxeOS: primary, fallback; cgminer: all pools by Priority)
and sends only  h = HMAC-SHA256(bytes.fromhex(GM_PAYOUT_SALT), "\n".join(slots))[:32 hex], so a changed backup pool alerts too.  The raw URL, wallet and worker never leave.
Without the salt (secret, exactly 64 hex, set ONLY in the Umbrel UI app settings, never in the store compose) the hash cannot be brute-forced from known pool lists.
Vercel compares h with the expected value(s) for that rig and date window; a mismatch alerts Olly.
Canonical form: host lowercased, scheme (stratum+tcp:// etc.) and trailing slash stripped, explicit port, user as-is."""
import hashlib, hmac, os, re, time
import miners
_URL = re.compile(r"^(?:[a-z0-9+.-]+://)?([^/:\s]+)(?::(\d{1,5}))?/?$", re.I)
def canon(url, port, user):
    m = _URL.match(str(url or "").strip())
    if not m: raise ValueError("url")
    p = m.group(2) or (str(int(port)) if port not in (None, "") else "")
    if not p: raise ValueError("port")
    return "%s:%s|%s" % (m.group(1).lower(), p, str(user or "").strip())
def fingerprint(salt, s): return hmac.new(salt, s.encode(), hashlib.sha256).hexdigest()[:32]
def _slots_axeos(host):
    i = miners.axeos_raw(host)  # every slot, fixed order: primary, fallback (an empty fallback hashes as "-")
    out = [canon(i.get("stratumURL"), i.get("stratumPort"), i.get("stratumUser"))]
    out.append(canon(i.get("fallbackStratumURL"), i.get("fallbackStratumPort"), i.get("fallbackStratumUser")) if i.get("fallbackStratumURL") else "-")
    return out
def _slots_cgminer(host):
    pools = miners._cg(host, "pools", time.monotonic()).get("POOLS") or []
    return [canon(p.get("URL"), None, p.get("User")) for p in sorted(pools, key=lambda p: (int(p.get("Priority", 99)), int(p.get("POOL", 99))))]
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
def salt():
    s = os.environ.get("GM_PAYOUT_SALT", "")
    if not s: return None
    if not _HEX64.match(s): raise ValueError("GM_PAYOUT_SALT must be exactly 64 lowercase hex characters")
    return bytes.fromhex(s)
def fingerprint_slots(k, slots): return fingerprint(k, "\n".join(slots))
_cache = {"at": 0, "v": []}
def collect(log):
    k = salt()
    if not k or not os.environ.get("GM_MINERS"): return []
    if time.time() - _cache["at"] < 55: return _cache["v"]
    _cache["at"] = time.time(); out = []
    for rid, c in miners.config().items():
        try:
            sl = (_slots_axeos if c["type"] == "axeos" else _slots_cgminer)(c["host"])
            out.append({"id": rid, "h": fingerprint_slots(k, sl)} if sl else {"id": rid, "s": "none"})
        except Exception as e:
            log("payout read failed", rid, type(e).__name__); out.append({"id": rid, "s": "err"})
    _cache["v"] = out[:6]; return _cache["v"]

"""Payout-integrity fingerprints for the Galaxy Mind pusher. OFF unless GM_PAYOUT_SALT (and GM_MINERS) are set.
Reads the SAME miners as miners.py, through the same validated config and the same read-only calls
(AxeOS GET /api/system/info, cgminer "pools"). Never writes, restarts or reconfigures a miner. Nothing listens.
For each rig it finds the ACTIVE pool (cgminer: "Stratum Active"; AxeOS: primary unless isUsingFallbackStratum is true)
and sends only  h = HMAC-SHA256(GM_PAYOUT_SALT, "host:port|user")[:32 hex].  The raw URL, wallet and worker never leave.
Without the salt (secret, Umbrel app settings only, >= 32 chars) the hash cannot be brute-forced from known pool lists.
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
def _active_axeos(host):
    i = miners.axeos_raw(host)
    fb = i.get("isUsingFallbackStratum") is True
    return canon(i.get("fallbackStratumURL" if fb else "stratumURL"), i.get("fallbackStratumPort" if fb else "stratumPort"),
                 i.get("fallbackStratumUser" if fb else "stratumUser"))
def _active_cgminer(host):
    pools = miners._cg(host, "pools", time.monotonic()).get("POOLS") or []
    act = [p for p in pools if p.get("Stratum Active")]
    if not act: return None  # no active pool: reported as state "none", not hashed
    return canon(act[0].get("URL"), None, act[0].get("User"))
def salt():
    s = os.environ.get("GM_PAYOUT_SALT", "")
    return s.encode() if len(s) >= 32 else None
_cache = {"at": 0, "v": []}
def collect(log):
    k = salt()
    if not k or not os.environ.get("GM_MINERS"): return []
    if time.time() - _cache["at"] < 55: return _cache["v"]
    _cache["at"] = time.time(); out = []
    for rid, c in miners.config().items():
        try:
            s = (_active_axeos if c["type"] == "axeos" else _active_cgminer)(c["host"])
            out.append({"id": rid, "h": fingerprint(k, s)} if s else {"id": rid, "s": "none"})
        except Exception as e:
            log("payout read failed", rid, type(e).__name__); out.append({"id": rid, "s": "err"})
    _cache["v"] = out[:6]; return _cache["v"]

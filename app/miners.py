"""Read-only local miner stats for the Galaxy Mind pusher. OFF unless GM_MINERS is set.
Reads ONLY: AxeOS GET /api/system/info, cgminer API summary/stats/estats/pools on TCP 4028 (non-privileged read commands).
Never writes, restarts or reconfigures a miner. Outbound from the container only; nothing listens.
Output is a strict numeric allowlist: no IPs, MACs, serials, hostnames, pool URLs, wallets or worker names leave here.
The only non-numeric values are two version strings that must match a strict pattern (cgminer x.y.z and a firmware
build id like 25052801_14a19a2), and, when GM_SLOT_KEY is set, a keyed 32-hex fingerprint per pool slot.
GM_SLOT_KEY       OPTIONAL: exactly 64 lowercase hex (app settings only). Per-slot fingerprint
                  h = HMAC-SHA256(key, "gm-slot.v1\n" + "host:port|user")[:32]. Vercel holds the same key and compares h with
                  the renter's own /pool requests, so it can show the renter their own pool URL and label every other slot
                  "backup" without any URL ever leaving the Umbrel.
GM_MINERS='{"<rig id>":{"type":"axeos","host":"<lan-ip>","miner":"<customer miner id>","mrr_rig":"<MRR rig id>"}, ...}'
 (exact IPs, set only in the Umbrel app settings, never in this repo).
GM_MINERS_SUBNET  REQUIRED: the home /24 every host must be inside (CIDR, app settings only).
GM_MINERS_DENY    REQUIRED: comma list of exact IPs to refuse (gateway, the Umbrel, other non-miners).
Always refused: network + broadcast address of the subnet, 172.16.0.0/12 (Docker), 10.21.21.0/24 (Umbrel apps), loopback.
Each host read has a ~10 s total deadline."""
import hashlib, hmac, ipaddress, json, os, re, socket, time, urllib.request
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
_HTTP = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
_RFC1918 = [ipaddress.ip_network(n) for n in ("192.168.0.0/16", "172.16.0.0/12", "10.0.0.0/8")]
_DENY = [ipaddress.ip_network(n) for n in ("10.21.21.0/24", "172.16.0.0/12", "127.0.0.0/8")]
DEADLINE = 10.0
ID_RE = re.compile(r"^[a-z0-9_-]{1,20}$")
def config():
    m = json.loads(os.environ.get("GM_MINERS", "") or "{}")
    net = ipaddress.ip_network(os.environ["GM_MINERS_SUBNET"])
    if net.prefixlen != 24 or not any(net.subnet_of(n) for n in _RFC1918): raise ValueError("GM_MINERS_SUBNET must be a private /24")
    deny = {ipaddress.ip_address(x.strip()) for x in os.environ["GM_MINERS_DENY"].split(",") if x.strip()}
    if not deny: raise ValueError("GM_MINERS_DENY required")
    deny |= {net.network_address, net.broadcast_address}
    for k, v in m.items():
        ip = ipaddress.ip_address(v["host"])
        if not ID_RE.match(k) or v.get("type") not in ("axeos", "cgminer") or ip not in net or ip in deny or any(ip in n for n in _DENY):
            raise ValueError("GM_MINERS entry refused: " + k)
        if "miner" in v and not ID_RE.match(v["miner"]) or "mrr_rig" in v and not re.fullmatch(r"\d{4,10}", v["mrr_rig"]): raise ValueError("bad map: " + k)
    return m
def _f(x, lo=0, hi=1e9):
    try: v = float(x)
    except (TypeError, ValueError): return None
    return round(v, 2) if lo <= v < hi else None
def _clean(d): return {k: v for k, v in d.items() if v is not None}
def axeos(host):
    t0 = time.monotonic()
    with _HTTP.open(urllib.request.Request(f"http://{host}/api/system/info", headers={"user-agent": "gm-pusher/1"}), timeout=5) as r:
        b = b""
        while len(b) < 200000:
            if time.monotonic() - t0 > DEADLINE: raise TimeoutError("deadline")
            c = r.read(16384)
            if not c: break
            b += c
        i = json.loads(b)
    temps = [t for t in (_f(i.get(k), -40, 150) for k in ("temp", "temp2")) if t is not None]
    return _clean({"temp_max_c": max(temps) if temps else None, "vr_temp_c": _f(i.get("vrTemp"), -40, 200),
        "fan_rpm": _f(i.get("fanrpm"), 0, 30000), "fan_pct": _f(i.get("fanspeed"), 0, 101), "power_w": _f(i.get("power"), 0, 10000),
        "hashrate_ths": _f((_f(i.get("hashRate")) or 0) / 1000) if i.get("hashRate") is not None else None,
        "freq_mhz": _f(i.get("frequency"), 0, 5000), "core_mv": _f(i.get("coreVoltageActual") or i.get("coreVoltage"), 0, 5000),
        "uptime_s": _f(i.get("uptimeSeconds"), 0, 1e9), "acc": _f(i.get("sharesAccepted")), "rej": _f(i.get("sharesRejected")),
        "pool_ok": None if i.get("isUsingFallbackStratum") is None else (1 if not i.get("isUsingFallbackStratum") else 0)})
def _cg(host, cmd, t0):
    left = lambda: DEADLINE - (time.monotonic() - t0)
    if left() <= 0: raise TimeoutError("deadline")
    with socket.create_connection((host, 4028), timeout=min(5, left())) as s:
        s.sendall(json.dumps({"command": cmd}).encode()); b = b""
        while len(b) < 500000:
            if left() <= 0: raise TimeoutError("deadline")
            s.settimeout(max(0.1, left())); c = s.recv(65536)
            if not c: break
            b += c
            if b"\x00" in c: break  # cgminer terminates each reply with NUL
    return json.loads(b.split(b"\x00", 1)[0].decode(errors="replace").replace("}{", "},{"))
_TOK = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\[([^\]]*)\]")
def _tokens(reply):
    tok = {}
    for st in reply.get("STATS") or []:
        for v in st.values():
            if isinstance(v, str) and "[" in v: tok.update({k: val for k, val in _TOK.findall(v)})
    return tok
_PINGRE = re.compile(r"^\s*(\d{1,9})(?:\.\d+)?\s*$")
def _ping(v):
    """The miner's own ping to its active pool, from Avalon PING[n] (ms). Whole ms, clamped to 0..60000; None if absent or not a number."""
    m = _PINGRE.match(str(v)) if v is not None else None
    return min(int(m.group(1)), 60000) if m else None
_CGVER = re.compile(r"^\d{1,3}(\.\d{1,3}){1,2}$"); _FWVER = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
_DOTTED4 = re.compile(r"\d+\.\d+\.\d+\.\d+")  # anything shaped like an IPv4 address is refused, even as a version
_URL = re.compile(r"^(?:[a-z0-9+.-]+://)?([^/:\s]+)(?::(\d{1,5}))?/?$", re.I)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
def slot_key():
    k = os.environ.get("GM_SLOT_KEY", "")
    if not k: return None
    if not _HEX64.match(k): raise ValueError("GM_SLOT_KEY must be exactly 64 lowercase hex characters")
    return bytes.fromhex(k)
def canon(url, user):
    """Canonical slot form shared with Vercel: host lowercased, scheme and trailing slash stripped, explicit port, user as-is."""
    m = _URL.match(str(url or "").strip())
    if not m or not m.group(2): return None
    return "%s:%s|%s" % (m.group(1).lower(), m.group(2), str(user or "").strip())
def slot_fp(key, url, user):
    c = canon(url, user)
    return hmac.new(key, ("gm-slot.v1\n" + c).encode(), hashlib.sha256).hexdigest()[:32] if key and c else None
_HOST = re.compile(r"^(?=.*[a-z])[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
_PRIVSUFFIX = (".local", ".lan", ".home", ".internal", ".localdomain", ".home.arpa", ".ts.net")
NODE_PORTS = {"2018"}  # the local Galaxy Mind Public Pool stratum port
def slot_display(url):
    """Sanitized pool display for a slot. Never a user, password or IP.
    "node" = a private-address or LAN-name host on the local pool's stratum port (our Galaxy Mind node);
    "ip"   = any other raw IP (v4 or v6) or private/LAN host, hidden;
    "stratum+tcp://<public hostname>:<port>" (or stratum+ssl) otherwise; None if the URL can't be parsed."""
    u = str(url or "").strip()
    if re.match(r"^(?:[a-z0-9+.-]+://)?\[[0-9a-f:.]+\]", u, re.I): return "ip"  # bracketed IPv6
    m = _URL.match(u)
    if not m or not m.group(2): return None
    host, port = m.group(1).lower().strip("[]").rstrip("."), m.group(2)
    scheme = "stratum+ssl" if u.lower().startswith("stratum+ssl://") else "stratum+tcp"
    try: ip = ipaddress.ip_address(host)
    except ValueError: ip = None
    if ip is not None: return "node" if (ip.version == 4 and any(ip in n for n in _RFC1918) or ip.is_loopback) and port in NODE_PORTS else "ip"
    if "." not in host or host.endswith(_PRIVSUFFIX) or host in ("localhost", "host.docker.internal"):
        return "node" if port in NODE_PORTS else "ip"
    if not _HOST.match(host) or len(host) > 80 or _DOTTED4.search(host.replace("-", ".")) or re.search(r"(^|[.-])(0x)?[0-9a-f]{8}([.-]|$)|(^|\.)\d{8,10}(\.|$)", host) or "--" in host:
        return "ip"  # anything that could carry an address in its name
    return "%s://%s:%s" % (scheme, host, port)
def _age(lst, elapsed, when):
    """Seconds since a pool's last share. cgminer reports unix time; some Avalon builds report seconds since start."""
    v = _f(lst, 0, 1e11)
    if not v: return None
    if v > 1e9: return _f((when or 0) - v, 0, 1e9) if when else None
    return _f((elapsed or 0) - v, 0, 1e9) if elapsed is not None else None
_ver_cache = {}
def _version(host, t0):
    c = _ver_cache.get(host)
    if c and time.time() - c[0] < 3600: return c[1]
    v = (_cg(host, "version", t0).get("VERSION") or [{}])[0]
    cg, fw = str(v.get("CGMiner") or ""), str(v.get("CGVERSION") or v.get("LVERSION") or "")
    out = _clean({"cgminer": cg if _CGVER.match(cg) else None, "fw": fw if _FWVER.match(fw) and not _DOTTED4.search(fw) else None})
    _ver_cache[host] = (time.time(), out); return out
def cgminer(host, extra=None):
    """Numeric stats dict. If `extra` is a dict it also receives "ver" (strict version strings) and "pools" (per-slot numbers)."""
    t0 = time.monotonic()
    s = (_cg(host, "summary", t0).get("SUMMARY") or [{}])[0]
    out = {"uptime_s": _f(s.get("Elapsed")), "acc": _f(s.get("Accepted")), "rej": _f(s.get("Rejected")), "stale": _f(s.get("Stale")),
           "hw_err": _f(s.get("Hardware Errors")), "hashrate_ths": _f((_f(s.get("MHS 5s")) or 0) / 1e6) if s.get("MHS 5s") is not None else None,
           "found_blocks": _f(s.get("Found Blocks")), "discarded": _f(s.get("Discarded")), "get_fail": _f(s.get("Get Failures")),
           "remote_fail": _f(s.get("Remote Failures"))}
    tok = _tokens(_cg(host, "stats", t0))
    num = lambda k, lo=0, hi=1e6: _f(tok.get(k, "").split()[0] if tok.get(k, "").strip() else None, lo, hi)
    fans = [x for x in (num(f"Fan{n}", 0, 30000) for n in range(1, 5)) if x]
    out.update({"temp_max_c": num("TMax", -40, 150), "temp_avg_c": num("TAvg", -40, 150), "fan_rpm": max(fans) if fans else None,
                "fan_pct": num("FanR", 0, 101) if tok.get("FanR") else None, "power_w": num("WALLPOWER", 0, 10000),
                "freq_mhz": num("Freq", 0, 5000)})
    if tok.get("FanR"): out["fan_pct"] = _f(tok["FanR"].rstrip("%"), 0, 101)
    pr = _cg(host, "pools", t0)
    pools = pr.get("POOLS") or []
    when = _f(((pr.get("STATUS") or [{}])[0]).get("When"), 0, 1e11)
    act = [p for p in pools if p.get("Stratum Active")]
    out["pool_ok"] = 1 if act and act[0].get("Status") == "Alive" else 0 if pools else None
    try: est = _tokens(_cg(host, "estats", t0))
    except Exception: est = {}  # estats is optional; fall back to the PING the stats reply carries
    pg = _ping(est.get("PING")); out["pool_ping_ms"] = pg if pg is not None else _ping(tok.get("PING"))
    if act:
        out["last_share_s"] = _age(act[0].get("Last Share Time"), out["uptime_s"], when)
        out["stratum_diff"] = _f(act[0].get("Stratum Difficulty"), 0, 1e15)
    if extra is not None:
        try: key = slot_key()
        except ValueError: key = None  # malformed key: send no fingerprints rather than drop the rig's stats
        sl = []
        for p in sorted(pools, key=lambda p: (int(p.get("Priority", 99)), int(p.get("POOL", 99))))[:3]:
            d = _clean({"prio": _f(p.get("Priority"), 0, 100), "active": 1 if p.get("Stratum Active") else 0, "alive": 1 if p.get("Status") == "Alive" else 0,
                        "acc": _f(p.get("Accepted")), "rej": _f(p.get("Rejected")), "stale": _f(p.get("Stale")),
                        "last_share_s": _age(p.get("Last Share Time"), out["uptime_s"], when), "diff": _f(p.get("Stratum Difficulty"), 0, 1e15)})
            h = slot_fp(key, p.get("URL"), p.get("User"))
            if h: d["h"] = h
            ph = slot_display(p.get("URL")) if p.get("Stratum Active") else None  # active pool only: backup hosts stay here
            if ph: d["ph"] = ph
            sl.append(d)
        if sl: extra["pools"] = sl
        try:
            v = _version(host, t0)
            if v: extra["ver"] = v
        except Exception: pass  # version is optional; never fail the stats read for it
    return _clean(out)
_cache = {"at": 0, "v": []}
def collect(log):
    if not os.environ.get("GM_MINERS"): return []
    if time.time() - _cache["at"] < 120: return _cache["v"]
    _cache["at"] = time.time(); out = []
    for rid, c in config().items():
        extra = {}
        try: m = axeos(c["host"]) if c["type"] == "axeos" else cgminer(c["host"], extra)
        except Exception as e: log("miner read failed", rid, type(e).__name__); continue
        if m: out.append({"id": rid, **({"miner": c["miner"]} if "miner" in c else {}), **({"mrr_rig": c["mrr_rig"]} if "mrr_rig" in c else {}), "m": m, **extra})
    _cache["v"] = out[:6]; return _cache["v"]

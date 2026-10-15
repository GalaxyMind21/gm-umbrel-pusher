"""Read-only local miner stats for the Galaxy Mind pusher. OFF unless GM_MINERS is set.
Reads ONLY: AxeOS GET /api/system/info, cgminer API summary/stats/pools on TCP 4028 (non-privileged read commands).
Never writes, restarts or reconfigures a miner. Outbound from the container only; nothing listens.
Output is a strict numeric allowlist: no IPs, MACs, serials, hostnames, pool URLs, wallets or worker names leave here.
GM_MINERS='{"<rig id>":{"type":"axeos","host":"<lan-ip>","miner":"<customer miner id>","mrr_rig":"<MRR rig id>"}, ...}'
 (exact IPs, set only in the Umbrel app settings, never in this repo).
GM_MINERS_SUBNET  REQUIRED: the home /24 every host must be inside (CIDR, app settings only).
GM_MINERS_DENY    REQUIRED: comma list of exact IPs to refuse (gateway, the Umbrel, other non-miners).
Always refused: network + broadcast address of the subnet, 172.16.0.0/12 (Docker), 10.21.21.0/24 (Umbrel apps), loopback.
Each host read has a ~10 s total deadline."""
import ipaddress, json, os, re, socket, time, urllib.request
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
def cgminer(host):
    t0 = time.monotonic()
    s = (_cg(host, "summary", t0).get("SUMMARY") or [{}])[0]
    out = {"uptime_s": _f(s.get("Elapsed")), "acc": _f(s.get("Accepted")), "rej": _f(s.get("Rejected")), "stale": _f(s.get("Stale")),
           "hw_err": _f(s.get("Hardware Errors")), "hashrate_ths": _f((_f(s.get("MHS 5s")) or 0) / 1e6) if s.get("MHS 5s") is not None else None}
    tok = {}
    for st in _cg(host, "stats", t0).get("STATS") or []:
        for v in st.values():
            if isinstance(v, str) and "[" in v: tok.update({k: val for k, val in _TOK.findall(v)})
    num = lambda k, lo=0, hi=1e6: _f(tok.get(k, "").split()[0] if tok.get(k, "").strip() else None, lo, hi)
    fans = [x for x in (num(f"Fan{n}", 0, 30000) for n in range(1, 5)) if x]
    out.update({"temp_max_c": num("TMax", -40, 150), "temp_avg_c": num("TAvg", -40, 150), "fan_rpm": max(fans) if fans else None,
                "fan_pct": num("FanR", 0, 101) if tok.get("FanR") else None, "power_w": num("WALLPOWER", 0, 10000),
                "freq_mhz": num("Freq", 0, 5000)})
    if tok.get("FanR"): out["fan_pct"] = _f(tok["FanR"].rstrip("%"), 0, 101)
    pools = _cg(host, "pools", t0).get("POOLS") or []
    act = [p for p in pools if p.get("Stratum Active")]
    out["pool_ok"] = 1 if act and act[0].get("Status") == "Alive" else 0 if pools else None
    return _clean(out)
_cache = {"at": 0, "v": []}
def collect(log):
    if not os.environ.get("GM_MINERS"): return []
    if time.time() - _cache["at"] < 120: return _cache["v"]
    _cache["at"] = time.time(); out = []
    for rid, c in config().items():
        try: m = (axeos if c["type"] == "axeos" else cgminer)(c["host"])
        except Exception as e: log("miner read failed", rid, type(e).__name__); continue
        if m: out.append({"id": rid, **({"miner": c["miner"]} if "miner" in c else {}), **({"mrr_rig": c["mrr_rig"]} if "mrr_rig" in c else {}), "m": m})
    _cache["v"] = out[:6]; return _cache["v"]

"""Home-rig pool switcher for the Galaxy Mind pusher. DISABLED unless GM_SWITCH_ENABLED=1.
Outbound only: polls Vercel for HMAC-signed switch jobs, applies them to LAN miners, verifies, reports back.
Nothing listens; no ports are opened. LAN targets are restricted to 192.168.4.0/24 and an explicit allowlist.

Env:
  GM_SWITCH_ENABLED=1                     opt-in flag (default off)
  GM_SWITCH_RIGS='{"titan":{"type":"axeos","host":"192.168.4.X"},"avalon1":{"type":"cgminer","host":"192.168.4.Y"}}'
  GM_SWITCH_URL (default https://gm-discord-bot.vercel.app/api/switch-jobs)
  GM_NTFY_URL / GM_NTFY_TOKEN             optional LAN ntfy fallback (e.g. http://host.docker.internal:13119/<topic>)
Job (inside signed envelope): {"id": hex12, "rig": id, "url": "stratum+tcp://host:port", "user": str, "pass": str, "exp": unix}
"""
import hashlib, hmac, ipaddress, json, os, re, socket, time, urllib.request
LAN = ipaddress.ip_network("192.168.4.0/24")
URL_RE = re.compile(r"^stratum\+(tcp|ssl)://([A-Za-z0-9.-]{3,80}):(\d{2,5})$")
USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,90}$"); PASS_RE = re.compile(r"^[A-Za-z0-9.,=_-]{0,40}$")
SWITCH_URL = os.environ.get("GM_SWITCH_URL", "https://gm-discord-bot.vercel.app/api/switch-jobs")
def enabled(): return os.environ.get("GM_SWITCH_ENABLED") == "1"
def rigs():
    r = json.loads(os.environ.get("GM_SWITCH_RIGS", "{}"))
    for k, v in r.items():
        if v.get("type") not in ("axeos", "cgminer") or ipaddress.ip_address(v["host"]) not in LAN: raise SystemExit("GM_SWITCH_RIGS invalid: " + k)
    return r
def _signed_post(key, sender, body):
    ts = str(int(time.time())); sig = hmac.new(key, b"switch.%s.%s." % (sender.encode(), ts.encode()) + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(SWITCH_URL, data=body, method="POST", headers={"content-type": "application/json", "user-agent": "gm-pusher/1",
        "x-gm-sender": sender, "x-gm-ts": ts, "x-gm-sig": sig})
    with urllib.request.urlopen(req, timeout=20) as r: return json.loads(r.read(65536))
def fetch_jobs(key, sender):
    env = _signed_post(key, sender, b'{"op":"poll"}')
    raw = json.dumps(env.get("jobs", []), separators=(",", ":")).encode()
    want = hmac.new(key, b"jobs." + str(env.get("ts", "")).encode() + b"." + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, str(env.get("sig", ""))) or abs(time.time() - int(env.get("ts", 0))) > 120: raise IOError("bad job signature")
    return env["jobs"]
def report(key, sender, jid, ok, detail):
    _signed_post(key, sender, json.dumps({"op": "result", "id": jid, "ok": ok, "detail": detail[:80]}).encode())
def valid(j, rs):
    return (isinstance(j, dict) and re.fullmatch(r"[0-9a-f]{12}", str(j.get("id", ""))) and j.get("rig") in rs and URL_RE.match(str(j.get("url", "")))
            and USER_RE.match(str(j.get("user", ""))) and PASS_RE.match(str(j.get("pass", "x"))) and int(j.get("exp", 0)) > time.time())
# ---- AxeOS (Bitaxe / NerdOctaxe) HTTP API ----
def _http(method, host, path, obj=None):
    req = urllib.request.Request(f"http://{host}{path}", data=None if obj is None else json.dumps(obj).encode(), method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r: b = r.read(200000); return json.loads(b) if b.strip().startswith(b"{") else {}
def axeos(host, j):
    m = URL_RE.match(j["url"])
    _http("PATCH", host, "/api/system", {"stratumURL": m.group(2), "stratumPort": int(m.group(3)), "stratumUser": j["user"], "stratumPassword": j.get("pass") or "x"})
    _http("POST", host, "/api/system/restart")
    for _ in range(12):
        time.sleep(15)
        try: i = _http("GET", host, "/api/system/info")
        except Exception: continue
        if i.get("stratumURL") == m.group(2) and int(i.get("stratumPort", 0)) == int(m.group(3)) and i.get("stratumUser") == j["user"] and float(i.get("hashRate", 0)) > 0:
            return "verified"
    raise IOError("axeos verify failed")
# ---- cgminer API (Avalon, TCP 4028 on the miner; needs W: access for addpool/switchpool) ----
def _cg(host, cmd, param=None):
    with socket.create_connection((host, 4028), timeout=10) as s:
        s.sendall(json.dumps({"command": cmd, **({"parameter": param} if param is not None else {})}).encode())
        b = b""
        while chunk := s.recv(65536): b += chunk
    return json.loads(b.rstrip(b"\x00").decode(errors="replace"))
def cgminer(host, j):
    pools = lambda: _cg(host, "pools").get("POOLS", [])
    idx = next((p["POOL"] for p in pools() if p.get("URL") == j["url"] and p.get("User") == j["user"]), None)
    if idx is None:
        r = _cg(host, "addpool", f'{j["url"]},{j["user"]},{j.get("pass") or "x"}')
        if r.get("STATUS", [{}])[0].get("STATUS") != "S": raise IOError("addpool refused")
        idx = next(p["POOL"] for p in pools() if p.get("URL") == j["url"] and p.get("User") == j["user"])
    _cg(host, "switchpool", str(idx))
    for _ in range(8):
        time.sleep(15)
        p = next((p for p in pools() if p["POOL"] == idx), {})
        if p.get("Status") == "Alive" and p.get("Priority") == 0 and p.get("Stratum Active"): return "verified"
    raise IOError("cgminer verify failed")
def ntfy(text):
    u = os.environ.get("GM_NTFY_URL")
    if not u: return
    h = {"Title": "Home rig switch FAILED", "Priority": "5"}
    if os.environ.get("GM_NTFY_TOKEN"): h["Authorization"] = "Bearer " + os.environ["GM_NTFY_TOKEN"]
    try: urllib.request.urlopen(urllib.request.Request(u, data=text.encode(), method="POST", headers=h), timeout=10)
    except Exception: pass
def run_once(key, sender, log):
    if not enabled(): return
    rs = rigs()
    for j in fetch_jobs(key, sender)[:4]:
        if not valid(j, rs): log("switch job rejected"); continue
        r = rs[j["rig"]]
        try: d = (axeos if r["type"] == "axeos" else cgminer)(r["host"], j); ok = True
        except Exception as e: d, ok = type(e).__name__ + ": " + str(e)[:60], False
        log("switch", j["id"], j["rig"], "ok" if ok else "FAIL")
        if not ok: ntfy(f"{j['rig']} switch {j['id']} failed ({d}). Switch it manually.")
        try: report(key, sender, j["id"], ok, d)
        except Exception: log("switch report failed")

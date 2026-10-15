#!/usr/bin/env python3
"""Galaxy Mind home-rig pool switcher. SEPARATE container from the pusher. DISABLED unless GM_SWITCH_ENABLED=1.
Outbound only (no listener, no ports). Polls Vercel for Ed25519-signed jobs; the Umbrel holds only the PUBLIC key.
Jobs carry {id, rig, profile, exp} only; pool URLs/users live in GM_SWITCH_PROFILES on the Umbrel.

Env (all set in the Umbrel app settings):
  GM_SWITCH_ENABLED=1        opt-in
  GM_SWITCH_PUBKEY           64-hex Ed25519 public key of the job signer (private key never on Vercel or Umbrel)
  GM_SWITCH_ALLOW            comma list of EXACT miner IPs allowed (no CIDR)
  GM_SWITCH_DENY             comma list of extra IPs to refuse (gateway, the Umbrel itself, ...)
  GM_SWITCH_RIGS             JSON {"titan":{"type":"axeos","host":"<ip>"},"avalon1":{"type":"cgminer","host":"<ip>"}}
  GM_SWITCH_DENY_RIGS        comma list of rig ids never to touch (rented rigs); avalonq* always denied
  GM_SWITCH_PROFILES         JSON {"renter-x":{"url":"stratum+tcp://host:port","user":"...","pass":"x"}, ...}
  GM_SWITCH_URL              job feed (default https://gm-discord-bot.vercel.app/api/switch-jobs)
  GM_SWITCH_STATE            state file on the app data volume (default /data/state.json)
  GM_NTFY_URL / GM_NTFY_TOKEN optional failure alert
"""
import fnmatch, ipaddress, json, os, re, socket, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ed25519
FEED = os.environ.get("GM_SWITCH_URL", "https://gm-discord-bot.vercel.app/api/switch-jobs")
STATE = os.environ.get("GM_SWITCH_STATE", "/data/state.json")
SENDER, AUD = "gm-vercel", "gm-umbrel-switcher"
MAX_EXP, RIG_GAP, INTERVAL = 300, 45 * 60, 60
URL_RE = re.compile(r"^stratum\+(tcp|ssl)://([A-Za-z0-9.-]{3,80}):(\d{2,5})$")
USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,90}$"); PASS_RE = re.compile(r"^[A-Za-z0-9.=_-]{0,40}$")
ID_RE, RIG_RE, PROF_RE = re.compile(r"^[0-9a-f]{12}$"), re.compile(r"^[a-z0-9_-]{1,20}$"), re.compile(r"^[a-z0-9_-]{1,40}$")
ALWAYS_DENY_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "10.21.21.0/24", "169.254.0.0/16", "0.0.0.0/8", "224.0.0.0/4")]
def log(*a): print(time.strftime("%Y-%m-%dT%H:%M:%S"), *a, flush=True)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
HTTP = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))

# ---------------- config (fail closed) ----------------
def config():
    allow = {x.strip() for x in os.environ.get("GM_SWITCH_ALLOW", "").split(",") if x.strip()}
    deny = {x.strip() for x in os.environ.get("GM_SWITCH_DENY", "").split(",") if x.strip()}
    for ip in allow | deny: ipaddress.ip_address(ip)  # exact IPs only
    rigs = json.loads(os.environ.get("GM_SWITCH_RIGS", "{}")); profiles = json.loads(os.environ.get("GM_SWITCH_PROFILES", "{}"))
    deny_rigs = {x.strip() for x in os.environ.get("GM_SWITCH_DENY_RIGS", "").split(",") if x.strip()} | {"avalonq*"}
    for k, v in rigs.items():
        ip = ipaddress.ip_address(v["host"])
        if not RIG_RE.match(k) or v.get("type") not in ("axeos", "cgminer") or not any(ip in ipaddress.ip_network(n) for n in ("192.168.0.0/16", "172.16.0.0/12", "10.0.0.0/8")) or v["host"] not in allow or v["host"] in deny \
           or any(ip in n for n in ALWAYS_DENY_NETS): raise ValueError("rig not allowed: " + k)
    for k, v in profiles.items():
        if not PROF_RE.match(k) or set(v) - {"url", "user", "pass"} or not URL_RE.match(v.get("url", "")) or not USER_RE.match(v.get("user", "")) \
           or not PASS_RE.match(v.get("pass", "x")): raise ValueError("bad profile: " + k)
    pub = bytes.fromhex(os.environ["GM_SWITCH_PUBKEY"]); assert len(pub) == 32
    return {"rigs": rigs, "profiles": profiles, "deny_rigs": deny_rigs, "pub": pub}
def load_state():
    try: return json.load(open(STATE))
    except FileNotFoundError: return {"done": {}, "last": {}, "added": {}}
def save_state(st):
    now = time.time(); st["done"] = {k: t for k, t in st["done"].items() if now - t < 7 * 86400}
    tmp = STATE + ".tmp"; open(tmp, "w").write(json.dumps(st)); os.replace(tmp, STATE)

# ---------------- signed job feed (verify RAW bytes before parsing) ----------------
def fetch_jobs(cfg):
    req = urllib.request.Request(FEED, headers={"user-agent": "gm-switcher/1"})
    with HTTP.open(req, timeout=20) as r:
        if r.status != 200: raise IOError("feed %d" % r.status)
        raw, sig = r.read(65536), r.headers.get("x-gm-sig", "")
    if not re.fullmatch(r"[0-9a-f]{128}", sig) or not ed25519.verify(cfg["pub"], raw, bytes.fromhex(sig)): raise IOError("bad signature")
    env = json.loads(raw)
    if set(env) != {"v", "sender", "aud", "ts", "jobs"} or env["v"] != 1 or env["sender"] != SENDER or env["aud"] != AUD: raise IOError("bad envelope")
    if abs(time.time() - int(env["ts"])) > MAX_EXP: raise IOError("stale feed")
    return env["jobs"] if isinstance(env["jobs"], list) else []
def job_ok(j, cfg, st, now):
    if not isinstance(j, dict) or set(j) != {"id", "rig", "profile", "exp"}: return "shape"  # any url/user/pass => rejected
    if not ID_RE.match(str(j["id"])) or j["id"] in st["done"]: return "replay"
    if not isinstance(j["exp"], int) or not now < j["exp"] <= now + MAX_EXP: return "exp"
    if j["rig"] not in cfg["rigs"] or any(fnmatch.fnmatch(j["rig"], d) for d in cfg["deny_rigs"]): return "rig"
    if j["profile"] not in cfg["profiles"]: return "profile"
    if now - st["last"].get(j["rig"], 0) < RIG_GAP: return "rate"
    return None

# ---------------- AxeOS (Titan / Bitaxe) ----------------
def _http(method, host, path, obj=None):
    req = urllib.request.Request(f"http://{host}{path}", data=None if obj is None else json.dumps(obj).encode(), method=method, headers={"content-type": "application/json"})
    with HTTP.open(req, timeout=10) as r: b = r.read(200000); return json.loads(b) if b.strip().startswith(b"{") else {}
def _axe_apply(host, url, port, user, pw):
    _http("PATCH", host, "/api/system", {"stratumURL": url, "stratumPort": port, "stratumUser": user, "stratumPassword": pw})
    _http("POST", host, "/api/system/restart")
def _axe_check(host, url, port, user):
    for _ in range(12):
        time.sleep(15)
        try: i = _http("GET", host, "/api/system/info")
        except Exception: continue
        if i.get("stratumURL") == url and int(i.get("stratumPort", 0)) == port and i.get("stratumUser") == user and float(i.get("hashRate", 0)) > 0: return True
    return False
def axeos(rig, host, prof, st):
    m = URL_RE.match(prof["url"]); url, port = m.group(2), int(m.group(3))
    i = _http("GET", host, "/api/system/info"); prior = (i.get("stratumURL"), int(i.get("stratumPort", 0)), i.get("stratumUser"))
    if not all(prior): raise IOError("no prior pool to snapshot")
    _axe_apply(host, url, port, prof["user"], prof.get("pass") or "x")
    if _axe_check(host, url, port, prof["user"]): return "verified"
    _axe_apply(host, *prior, "x")  # rollback (AxeOS does not expose the old password; solo/most pools ignore it)
    raise IOError("verify failed, rolled back" + ("" if _axe_check(host, *prior) else " (ROLLBACK UNVERIFIED)"))

# ---------------- cgminer API (Avalon, miner TCP 4028; needs W: access) ----------------
def _cg(host, cmd, param=None):
    with socket.create_connection((host, 4028), timeout=10) as s:
        s.sendall(json.dumps({"command": cmd, **({"parameter": param} if param is not None else {})}).encode()); b = b""
        while (c := s.recv(65536)) and len(b) < 500000: b += c
    r = json.loads(b.rstrip(b"\x00").decode(errors="replace"))
    if r.get("STATUS", [{}])[0].get("STATUS") not in ("S", "I"): raise IOError(cmd + " refused")
    return r
def _pools(host): return _cg(host, "pools").get("POOLS", [])
def _active(host):
    ps = [p for p in _pools(host) if p.get("Stratum Active")]; return ps[0] if ps else None
def _cg_check(host, url, user):
    for _ in range(8):
        time.sleep(15); a = _active(host)
        if a and a.get("URL") == url and a.get("User") == user and a.get("Status") == "Alive": return True
    return False
def cgminer(rig, host, prof, st):
    prior = _active(host)
    if not prior: raise IOError("no prior pool to snapshot")
    url, user = prof["url"], prof["user"]
    p = next((p for p in _pools(host) if p.get("URL") == url and p.get("User") == user), None); added = p is None
    if added:
        _cg(host, "addpool", f'{url},{user},{prof.get("pass") or "x"}')
        p = next(p for p in _pools(host) if p.get("URL") == url and p.get("User") == user)
    _cg(host, "switchpool", str(p["POOL"]))
    if _cg_check(host, url, user):
        for old in st["added"].get(rig, []):  # remove pools WE added for earlier rentals
            q = next((q for q in _pools(host) if q.get("URL") == old[0] and q.get("User") == old[1] and not q.get("Stratum Active")), None)
            if q: _cg(host, "removepool", str(q["POOL"]))
        st["added"][rig] = [[url, user]] if added else []
        return "verified"
    q = next((q for q in _pools(host) if q.get("URL") == prior["URL"] and q.get("User") == prior["User"]), None)
    if q: _cg(host, "switchpool", str(q["POOL"]))
    if added:
        r = next((r for r in _pools(host) if r.get("URL") == url and r.get("User") == user), None)
        if r and not r.get("Stratum Active"): _cg(host, "removepool", str(r["POOL"]))
    raise IOError("verify failed, rolled back" + ("" if q and _cg_check(host, prior["URL"], prior["User"]) else " (ROLLBACK UNVERIFIED)"))

def ntfy(text):
    u = os.environ.get("GM_NTFY_URL")
    if not u: return
    h = {"Title": "Home rig switch FAILED", "Priority": "5"}
    if os.environ.get("GM_NTFY_TOKEN"): h["Authorization"] = "Bearer " + os.environ["GM_NTFY_TOKEN"]
    try: HTTP.open(urllib.request.Request(u, data=text.encode(), method="POST", headers=h), timeout=10)
    except Exception: pass
def run_once(cfg):
    st, now = load_state(), int(time.time())
    for j in fetch_jobs(cfg)[:4]:
        why = job_ok(j, cfg, st, now)
        if why: log("job rejected", why); continue
        st["done"][j["id"]] = now; st["last"][j["rig"]] = now; save_state(st)  # mark BEFORE acting: never re-run
        r = cfg["rigs"][j["rig"]]
        try: d = (axeos if r["type"] == "axeos" else cgminer)(j["rig"], r["host"], cfg["profiles"][j["profile"]], st); ok = True
        except Exception as e: d, ok = type(e).__name__ + ": " + str(e)[:80], False
        save_state(st); log("switch", j["id"], j["rig"], j["profile"], "ok" if ok else "FAIL " + d)
        if not ok: ntfy(f"{j['rig']} switch {j['id']} failed ({d}). Check it manually.")
def main():
    if os.environ.get("GM_SWITCH_ENABLED") != "1":
        log("disabled (GM_SWITCH_ENABLED!=1); idling")
        while True: time.sleep(3600)
    cfg = config(); log("enabled rigs=%d profiles=%d" % (len(cfg["rigs"]), len(cfg["profiles"])))
    while True:
        try: run_once(cfg)
        except Exception as e: log("poll error", type(e).__name__)
        time.sleep(INTERVAL)
if __name__ == "__main__": main()

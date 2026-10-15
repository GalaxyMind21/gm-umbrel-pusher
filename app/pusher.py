#!/usr/bin/env python3
"""Galaxy Mind Public Pool -> Vercel /api/ingest pusher. Outbound only, stdlib only, no state on disk.
Reads Public Pool every GM_INTERVAL s, keeps an allowlist of fields, sends an HMAC-signed POST.
Env: GM_HMAC_KEY (secret, from Umbrel app settings), GM_WALLETS (comma list of customer wallets),
     GM_SENDER (umbrel|box), GM_POOL_URL (default http://host.docker.internal:2019/api),
     GM_INGEST_URL (default https://gm-discord-bot.vercel.app/api/ingest), GM_INTERVAL (default 60), GM_ONCE=1 to run once."""
import hashlib, hmac, json, os, re, sys, time, urllib.request
POOL = os.environ.get("GM_POOL_URL", "http://host.docker.internal:2019/api").rstrip("/")
INGEST = os.environ.get("GM_INGEST_URL", "https://gm-discord-bot.vercel.app/api/ingest")
SENDER = os.environ.get("GM_SENDER", "umbrel")
INTERVAL = max(30, int(os.environ.get("GM_INTERVAL", "60")))
ADDR = re.compile(r"^(bc1[a-z0-9]{8,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$")
NAME = re.compile(r"^[A-Za-z0-9 ._#()+-]{1,40}$")
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?(Z|[+-]\d\d:\d\d)$")
_ctr = [0]
def log(*a): print(time.strftime("%Y-%m-%dT%H:%M:%S"), *a, flush=True)
def key():
    k = os.environ.get("GM_HMAC_KEY", "")
    if len(k) < 32: raise SystemExit("GM_HMAC_KEY missing or too short")
    return k.encode()
def wallets():
    ws = [w.strip() for w in os.environ.get("GM_WALLETS", "").split(",") if w.strip()]
    if not ws or len(ws) > 5 or not all(ADDR.match(w) for w in ws): raise SystemExit("GM_WALLETS invalid")
    return ws
def get(path):
    with urllib.request.urlopen(POOL + path, timeout=15) as r:
        if r.status != 200: raise IOError("pool %d" % r.status)
        return json.loads(r.read(1_000_000))
def num(x):
    v = float(x)
    if not (0 <= v < 1e30): raise ValueError("range")
    return v
def worker(w):
    name, ls, st = str(w["name"]), str(w["lastSeen"]), str(w["startTime"])
    if not NAME.match(name) or not ISO.match(ls) or not ISO.match(st): return None  # drop, never rewrite
    return {"name": name, "hs": num(w["hashRate"]), "best": num(w["bestDifficulty"]), "last_seen": ls, "start": st}
def snapshot(ws):
    net = get("/network")
    out = []
    for a in ws:
        cl = get("/client/" + a)
        out.append({"addr": a, "workers": [x for x in (worker(w) for w in (cl.get("workers") or [])[:20]) if x]})
    return {"v": 1, "kind": "pool", "at": int(time.time()), "net": {"height": int(net["blocks"]), "difficulty": num(net["difficulty"])}, "wallets": out}
def send(payload, k, sender=SENDER, url=INGEST):
    body = json.dumps(payload, separators=(",", ":")).encode()
    ts = str(int(time.time())); _ctr[0] = max(_ctr[0] + 1, time.time_ns() // 1_000_000)  # monotonic across restarts
    ctr = str(_ctr[0])
    sig = hmac.new(k, b"%s.%s.%s." % (sender.encode(), ts.encode(), ctr.encode()) + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=body, method="POST", headers={"content-type": "application/json",
        "user-agent": "gm-pusher/1", "x-gm-sender": sender, "x-gm-ts": ts, "x-gm-ctr": ctr, "x-gm-sig": sig})
    try:
        with urllib.request.urlopen(req, timeout=20) as r: return r.status
    except urllib.error.HTTPError as e: return e.code
def main():
    k, ws = key(), wallets()
    log("start sender=%s wallets=%d interval=%ds" % (SENDER, len(ws), INTERVAL))
    while True:
        t0 = time.time()
        try: log("push", send(snapshot(ws), k))
        except Exception as e: log("error", type(e).__name__)
        if os.environ.get("GM_ONCE"): return
        time.sleep(max(5, INTERVAL - (time.time() - t0)))
if __name__ == "__main__": main()

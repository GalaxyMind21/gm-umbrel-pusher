#!/usr/bin/env python3
"""Box-side alerter (cron every minute). Outbound only: GET Vercel /api/payout-status, POST Olly's ntfy over Tailscale.
Alerts when a rig enters mismatch/none/err, again hourly while it persists, and once on recovery.
Also alerts if the status is stale (> 5 min: pusher or Vercel stopped), so a silenced check can't look healthy.
Config: ~/.config/umbrel/payout-alert.json {"url": ".../api/payout-status", "token": "..."}; ntfy: ~/.config/umbrel/ntfy.json.
State: /workspace/umbrel/state/payout-alert.json. Appends a Kuma push URL ping if "kuma" is set in the config."""
import json, os, time, urllib.request
C = json.load(open(os.path.expanduser("~/.config/umbrel/payout-alert.json")))
N = json.load(open(os.path.expanduser("~/.config/umbrel/ntfy.json")))
ST = "/workspace/umbrel/state/payout-alert.json"
BAD = {"mismatch": "payout target CHANGED", "none": "has no active pool", "err": "unreadable for 5+ min", "stale": "no fresh check (pusher/Vercel silent)"}
def ntfy(title, msg, prio="5"):
    urllib.request.urlopen(urllib.request.Request(N["server"] + "/" + N["topic"], data=msg.encode(), method="POST",
        headers={"Authorization": "Bearer " + N["access_token"], "Title": title, "Priority": prio, "Tags": "rotating_light"}), timeout=10)
now = time.time()
st = json.load(open(ST)) if os.path.exists(ST) else {}
r = json.loads(urllib.request.urlopen(urllib.request.Request(C["url"], headers={"Authorization": "Bearer " + C["token"]}), timeout=15).read())
rigs = {k: v["state"] for k, v in r["rigs"].items()}
if now - r.get("at", 0) > 300: rigs = {"all rigs": "stale"}
for rid, s in rigs.items():
    p = st.get(rid, {"state": "ok", "sent": 0})
    if s in BAD and (p["state"] != s or now - p["sent"] > 3600):
        ntfy("Payout integrity: " + rid, f"{rid} {BAD[s]}. Check the miner's pool settings now. (Expected values: PAYOUT_EXPECTED_JSON)"); p = {"state": s, "sent": now}
    elif s not in BAD and p["state"] in BAD:
        ntfy("Payout integrity OK: " + rid, f"{rid} is back on its expected payout target.", "3"); p = {"state": s, "sent": now}
    else: p["state"] = s
    st[rid] = p
if "all rigs" in st and "all rigs" not in rigs and st["all rigs"]["state"] in BAD:
    ntfy("Payout integrity OK", "Checks are fresh again.", "3"); del st["all rigs"]
os.makedirs(os.path.dirname(ST), exist_ok=True); json.dump(st, open(ST, "w"))
if C.get("kuma"): urllib.request.urlopen(C["kuma"], timeout=10)

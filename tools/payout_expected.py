#!/usr/bin/env python3
"""Compute an expected payout fingerprint (box/Mac only, never in the image).
Salt: file in $GM_PAYOUT_SALT_FILE, exactly 64 hex (same value typed into the Umbrel UI as GM_PAYOUT_SALT). Never printed.
Give EVERY pool slot in miner order (AxeOS: primary then fallback, "-" if empty; cgminer: pool 0,1,2...) as "url|user":
  GM_PAYOUT_SALT_FILE=~/.config/umbrel/payout-salt python3 tools/payout_expected.py blackpearl \
     "stratum+tcp://host:port|user0" "stratum+tcp://host:port|user1" ... [--from ISO] [--until ISO] [--label direct|mrr|home]
Prints {"<rig>": [{"h":..., "from"?, "until"?, "label"?}]} for Vercel env PAYOUT_EXPECTED_JSON (sensitive).
No renter names anywhere (label is an enum). AFTER computing all hashes: `shred -u` the salt file."""
import json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
from payout import canon, fingerprint_slots
a = sys.argv[1:]; opt = lambda k: a[a.index(k) + 1] if k in a else None
flags = {a.index(k) for k in ("--from", "--until", "--label") if k in a}; skip = flags | {i + 1 for i in flags}
pos = [x for i, x in enumerate(a) if i not in skip]
rig, slots = pos[0], ["-" if x == "-" else canon(x.rsplit("|", 1)[0], None, x.rsplit("|", 1)[1]) for x in pos[1:]]
s = open(os.path.expanduser(os.environ["GM_PAYOUT_SALT_FILE"])).read().strip()
if not re.fullmatch(r"[0-9a-f]{64}", s): raise SystemExit("salt must be exactly 64 lowercase hex")
e = {"h": fingerprint_slots(bytes.fromhex(s), slots)}
if opt("--from"): e["from"] = opt("--from")
if opt("--until"): e["until"] = opt("--until")
if opt("--label"):
    if opt("--label") not in ("direct", "mrr", "home"): raise SystemExit("label must be direct|mrr|home")
    e["label"] = opt("--label")
print(json.dumps({rig: [e]}))

#!/usr/bin/env python3
"""Compute expected payout fingerprints (box/Mac only, never in the image).
Salt is read from the file in $GM_PAYOUT_SALT_FILE (same value as the Umbrel GM_PAYOUT_SALT); it is never printed.
  GM_PAYOUT_SALT_FILE=~/.config/umbrel/payout-salt python3 tools/payout_expected.py \
     blackpearl "stratum+tcp://pool.example:42069" "bc1q...worker" [--from 2026-10-01T05:00:00Z] [--until 2026-12-01T06:00:00Z] [--note jphu]
Prints one JSON entry to paste into the rig's list in Vercel env PAYOUT_EXPECTED_JSON:
  {"<rig>": [{"h": "...", "from": "...", "until": "...", "note": "..."}, ...]}   (from/until optional, ISO UTC, until exclusive)"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
from payout import canon, fingerprint
a = sys.argv[1:]
opt = lambda k: a[a.index(k) + 1] if k in a else None
rig, url, user = a[0], a[1], a[2]
salt = open(os.path.expanduser(os.environ["GM_PAYOUT_SALT_FILE"])).read().strip().encode()
if len(salt) < 32: raise SystemExit("salt too short")
e = {"h": fingerprint(salt, canon(url, None, user))}
for k in ("from", "until", "note"):
    if opt("--" + k): e[k] = opt("--" + k)
print(json.dumps({rig: [e]}))

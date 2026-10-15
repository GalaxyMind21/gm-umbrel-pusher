# gm-umbrel-pusher

Tiny outbound-only container that runs on the Galaxy Mind Umbrel. Every 60 s it reads the local Public Pool API,
keeps an allowlist of fields (worker name, hashrate, best difficulty, last seen, start time; per configured customer wallet),
and sends an HMAC-SHA256 signed POST to `https://gm-discord-bot.vercel.app/api/ingest`. Vercel validates it, writes the
Discord bot data and posts #rig-status alerts. Nothing listens; no ports, no UI, no state on disk.

- Image: `ghcr.io/galaxymind21/gm-umbrel-pusher` built only by `.github/workflows/image.yml` from `main`.
  Deploy by **@sha256 digest** (see the Actions run summary). Tags are informational only. No auto-update.
- Base: `gcr.io/distroless/python3-debian12:nonroot`, pinned by digest. Stdlib only, no pip deps.
- Installed via the private community app store `GalaxyMind21/gm-umbrel-store` (app `galaxymind-pusher`).

## Config (Umbrel app settings, never in this repo or the image)
| var | value |
|---|---|
| `GM_HMAC_KEY` | Umbrel sender key (64 hex). Secret. Matches Vercel env `INGEST_KEY_UMBREL`. |
| `GM_WALLETS` | Comma list of customer payout wallets to read from Public Pool (max 5). Not secret. |
| `GM_SENDER` | `umbrel` (set in compose) |
| `GM_POOL_URL` | `http://host.docker.internal:2019/api` (host-published Public Pool port; set in compose) |

## Signing protocol
Headers `x-gm-sender`, `x-gm-ts` (unix s, must be within ±60 s), `x-gm-ctr` (strictly increasing; ms clock),
`x-gm-sig` = hex HMAC-SHA256(key, `sender.ts.ctr.` + body). Body ≤ 8 KB, strict schema, ≤4 accepted pushes/min/key.
Errors are generic (`{"ok":false}`); rejects are logged on Vercel without the payload.

## Key rotation (either sender independently)
1. Make a key on the box: `python3 -c "import secrets;print(secrets.token_hex(32))"` (do not paste it in chat).
2. Vercel: `cd /workspace/gm-discord-bot && printf '%s' "$NEW" | vercel env add INGEST_KEY_UMBREL production --sensitive --force && vercel deploy --prod --yes`
   (box key: `INGEST_KEY_BOX`, and update `/workspace/miner-status/ingest.env`).
3. Umbrel: set `GM_HMAC_KEY` in the app's settings (Umbrel UI → app → Settings → environment, or MCP `set_app_settings` customEnvironment). The app restarts.
4. Confirm: `python3 /workspace/miner-status/check_fresh.py` shows `avalonq:umbrel` within 2 minutes. Until step 3 is done the box backup keeps the bot fresh.
Emergency revoke: remove the env var on Vercel and redeploy; every push from that sender is then rejected.

## Updating the image
Merge to `main` → Actions builds `sha-<commit>` → copy the digest from the run summary → bump the `image:` digest
and `version` in `gm-umbrel-store/galaxymind-pusher/` (PR) → update the app in the Umbrel UI. Log it in CHANGES.log first.

## Payout-integrity watch (optional, inert until `GM_PAYOUT_SALT` is set)
Every ~60 s `app/payout.py` reads EVERY pool slot of each `GM_MINERS` rig, in order (AxeOS: primary, fallback; cgminer: all
pools by Priority), through the existing read-only calls, and pushes `"payout": [{"id":"<rig>","h":"<32 hex>"}]`
(or `{"id","s":"none"|"err"}`). `h` = HMAC-SHA256(salt, ordered `host:port|user` slots joined by newline)[:32].
Raw URLs, wallets and worker names never leave the Umbrel. A changed backup pool alerts just like a changed primary.

**Salt.** `GM_PAYOUT_SALT` = exactly 64 lowercase hex. Set it ONLY in the Umbrel UI (app → Settings → environment), never
in the gm-umbrel-store compose, this repo or the image. To compute expected hashes, put the same value in a temp file
for `tools/payout_expected.py`, compute every rig's hashes, then `shred -u` the file. Rotating = new salt + recompute.

**Vercel** (`vercel/payout-check.js` → gm-discord-bot `lib/`, `vercel/payout-status.js` → `api/`): iterates the expected
list AND the pushed rigs. States: `ok`, `mismatch`, `missing` (expected but absent from the push), `unconfigured` (pushed,
no expectation), `none`, `err`. Every non-ok state alerts (mismatch/missing/unconfigured after 2 pushes ≈ 2 min; none/err ≈ 5 min).
Env, all marked **sensitive**: `PAYOUT_EXPECTED_JSON` (entries `{h, from?, until?, label?}`, label ∈ direct|mrr|home, no
renter names; anything else is rejected), `PAYOUT_BLOB_SECRET` (64 hex; status blob lives at `pi/<secret>/status.json`, in a
private Blob store, never listed or linked), `PAYOUT_STATUS_TOKEN` (≥32 chars).

**Expected values policy.** The MRR configuration of each rig (stratum host `*.miningrigrentals.com` with user
`GalaxyMind.<rig id>`, the full ordered slot set) is a permanent entry with no window (`label: "mrr"`). Direct rentals get
a `label: "direct"` entry with `from`/`until` matching the rental (Black Pearl until 2026-12-01, Marshall until 2026-11-08,
UTC); when it lapses only the MRR entry matches, so a rig left on the renter's pool after the end date alerts.

**Alerting (primary, no agent in the path).** Uptime Kuma JSON-query monitor: GET `https://gm-discord-bot.vercel.app/api/payout-status`,
header `Authorization: Bearer <PAYOUT_STATUS_TOKEN>`, query `all_ok`, expected value `true`, interval 60 s, retries 1,
notification = Olly's ntfy. `all_ok` is false on any non-ok rig or if the status is >5 min old (pusher or Vercel silent),
and a fetch error/401 is also a Kuma failure. Kuma polls outbound; nothing connects into the home network.
**Fallback** `tools/payout_alert.py` (box cron, 1 min): per-rig ntfy text, hourly repeat, recovery notices, alerts after 3
consecutive fetch failures; its Kuma heartbeat push URL is mandatory so a dead poller alerts.

Go-live order: Vercel accepts `payout` first (old Vercel rejects the field) → expected values + Kuma monitor →
`GM_PAYOUT_SALT` in the Umbrel UI → image update (CHANGES.log first).

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
| `GM_SLOT_KEY` | Optional. Exactly 64 lowercase hex. Secret. Matches Vercel env `POOL_SLOT_KEY`. Enables per-pool-slot fingerprints for renter `/status` (see below). |
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

## Renter status fields (cgminer rigs, read-only)
`miners.py` also sends, per cgminer rig: found blocks, discarded work, get/remote failures, seconds since the active
pool's last share, and the active stratum difficulty (all numbers, in `m`); `ver` = `{cgminer, fw}` (strict patterns,
nothing shaped like an IP address); and `pools` = up to 3 slots in priority order with numbers only
(`prio, active, alive, acc, rej, stale, last_share_s, diff`). With `GM_SLOT_KEY` set, each slot also carries
`h` = HMAC-SHA256(key, `"gm-slot.v1\n" + "host:port|user"`)[:32] (same canonical form as the payout watch). Vercel compares
`h` with the renter's own `/pool` requests so it can show them their own pool URL and label every other slot "Backup pool".
`m.pool_ping_ms` is the miner's own ping to its pool as the Avalon reports it (`PING[n]` in `estats`, falling back to
`stats`): whole ms, clamped to 0..60000, omitted when absent. We never measure it ourselves.
No URL, worker, password, MAC, serial or IP leaves the Umbrel.
**Deploy order:** Vercel must accept `ver`/`pools` and the new keys (including `pool_ping_ms`) first (older Vercel rejects them and every push fails).
Tests: `python3 -m unittest discover -s tests` (fake cgminer replies, no network).

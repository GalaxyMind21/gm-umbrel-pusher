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

## Home-rig switcher (separate container, disabled by default)
`switcher/` is its own image and container; the pusher is unchanged and stays isolated. Off unless `GM_SWITCH_ENABLED=1`.
- Outbound only: polls a job feed; no listener, no ports. urllib redirects and proxies disabled.
- Feed is verified with **Ed25519 over the raw body bytes** before parsing. The Umbrel holds only the public key
  (`GM_SWITCH_PUBKEY`); the private key is kept off Vercel. Envelope binds `sender` + `aud` + `ts`.
- Jobs are exactly `{id, rig, profile, exp}`; anything else (url/user/pass) is rejected. Pool profiles live in Umbrel settings.
- Replay: executed IDs persisted (marked before acting), `exp` must be within now..now+300s, max 1 switch per rig per 45 min.
- Targets: exact-IP allowlist (`GM_SWITCH_ALLOW`), RFC1918 only; loopback, 10.21.21.0/24, link-local and `GM_SWITCH_DENY`
  (gateway, Umbrel) refused. `GM_SWITCH_DENY_RIGS` blocks rented rigs; `avalonq*` always denied.
- Snapshots the prior pool, verifies the new one, rolls back on failure, removes cgminer pools it added, ntfy on failure.

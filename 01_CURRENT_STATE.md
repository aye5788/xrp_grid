# MAGI — Current State

Last updated: 2026-05-16 (post-Phase 5 Letta Cloud migration).

## Phase 5 — COMPLETE

Migrated from stateless three-agent council to stateful Letta Cloud agents.
All six provisioning prompts executed; verification passes.

### Built and verified today
- SQLite: `debate_records` (40 cols, indexed by cycle_id and timestamp) + `agent_registry`
- `magi/provision_agents.py` — idempotent Letta agent creation, 7 memory blocks per agent
- `magi/council.py` — parallel Round 0, CONFLICT_MATRIX, Round 1 debate, validate_revision, resolve_consensus
- `magi/orchestrator.py` — rewritten: builds world_state, enforces HARD_RULES, dual-writes
- `observer.py` — added `backfill_outcomes()` for 1h/6h/24h windows, sends 6h outcomes to agent threads
- `dashboard.py` — 5 new council panels; legacy panels (Latest MAGI Decision, Supervisor, Recent Decisions) removed

### Letta Cloud agents (provisioned)
| agent_id | Display name | Model |
|---|---|---|
| casper | Casper | google_ai/gemini-3-flash-preview |
| melchior | Melchior | openai/gpt-4o |
| balthasar | Balthasar | anthropic/claude-sonnet-4-6 |

Letta UUIDs live in `agent_registry`. Get the latest with:
`sqlite3 /root/xrp_grid/observer.db "SELECT agent_id, letta_agent_id, model FROM agent_registry;"`

### Self-hosted Letta — DECOMMISSIONED but PRESERVED
- `letta.service` stopped + disabled
- `/root/xrp_grid/letta/` directory intact (docker-compose.yml, .env, pgdata/) for rollback
- Do not delete

## Verified facts (do NOT re-derive in future sessions)

### Kraken API
- HMAC auth: signature = HMAC-SHA512(path + SHA256(nonce + urlencoded_payload))
- Headers: `API-Key`, `API-Sign`, `Content-Type: application/x-www-form-urlencoded`
- Pair name: `XXRPZUSD` (alt `XRPUSD`, WS `XRP/USD`)
- Price decimals: 5 — lot decimals: 8 — cost decimals: 8
- Order min: 1.65 XRP — cost min: $0.50
- Tick: 0.00001
- Pro tier rate limits: trading max=125 decay=2.34/s; account-mgmt max=20 decay=0.5/s
- Open orders cap: 80 per pair
- `GetAPIKeyInfo` endpoint is **gone** (HTTP 404) — verify tier and permissions via the Kraken web console
- Current keys return `EAPI:Invalid key` — must regenerate before live

### Kraken fees (tier 0)
| 30d vol $ | Taker | Maker |
|---|---|---|
| 0 | 0.40% | 0.25% |
| 10K | 0.35% | 0.20% |
| 50K | 0.24% | 0.14% |
| 100K | 0.22% | 0.12% |
| 250K | 0.20% | 0.10% |
| 500K | 0.18% | 0.08% |
| 1M | 0.16% | 0.06% |
| 10M | 0.10% | 0.00% |

`config.py` uses `TAKER_FEE = 0.0026`, `MAKER_FEE = 0.0016` (tier-0
placeholder; recompute after live volume builds).

### Asset analysis (completed; do not re-derive)
- DOGE: best grid PnL historically (~$606 over ~4.2yr, ~2.5%/4 levels) — top performer
- XRP: most forgiving grid dynamics — current active asset
- SOL: narrow grid characteristics — viable but constrained
- ADA: eliminated (does not meet grid trading criteria)

### Optimal grid spacing (determined per asset)
- XRP: 1.5%
- DOGE: 2.5%
- SOL: 2.0%

Hard caps in `config.py`: `MAX_GRID_SPACING_PCT = 0.025`, `MIN_GRID_SPACING_PCT = 0.003`.

## Environment

`/root/xrp_grid/.env` contains:
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY` (main system uses GOOGLE_API_KEY for Casper's direct path; Letta Cloud reads from Casper's model handle separately)
- `KRAKEN_API_KEY`, `KRAKEN_API_SECRET` (currently invalid — regenerate before live)
- `COINBASE_API_KEY`, `COINBASE_API_SECRET` (legacy, unused; `EXCHANGE = "kraken"`)
- `CF_ACCOUNT_ID`, `CF_GATEWAY_ID`, `CF_AIG_TOKEN` — Cloudflare AI Gateway
- `LETTA_API_KEY` — added 2026-05-16 for Letta Cloud
- Backup of pre-migration .env: `/root/xrp_grid/.env.pre-cloud-migration.bak`

`/root/xrp_grid/letta/.env` exists but is unused (self-hosted dormant).

## Services
| Service | State |
|---|---|
| magi.service | active |
| magi-dashboard.service | active |
| letta.service | inactive + disabled (intentional) |
| docker.service | active (no MAGI containers; engine doesn't use Docker) |

Restart pattern: `systemctl restart magi.service magi-dashboard.service`

## Outstanding issues (not blocking paper mode)
- **nginx basic auth** — dashboard token visible in page source. Required before live.
- **Kraken key invalid** — regenerate before live.
- **Base rates staleness** — deferred.
- **CHANGELOG.md** — deferred.

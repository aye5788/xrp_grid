# MAGI — Current State

Last updated: 2026-05-17 (end-of-session 11-item refresh).

## Phase 5 — COMPLETE

Migrated from stateless three-agent council to stateful Letta Cloud agents.
All six provisioning prompts executed; verification passes.

### Built and verified — Phase 5 baseline (2026-05-16)
- SQLite: `debate_records` + `agent_registry`
- `magi/provision_agents.py` — idempotent Letta agent creation, 7 memory blocks per agent
- `magi/council.py` — parallel Round 0, CONFLICT_MATRIX, Round 1 debate, validate_revision, resolve_consensus
- `magi/orchestrator.py` — builds world_state, enforces HARD_RULES, dual-writes
- `observer.py` — `backfill_outcomes()` for 1h/6h/24h windows, sends 6h outcomes to agent threads
- `dashboard.py` — council panels; legacy Latest MAGI Decision / Supervisor / Recent Decisions panels removed

### Session 2026-05-17 changes (11-item summary)

1. **Persona overhaul** — all three agents got new persona prompts: identical
   1752-byte SYSTEM CONTEXT preamble, numbered decision-tree with "first
   matching gate wins" precedence, 2 worked examples each with `WRONG action`
   callouts, 1 derived-quantity formula each. Melchior's TIGHTEN path was
   structurally unreachable and is now reachable. Live char counts: Casper
   7883, Melchior 7951, Balthasar 7940.
2. **New hard rules** in `enforce_hard_rules`: `[GRID_DEGENERATE]`,
   `[RECENTRE_COOLDOWN]`, `[PAUSE_INVALID]`. `hours_since_last_fill` and
   `hours_since_last_rebuild` exposed in `world_state`.
3. **CONFLICT_MATRIX expansion** — predicate signature changed from
   `(round_0)` to `(round_0, world_state)`; 4 grid-state-aware rules added
   (one-sided MAINTAIN, stale MAINTAIN, PAUSE_LONGS on empty buys,
   PAUSE_SHORTS on empty sells). First Round 1 debate ever fired at
   `cyc_1779032110`. **Known gap**: no rule covers the current divergence
   pattern (Casper TRENDING/RANGING + Melchior RECENTRE + Balthasar CLEAR);
   Round 1 is not firing on the real disagreement. → `02_NEXT_BUILD_TASKS.md`.
4. **Engine guards** in `grid/engine.py` — post-action integrity guard
   rebuilds if a risk action leaves the book one-sided. PAUSE_LONGS /
   PAUSE_SHORTS emit WARN "no-op" when `cancelled==0 AND pre-count==0`.
5. **Scheduler** — `MAGI_HOURS_EST = [0, 4, 8, 12, 16, 20]` (every 4 hours,
   6 cycles/day; reduced from hourly on 2026-05-18 to bring monthly cost
   inside the $20 Letta plan — ~$13/mo @ 6 cycles/day). Startup debounce
   reads `debate_records` (was reading sparse `magi_decisions` and firing
   duplicates on restart). `/internal/trigger_magi` reads `debate_records`.
6. **`debate_records` schema** — added `hard_rule_overrides` (JSON-encoded
   list of bracketed tags), `balthasar_concerns`, `casper_concerns` (schema
   symmetry with `melchior_concerns`). Dashboard's latest-override-tag panel
   and 30-day override-count panel migrated to read `hard_rule_overrides`.
   `magi_decisions` dual-write retained; documented at `database.py:614`.
   **17 of 38 historical rows** still have NULL `hard_rule_overrides`;
   30-day panel under-reports until they age out.
7. **`provision_agents.py`** UPDATE path now syncs persona blocks **and**
   LLM config knobs from `AGENT_CONFIG`, idempotently. Re-running the script
   is the canonical way to push persona + config edits to live agents.
8. **LLM config equalisation** — `temperature` 1.0 → 0.3, `max_output_tokens`
   → 8192 across all three. Thinking enabled at comparable levels: Anthropic
   `effort=medium` + `budget_tokens=2048`, OpenAI `reasoning_effort=medium`,
   Google AI `thinking_config.thinking_budget=2048`. Provider asymmetry
   documented: GPT-4o has no native extended-thinking budget;
   `reasoning_effort` is the closest equivalent. `parallel_tool_calls` is
   platform-forced to `True` and cannot be set `False`.
9. **self_model curation** — Casper's and Melchior's `self_model` blocks
   rewritten to retire reflections written under the prior persona. Casper
   flipped from RANGING to TRENDING bearish on the first post-curation cycle
   and is holding. Melchior **did not** shift — GPT-4o is pattern-matching
   prior conversation-turn responses (evidence list is byte-identical
   across cycles 40–46 including the stale `autocorr 1h: 0.0218` value).
   Balthasar untouched (was aligned). Pre-intervention snapshot at
   `/tmp/self_model_snapshot_2026-05-17.json`. Six orphan persona blocks
   identified at project scope; not deleted this session.
10. **Architecture-intent reframe** (operator-led) — the three providers are
    chosen to offset each other's biases by design. Diversity is the
    architecture's strength. The right lever for stuck-agent behaviour is
    `CONFLICT_MATRIX` expansion routing genuine divergence to Round 1, not
    per-agent compliance fixes. Captured in `CLAUDE.md` §3.
11. **`CLAUDE.md` authored** at repo root — operating discipline, architecture
    intent, recurring failure patterns. Auto-loaded by Claude Code at
    session start.

### Letta Cloud agents (provisioned, verified live 2026-05-17)

| agent_id | Letta agent ID | Model | Persona chars (live) | Self-model chars |
|---|---|---|---|---|
| casper | `agent-8c4f3a1e-a662-4ade-9336-7d24300612ee` | google_ai/gemini-3-flash-preview | 7883 | 1268 |
| melchior | `agent-65ee1ab4-421a-4111-9631-5ddf2b5113d4` | openai/gpt-4o | 7951 | 1227 |
| balthasar | `agent-139c7480-63da-40f3-8731-14772ba8d17d` | anthropic/claude-sonnet-4-6 | 7940 | 547 |

Letta UUIDs are also in `agent_registry`. Refresh via:
`sqlite3 /root/xrp_grid/observer.db "SELECT agent_id, letta_agent_id, model FROM agent_registry;"`

**Per-agent LLM config** (synced via `provision_agents.AGENT_CONFIG`,
idempotent): temperature 0.3, max_output_tokens 8192, thinking enabled at
medium effort with 2048-token budget where the provider supports it. GPT-4o
uses `reasoning_effort=medium` (no native extended-thinking budget).
`parallel_tool_calls` is platform-forced True.

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

## Live state (verified end-of-session 2026-05-18 ~10:00 UTC)

- MAGI cadence: **every 4 hours (6 cycles/day)**, EST hours [0, 4, 8, 12,
  16, 20]. Projected Letta monthly cost ~$13/mo at this rate, inside the
  $20/mo plan ceiling. Down from hourly (~$3.20/day, ~$96/mo) which was
  the proximate cause of the credit blowout earlier this session.
- Letta Cloud credit balance: **$-241 (negative)** as of session end.
  Production magi.service is alive but all council calls are 402-rejected
  until credits are restored or BYOK migration lands. Cycle work falls to
  safe-defaults silently.
- Grid centre $1.38×, spacing at MAX 2.5%, 10 levels (per latest grid_state).
- Open orders: 6 buys / 3 sells (skew unchanged).
- vol_regime: LOW, low-vol drift continues.
- **Last real fill: 2026-05-15T17:08 — ~68h ago. Fill drought continues.**
- Production Letta agents (persona/self_model) on Melchior: v1 (decision-
  tree) per the rollback earlier this session. v2 Grid Economist persona
  and Branch A/B work present on disk but not pushed live (eval gate
  failures across multiple iterations).
- Shadow infrastructure migrated to 24-variant (lc, sp) keying with
  expected_pnl_pct closed-form math persisted per variant. World_state now
  carries `shadow_variants` (24 entries), `current_variant_position`,
  `current_fee_tier_pct`, and `cooldown_status`.
- Letta agent count: 13 (3 production + 10 eval, last keep-3 cohorts).
  Cleanup leak fixed (FIX A/B/C/D/E this session); dashboard widget shows
  live count with amber/red banding.

## Outstanding issues

### Engineering (not blocking paper mode)
- **Melchior conversation-history anchoring** — GPT-4o reproduces prior-cycle
  evidence byte-for-byte (including stale `autocorr_1h: 0.0218` while
  world_state has 0.0222). self_model curation did not shift it in one cycle.
  Decision pending: clear Letta message history, accept gradual drift, or
  rely on Round 1 once CONFLICT_MATRIX expands. → `02_NEXT_BUILD_TASKS.md`.
- **CONFLICT_MATRIX coverage gap** — no rule matches the current empirical
  divergence pattern (Casper=TRENDING/RANGING + Melchior=RECENTRE +
  Balthasar=CLEAR). Round 1 is not firing on the real disagreement.
- **17 NULL `hard_rule_overrides` rows** in `debate_records` from the
  pre-column-migration window; under-reports the 30-day override panel
  until rows age out.
- **Six orphan persona blocks** at the Letta project scope from prior
  provisioning runs; not attached to any current agent but visible in the
  Letta web UI's Memory blocks page. Cleanup pending.
- **Dashboard `magi_decisions` reads** — two analytic reads migrated this
  session; `/api/status:1777` still reads `magi_decisions` for back-compat.
  Full migration deferred until dual-write is retired.

### Pre-live (still blocking live trading)
- **nginx basic auth** — dashboard token visible in page source.
- **Kraken keys** — current keys return `EAPI:Invalid key`; regenerate.
- **Base rates staleness** — deferred.

### Deferred docs
- **CHANGELOG.md** — long deferred.

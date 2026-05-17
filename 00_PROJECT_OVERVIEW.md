# MAGI — XRP Grid Bot — Project Overview

## What this system is

MAGI is a grid-trading bot for XRP/USD on Kraken, currently in paper mode. The
end goal is profitable live trading at meaningful scale. Paper mode is
validation only. Hard constraint: right >50% of the time AND profitable after
fees.

## Architecture

Three layers, complementary by design:

1. **Council (judgment)** — three Letta Cloud agents vote independently each
   cycle (Round 0). Each owns its action vocabulary:
   - Casper → regime: `RANGING | TRENDING | UNCERTAIN`
   - Melchior → grid action: `MAINTAIN | RECENTRE | TIGHTEN | WIDEN`
   - Balthasar → risk action: `CLEAR | PAUSE_LONGS | PAUSE_SHORTS | HALT`
2. **Hard rules (survival)** — `magi/orchestrator.py:enforce_hard_rules`
   applies Python-enforced overrides on top of council consensus. These are
   non-negotiable; the council can be overridden silently and there is no
   penalty for being overridden.
3. **Engine (execution)** — `grid/engine.py` builds and maintains the ladder,
   places paper orders, tracks fills.

Cycles run **hourly** (`MAGI_HOURS_EST = list(range(24))` in `scheduler.py`),
plus startup and manual triggers via `/api/trigger_magi`.

Agents are **stateful Letta Cloud agents** with persistent memory blocks. The
runtime is api.letta.com (not self-hosted). Each agent's memory survives
across cycles, sessions, and droplet restarts.

### The three agents
| agent_id | Display | Model | Role |
|---|---|---|---|
| casper | Casper | google_ai/gemini-3-flash-preview | Regime classifier (RANGING / TRENDING / UNCERTAIN) |
| melchior | Melchior | openai/gpt-4o | Grid microstructure (MAINTAIN / RECENTRE / TIGHTEN / WIDEN) |
| balthasar | Balthasar | anthropic/claude-sonnet-4-6 | Risk / survival (CLEAR / PAUSE_LONGS / PAUSE_SHORTS / HALT) |

The three providers are chosen **by design**, not by accident. Each model has
known biases (Gemini favours structural classification; GPT-4o anchors on
prior responses; Sonnet defaults toward risk-conservative). The council's
strength is that one agent's blind spot is another's signal — the diversity
is the architecture. `CONFLICT_MATRIX` → Round 1 debate is what surfaces
genuine disagreement productively. The goal is NOT to engineer all three into
producing identical outputs. See `CLAUDE.md` §3 for the full framing.

Lowercase `agent_id` is the canonical key everywhere in code, database,
CONFLICT_MATRIX, and column prefixes in debate_records. Capitalized display
names appear only in the Letta dashboard.

### Per-agent memory blocks (7)
- `persona` — full role spec from `/root/xrp_grid/magi/prompts/<agent>_prompt.txt`
- `self_model` — agent-editable scratchpad for self-reflection
- `world_state` — shared, updated by orchestrator each cycle
- `casper_r0_output` / `melchior_r0_output` / `balthasar_r0_output` — shared, each agent's latest Round 0 response
- `cycle_phase` — round_0 or round_1

## Cycle protocol

1. **build_world_state()** (orchestrator.py) assembles indicators, inventory,
   open orders, market knowledge, HARD_RULES → writes to the shared
   `world_state` block.
2. **Round 0 (parallel)** — all three agents receive the cycle prompt and
   return `{position, conviction, key_evidence, crux}` as JSON. Outputs are
   written to the three shared `*_r0_output` blocks so peers can read them
   in Round 1.
3. **detect_conflict()** scans the three R0 positions against CONFLICT_MATRIX
   (e.g. TRENDING+TIGHTEN, WIDEN+PAUSE_LONGS, HALT-high-conv-vs-anything).
4. **Round 1 (only if conflict)** — each agent reads peers' R0 outputs from the
   shared blocks and either holds or revises. `validate_revision()` heuristically
   checks revisions cite new evidence not present in the agent's own R0.
5. **resolve_consensus()** — if any held → most conservative wins; if all
   capitulated → deadlock → most conservative position + human alert.
6. **enforce_hard_rules()** — Python clamps the consensus against HARD_RULES
   (max spacing, min buffers, HALT file, etc.) before the grid engine sees it.

## The learning loop (outcome backfill)

`observer.py` runs `backfill_outcomes()` each poll cycle. For every completed
cycle, it computes fills + P&L at 1h / 6h / 24h windows and writes them to
`debate_records`. At the 6h backfill, it sends an "Outcome for cycle X..."
user-role message to each agent's persistent Letta thread. Over time the
agents accumulate experience inside their persistent context.

## Hard rules (enforced in Python, not by agents)

Thresholds (in `magi/orchestrator.HARD_RULES`):
- `max_allocation_skew`: 0.85
- `min_usd_buffer`: $10
- `min_xrp_buffer_usd`: $10
- `daily_loss_limit_pct`: 0.15
- `halt_file`: `/root/xrp_grid/HALT`
- `max_grid_spacing_pct`: 0.025
- `min_grid_spacing_pct`: 0.003

Override tags applied inside `enforce_hard_rules` (emitted in cycle notes
and stored in `debate_records.hard_rule_overrides` as a JSON-encoded list):
- `[KILL_SWITCH]` — HALT file present
- `[DAILY_LOSS_LIMIT]` — daily PnL below the limit
- `[ALLOC_SKEW_CEILING]` — |skew| > 0.85
- `[USD_BUFFER_FLOOR]` — usd_held < $10 → upgrade CLEAR to PAUSE_LONGS
- `[XRP_BUFFER_FLOOR]` — xrp_value_usd < $10 → upgrade CLEAR to PAUSE_SHORTS
- `[GRID_DEGENERATE]` — buy_count=0 OR sell_count=0 OR (hours_since_last_fill > 24 AND last rebuild > 4h ago) → force RECENTRE + CLEAR
- `[RECENTRE_COOLDOWN]` — council voted RECENTRE within 1h of a fresh healthy rebuild (≥3 buys, ≥2 sells) → downgrade to MAINTAIN + CLEAR
- `[PAUSE_INVALID]` — Balthasar voted PAUSE_X on a thin / balanced book that does not actually warrant a pause → downgrade to CLEAR
- `[GUARDRAILS_BLOCKED]` — pre-cycle `check_all_guardrails` failed; HALT

## Data layout

- **observer.db** SQLite — canonical tables:
  - `debate_records` (one row per cycle, including
    `hard_rule_overrides` JSON column, `balthasar_concerns` /
    `casper_concerns` for schema symmetry, and `outcome_{1,6,24}h_backfilled`
    flags)
  - `agent_registry` (logical agent ↔ Letta UUID)
  - `grid_state`, `grid_orders`, `inventory`, `indicators`, `candles`,
    `market_knowledge`, `letta_status`, `pnl_daily`, `token_usage`
  - Legacy `magi_decisions` — **dual-written** for back-compat readers
    (`learning.py`, `extract_test_cases.py`, two dashboard panels not yet
    migrated). `debate_records` is canonical; do not introduce new
    `magi_decisions` readers.
- **Letta Cloud** (api.letta.com) — agent state, memory blocks, message threads.
  Authenticated via `LETTA_API_KEY` in `/root/xrp_grid/.env`. LLM config
  knobs synced via `magi/provision_agents.AGENT_CONFIG`.

## Services
| Service | Expected state |
|---|---|
| `magi.service` | active (scheduler, observer, MAGI cycles) |
| `magi-dashboard.service` | active (Flask :5000, exposed via nginx as api.ethobs.uk) |
| `letta.service` | inactive + disabled (self-hosted Docker, dormant for rollback only) |

Restart together: `systemctl restart magi.service magi-dashboard.service`.

## Out of scope / dead code
- Self-hosted Letta Docker (dormant; `/root/xrp_grid/letta/` and pgdata preserved for rollback)
- Old stateless `apply_consensus()` three-agent orchestrator (replaced)
- Supervisor / override authority concept (rejected; removed from dashboard)
- Mem0, Graphiti, persistent thread-only approaches (rejected — Letta Cloud is the runtime)
- ETH futures system (dead — do not reference)
- krakenex, python-kraken-sdk, any third-party Kraken wrapper (banned)

## See also

- `CLAUDE.md` — operating discipline, architecture intent, recurring failure
  patterns (auto-loaded at session start by Claude Code)
- `01_CURRENT_STATE.md` — verified facts, live agent IDs, session change log
- `02_NEXT_BUILD_TASKS.md` — work queue
- `03_INSTRUCTIONS_TO_CLAUDE.md` — tone, workflow, forbidden moves

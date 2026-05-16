# MAGI — XRP Grid Bot — Project Overview

## What this system is

MAGI is a grid-trading bot for XRP/USD on Kraken, currently in paper mode. The
end goal is profitable live trading at meaningful scale. Paper mode is
validation only. Hard constraint: right >50% of the time AND profitable after
fees.

## Architecture

Grid engine (Python) places symmetric buy/sell orders around a centre price on
Kraken. Three LLM agents form a council that runs each cycle (twice daily +
manual triggers + startup) and decides whether to MAINTAIN, TIGHTEN, WIDEN,
RECENTRE, or PAUSE the grid, and whether the risk regime is CLEAR, PAUSE_LONGS,
or HALT.

Agents are **stateful Letta Cloud agents** with persistent memory blocks. The
runtime is api.letta.com (not self-hosted). Each agent's memory survives
across cycles, sessions, and droplet restarts.

### The three agents
| agent_id | Display | Model | Role |
|---|---|---|---|
| casper | Casper | google_ai/gemini-3-flash-preview | Regime classifier (TRENDING/RANGE/SIDEWAYS) |
| melchior | Melchior | openai/gpt-4o | Grid optimizer (spacing/levels) |
| balthasar | Balthasar | anthropic/claude-sonnet-4-6 | Risk steward (skew/buffers/HALT) |

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
- `max_allocation_skew`: 0.85 (HALT)
- `min_usd_buffer`: $10
- `min_xrp_buffer_usd`: $10
- `daily_loss_limit_pct`: 0.15
- `halt_file`: `/root/xrp_grid/HALT`
- `max_grid_spacing_pct`: 0.025
- `min_grid_spacing_pct`: 0.003

## Data layout

- **observer.db** SQLite — new tables: `debate_records` (40-col per-cycle),
  `agent_registry` (logical agent ↔ Letta UUID). Legacy `magi_decisions`
  still dual-written for backward compat.
- **Letta Cloud** (api.letta.com) — agent state, memory blocks, message threads.
  Authenticated via `LETTA_API_KEY` in `/root/xrp_grid/.env`.

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

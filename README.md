# xrp_grid — MAGI

An XRP/USD spot grid-trading bot on Kraken, with a three-agent LLM council
("MAGI") advising structural decisions. **Currently running in PAPER mode**
(since 2026-06-09): real Kraken prices and balances are read, but fills are
simulated into an internal paper ledger — no real orders are placed. A prior
live-money run (2026-05-23 → 2026-05-28) keeps its own separate PnL record.

The goal: a profitable adaptive grid — net-positive PnL after Kraken tier-0
fees (maker 0.25%, taker 0.40%), surviving without manual intervention.

## Architecture — three layers

1. **Council judgment** — `magi/council_v2.py`, a hand-rolled arbiter
   orchestrator (direct vendor-SDK calls, no agent framework). Three stateless
   seats vote each cycle via Pydantic output schemas, in a sequential six-call
   choreography (three openings → Casper+Melchior rebuttal → Balthasar
   synthesis as arbiter):
   - **Casper** (`gemini-2.5-flash`) — regime classification
   - **Melchior** (`deepseek-v4-pro`) — grid economics; emits a verdict
     (THESIS_HOLDS / RECONFIGURE / NO_PROFITABLE_GRID), not an action
   - **Balthasar** (`claude-sonnet-4-6`) — risk/survival; the arbiter
2. **Hard rules** — `magi/orchestrator.py:enforce_hard_rules`. Deterministic
   Python overrides on top of council consensus (cooldowns, buffer floors,
   loss limits, kill switch). Survival rules outrank council judgment.
3. **Execution** — `grid/engine.py`. Builds and maintains the order ladder,
   places/simulates orders, tracks fills. Fixed order size (1.65 XRP);
   fee-positive spacing is owned by the deterministic scorer
   (`magi/spacing_evaluator.py`), not the LLMs.

Cadence is **gate-driven**: one daily clock-floor council call plus wake-class
triggers from the always-on free gate (`magi/gate.py` / `magi/gate_monitor.py`),
with throttles and per-episode guards — not a fixed N-hour schedule.

## Runtime

| Unit | Entrypoint | Role |
|---|---|---|
| `magi.service` | `scheduler.py` | observer polls → gate → council → hard rules → engine |
| `magi-dashboard.service` | `dashboard.py` | Flask dashboard (`:5000`, cookie auth) |
| `warehouse-append.timer` etc. | `tape/warehouse.py`, `tape/backup.py` | hourly history warehouse append + daily GCS backups |

`observer.db` (SQLite, gitignored) is the single persistence layer — candles,
indicators, grid orders, council `debate_records`, alerts. `tape/history.db`
holds ~9.5 years of contiguous 1-minute XRP history. LLM call tracing goes to
Langfuse (`magi/agents/tracing.py`).

## Repo map

- `scheduler.py` / `observer.py` / `dashboard.py` — service entrypoints
- `config.py` / `database.py` / `guardrails.py` — config, SQLite layer, kill-switch checks
- `magi/` — council orchestration (`council_v2.py`, `orchestrator.py`), seats
  (`agents/` — one module + persona per seat, shared schema tools), gate
  (`gate.py`, `gate_monitor.py`), scorer (`spacing_evaluator.py`), readiness,
  notifications, Sentry wiring (`adam.py`), world_state schema contract
- `grid/` — engine, exchange clients (`exchanges/kraken.py` is the live one),
  PnL (live/paper scope-split), forward sim, shadow simulator
- `tape/` — market-tape collection stack (collector currently stood down;
  warehouse timers still run); mirrored to `aye5788/market-tape`
- `optimize/casper/` — offline Casper eval/tuning scaffold (experimental, runs nowhere)

## State & history docs

`CLAUDE.md` plus the five handoff docs at repo root are the operating record:
`00_PROJECT_OVERVIEW.md` (system shape), `01_CURRENT_STATE.md` (authoritative
live-vs-experimental STATE LEDGER at the top), `02_NEXT_BUILD_TASKS.md` (work
queue), `03_INSTRUCTIONS_TO_CLAUDE.md` (workflow rules),
`04_EXPERIMENTAL_IDEAS.md` (exploratory, not adopted). They contain extensive
historical narrative — including a **retired Letta Cloud agent layer**
(decoupled 2026-06-09) and an unused ADK migration. Anything described as
Letta-era, CrewAI, or ADK is historical context, not the current system; the
current decision layer is the hand-rolled `council_v2`.

Deprecated code and one-off experiment artifacts were removed from the repo on
2026-06-10 (preserved off-repo in `xrp_grid_deprecated_2026-06-10.tar.gz` and
in git history). Rollback snapshots live untracked under `archive/`.

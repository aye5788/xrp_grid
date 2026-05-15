# MAGI XRP Grid Bot — Project Overview

**Last architecture review:** 2026-05-14

---

## What is this

An automated XRP/USD spot grid trading bot supervised by three AI agents (the MAGI) and a fourth Supervisor agent (under design). The bot places a ladder of buy and sell limit orders symmetrically around a centre price. When price oscillates, orders fill and the bot earns the spread. The AI agents assess market regime, microstructure, and risk exposure twice daily, then vote on whether the grid should maintain, reconfigure, or halt.

The system is designed to run continuously as a systemd service on a VPS. A ~$70 USD universe (mirroring the actual Kraken account) is ring-fenced within the strategy.

**Current mode:** Kraken paper trading (no real orders placed). The paper layer simulates fills against live price data and tracks a hypothetical inventory, but nothing touches the Kraken order book.

**Primary goal:** Build a system that is right >50% of the time and profitable after fees at meaningful scale with real capital. Paper trading is validation only. The end goal is live trading.

---

## Architecture
scheduler.py — main loop: observer every 60min, MAGI at 9AM + 2PM EST ├── observer.py — pulls candles, computes indicators, writes to SQLite ├── magi/ — three stateless AI agents + orchestrator + consensus │ └── [PLANNED] magi/magi_supervisor.py — Supervisor agent (see §Roadmap) ├── grid/engine.py — grid construction, paper fill simulation, shadow sim │ └── grid/exchanges/ — BaseExchange → CoinbaseExchange | KrakenExchange ├── guardrails.py — kill switch + daily loss limit check (runs before each MAGI cycle) └── dashboard.py — Flask read-only dashboard + trigger endpoints

**Database:** SQLite at `/root/xrp_grid/observer.db`

**Hosting:** DigitalOcean droplet, $6/month. Dashboard externally accessible at `https://api.ethobs.uk`.

**Services:** TWO systemd services must both be restarted after any code change:
- `magi.service` — runs `python3 -m scheduler` (observer, scheduled cycles)
- `magi-dashboard.service` — runs `python3 -m dashboard` (Flask API, manual triggers)

Standard restart: `systemctl restart magi.service magi-dashboard.service`

---

## Exchange abstraction

`grid/exchanges/base.py` defines `BaseExchange`. Two concrete implementations:

- `grid/exchanges/coinbase.py` — `CoinbaseExchange`
- `grid/exchanges/kraken.py` — `KrakenExchange`: Kraken Spot REST API, HMAC-SHA512, hand-rolled. No third-party Kraken SDK — ever.

Active exchange controlled by `EXCHANGE` in `config.py`. BTC candles always come from Coinbase regardless of `EXCHANGE` setting.

---

## The MAGI Council

| Agent | Provider | Model | Role |
|-------|----------|-------|------|
| Melchior | OpenAI | gpt-4o | Grid microstructure — spacing, centering, grid action |
| Balthasar | Anthropic | claude-sonnet-4-6 | Survival guardian — capital adequacy, inventory risk |
| Casper | Google | gemini-2.5-flash | Market regime — RANGING / TRENDING / UNCERTAIN |

All three agents are **stateless**: each call receives current state from the database. No memory of prior decisions is passed.

### Output schemas

**Melchior:** `action` (MAINTAIN / RECENTRE / TIGHTEN / WIDEN), `conviction`, `reasoning`, `concerns`

**Balthasar:** `action` (CLEAR / PAUSE_LONGS / PAUSE_SHORTS / HALT), `conviction`, `reasoning`, `concerns`

**Casper:** `regime` (RANGING / TRENDING / UNCERTAIN), `conviction`, `trend_direction`, `reasoning`, `concerns`

---

## Consensus rules (current — under revision)

Evaluated in `magi/orchestrator.py:apply_consensus()`:

1. **Balthasar HALT** → everything halts. Cannot be overridden.
2. **Casper TRENDING** → `grid_action = MAINTAIN` regardless of Melchior. *(Known issue: this veto is too broad — RECENTRE should be exempt. Fix pending.)*
3. **Casper RANGING or UNCERTAIN** → `grid_action = Melchior's action`.
4. **`risk_action`** is always Balthasar's action, applied independently.

**Known architectural gap:** No component asks whether the collective output serves the trading objective. When PAUSE_LONGS + TRENDING + MAINTAIN combine, the system has no self-recovery path. This is the primary motivator for the Supervisor layer (see §Roadmap).

---

## Roadmap — Supervisor Layer

The system is missing a component whose explicit job is: **is this achieving profitable trading?**

The planned Supervisor agent sits above the three councils and has two functions:

1. **Quality control:** pushes back on circular or disconnected reasoning before it reaches the grid engine. Uses multi-turn dialogue — prior council output injected back into context with a specific challenge. Stateless agents can engage with their own prior output when it's provided explicitly.

2. **Strategic override:** when inaction costs more than the council's stated risks justify, overrides toward RECENTRE, WIDEN, or CLEAR_PAUSE. Cannot override toward greater conservatism.

The Supervisor has **bounded persistent memory** via Mem0 (bolt-on memory layer, not a runtime). The three councils remain stateless. The Supervisor accumulates outcome history — what happened after each override or approval — and uses that to calibrate future decisions.

**Sequencing:** Path 1 fixes deploy immediately (see 02_NEXT_BUILD_TASKS.md). Supervisor v1 builds in parallel in shadow mode — it starts logging decisions from day one without affecting grid behavior. Shadow mode requires no clean baseline. Activate Supervisor after 7 days of shadow decisions, not 14. The downside of a bad override is a recoverable RECENTRE. The cost of waiting is weeks of the system not trading.

---

## Schedule
Every 60 minutes observer cycle: candle fetch → indicator compute → shadow tick → paper fill sim 9 AM EST scheduled MAGI cycle 2 PM EST scheduled MAGI cycle On startup observer cycle → grid init or restore → startup MAGI cycle

---

## Risk model and guardrails

**Spot-only trading.** No margin, no leverage. Grid construction trims the sell ladder to XRP held and buy ladder to USD held. `simulate_fills` rejects any fill that would push inventory negative.

**Allocation-based risk (Balthasar).** `allocation_skew = (xrp_value - target) / total_universe_usd` where target = 50/50 neutral. Range ±1. Thresholds: HALT at ±0.85, PAUSE_LONGS/PAUSE_SHORTS at ±0.6, CLEAR inside ±0.6.

**Kill switch.** `/root/xrp_grid/HALT` file blocks all cycles and cancels orders.

**Daily loss limit.** Trips when total universe value drops >15% from UTC midnight baseline.

**Spacing cap.** `MAX_GRID_SPACING_PCT = 2.5%`. WIDEN decisions are clamped to this ceiling before grid rebuild.

---

## Shadow grid simulator

Six parallel virtual grids (level_count ∈ {6, 8, 10, 12, 14, 16}) run on every price tick. Automatic level-count switching when a better variant clears all four gates: ≥0.10% margin, ≥20 fills, ≥24h history, not already current.

**Known limitation:** Shadow buy levels have never filled since launch (lowest shadow buy ~$1.361, XRP low since launch ~$1.377). Level switching has never fired. Shadow fills are not a current priority — the core trading loop must stabilize first.

---

## P&L tracking

FIFO-matched realized P&L, unrealized P&L mark-to-market, win rate, fees. `pnl_daily` table exists but has no writer — this is a known gap to fix.

---

## Cost tracking

Every agent call logs tokens and estimated cost to `token_usage`. Current 30-day actuals: Balthasar ~$1.25, Melchior ~$0.60, Casper ~$0.12. Total projected ~$1.97/month — within budget.

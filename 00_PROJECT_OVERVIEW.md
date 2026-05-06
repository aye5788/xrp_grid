# MAGI XRP Grid Bot — Project Overview

**Last architecture review:** 2026-05-06

---

## What is this

An automated XRP/USD spot grid trading bot supervised by three AI agents (the MAGI). The bot places a ladder of buy and sell limit orders symmetrically around a centre price. When price oscillates, orders fill and the bot earns the spread. The AI agents assess market regime, microstructure, and risk exposure twice daily, then vote on whether the grid should maintain, reconfigure, or halt.

The system is designed to run continuously as a systemd service on a VPS. A $50 USD budget is ring-fenced within the strategy — other assets on the operator's exchange account are invisible to the bot.

**Current mode:** Kraken paper trading (no real orders placed). The paper layer simulates fills against live price data and tracks a hypothetical inventory, but nothing touches the Kraken order book.

---

## Architecture

```
scheduler.py          — main loop: observer every 60min, MAGI at 9AM + 2PM EST
  ├── observer.py     — pulls candles, computes indicators, writes to SQLite
  ├── magi/           — three stateless AI agents + orchestrator + consensus
  ├── grid/engine.py  — grid construction, paper fill simulation, shadow sim
  │   └── grid/exchanges/  — BaseExchange → CoinbaseExchange | KrakenExchange
  ├── guardrails.py   — kill switch + daily loss limit check (runs before each MAGI cycle)
  └── dashboard.py    — Flask read-only dashboard + trigger endpoints
```

**Database:** SQLite at `/root/xrp_grid/observer.db`

**Hosting:** DigitalOcean droplet, $6/month. Dashboard externally accessible at `https://api.ethobs.uk`.

**Service:** `systemctl status magi.service` — started at boot, logs to `magi.log` and journald.

---

## Exchange abstraction

`grid/exchanges/base.py` defines `BaseExchange` — an abstract interface with methods for price fetch, ticker, order placement, order cancellation, balance query, and candle fetch. Two concrete implementations exist:

- `grid/exchanges/coinbase.py` — `CoinbaseExchange`: Coinbase Advanced Trade REST API
- `grid/exchanges/kraken.py` — `KrakenExchange`: Kraken Spot REST API, HMAC-SHA512 auth, hand-rolled

The active exchange is controlled by `EXCHANGE` in `config.py`:

```python
EXCHANGE = "kraken"   # "coinbase" or "kraken"
```

`GridEngine.__init__` and `observer.get_candles_xrp()` both read this flag at runtime.

**BTC candles always come from Coinbase regardless of `EXCHANGE` setting.** BTC is a cross-asset market context signal for Casper only — it is never traded. `observer.poll_cycle()` calls `get_candles_coinbase("BTC-USD", ...)` directly, bypassing the exchange abstraction.

**SIX_HOUR resampling:** Kraken's OHLC endpoint does not support a 6-hour interval (360 is not a valid value — valid intervals are 1, 5, 15, 30, 60, 240, 1440, 10080, 21600). `KrakenExchange.get_candles("SIX_HOUR", ...)` fetches 1H bars and resamples them into 6H buckets in Python, dropping any trailing incomplete bucket. This maintains the same 6H ROC window that the system was designed for.

---

## The three MAGI agents

| Agent | Provider | Model | Role |
|-------|----------|-------|------|
| Melchior | OpenAI | gpt-4o | Grid microstructure analyst — spacing, centering, grid action |
| Balthasar | Anthropic | claude-sonnet-4-6 | Survival guardian — capital adequacy, inventory risk, risk action |
| Casper | Google | gemini-2.5-flash | Market regime analyst — RANGING / TRENDING / UNCERTAIN |

All three agents are **stateless**: each call receives the current indicators, grid state, and inventory snapshot from the database. No memory of prior decisions is passed to the models.

**Balthasar's context** includes explicit budget fields in addition to inventory: `capital_deployed_usd`, `capital_free_usd`, `total_value_usd`, `drawdown_pct`, and `pct_deployed` (% of ring-fenced budget currently in XRP positions). This lets Balthasar reason about capital adequacy directly rather than inferring it from `inventory_skew` alone.

### Output schemas

**Melchior** returns: `action` (MAINTAIN / RECENTRE / TIGHTEN / WIDEN), `conviction`, `recentre_target`, `spacing_adjustment_pct`, `reasoning`, `concerns`.

**Balthasar** returns: `action` (CLEAR / PAUSE_LONGS / PAUSE_SHORTS / HALT), `conviction`, `reasoning`, `concerns`.

**Casper** returns: `regime` (RANGING / TRENDING / UNCERTAIN), `conviction`, `trend_direction`, `adx_reading`, `btc_context`, `reasoning`, `concerns`.

---

## Consensus rules

Evaluated in `magi/orchestrator.py:apply_consensus()`:

1. **Balthasar HALT** → `grid_action = HALT`, `risk_action = HALT`. All grid activity suspended. No other agent can override this.
2. **Casper TRENDING** → `grid_action = MAINTAIN` regardless of Melchior's recommendation. Grid structure is locked when price is trending (accumulating into a trend is the primary grid failure mode).
3. **Casper RANGING or UNCERTAIN** → `grid_action = Melchior's action`. Melchior's structural recommendation is applied.
4. **`risk_action`** is always Balthasar's action (CLEAR / PAUSE_LONGS / PAUSE_SHORTS), applied independently of `grid_action`.

---

## Schedule

```
Every 60 minutes   observer cycle: candle fetch → indicator compute → shadow tick → paper fill sim
9 AM EST           scheduled MAGI cycle
2 PM EST           scheduled MAGI cycle
5 PM EST           learning/summary cycle (manual trigger only — not yet automated)
On startup         observer cycle → grid init or restore → startup MAGI cycle
```

MAGI cycles can also be triggered manually via dashboard button or `POST /api/trigger_magi`.

---

## Risk model and guardrails

**Spot-only trading:** The bot trades spot XRP/USD with no margin and no leverage. It can never sell more XRP than it currently holds. Grid construction (`build_grid_levels` in `grid/engine.py`) trims the sell ladder to what current XRP holdings can cover, and `simulate_fills` rejects any fill that would push inventory negative. As paper inventory accumulates from buys, more sell levels become coverable; as sells fill, fewer do. The grid naturally adjusts capacity to holdings each time it rebuilds.

**Ring-fenced budget:** `MAX_INVENTORY_USD = $50`. This constant has two roles: (1) it determines per-level order size via `compute_order_size` (size = MAX_INVENTORY_USD / (level_count // 2) / price), and (2) it anchors Balthasar's drawdown and deployment-percentage calculations. It is NOT the simulated starting cash. Paper inventory is initialized from the actual live Kraken account balances on startup — `load_state` queries `exchange.get_balances()` if the stored inventory is impossible (negative), and on a clean first deployment the engine starts with whatever XRP and USD are actually held on Kraken. Fund detection at startup verifies XXRP + ZUSD value ≥ $50; refuses to start otherwise.

**Kill switch:** If `/root/xrp_grid/HALT` file exists, all MAGI cycles are blocked and open orders are cancelled.

**Daily loss limit:** If net realized P&L drops below `-$10` in a calendar day, MAGI cycles are blocked and orders are cancelled.

**Balthasar** enforces private thresholds on capital adequacy, drawdown, and inventory concentration. It can independently escalate to PAUSE_LONGS, PAUSE_SHORTS, or HALT based on portfolio state, independent of Melchior and Casper.

---

## Shadow grid simulator

Six parallel virtual grids run on every price tick, with `level_count` ∈ {6, 8, 10, 12, 14, 16}. Each tracks hypothetical fills and rolling P&L%. During each MAGI cycle, the engine evaluates all six variants. If a non-current variant outperforms the live level count by ≥0.10% margin, has ≥20 fills, and both variants have ≥24h of history, the engine switches to the better-performing level count and rebuilds the grid.

**Known limitation:** The shadow simulator's internal fill accounting does not enforce spot inventory constraints — it operates on an idealized model where XRP supply is unlimited. Shadow P&L numbers are directional signal only. See `01_CURRENT_STATE.md` for details.

---

## P&L tracking

The `grid/pnl.py` engine uses FIFO matching of fills to compute realized P&L per trade, unrealized P&L on open inventory (mark-to-market), win rate, and total fees paid. Results feed the dashboard.

---

## Cost tracking

Every MAGI agent call logs prompt tokens, completion tokens, total tokens, and estimated cost to the `token_usage` table. The dashboard surfaces daily cost and 30-day running totals per agent.

Current model pricing (per million tokens):
- gpt-4o: $2.50 input / $10.00 output
- claude-sonnet-4-6: $3.00 input / $15.00 output
- gemini-2.5-flash: $0.30 input / $2.50 output

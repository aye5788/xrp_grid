import os
import time
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import ta
from dotenv import load_dotenv

from database import init_db, insert_candle, get_candles, upsert_indicators
from config import COINBASE_API_KEY, COINBASE_API_SECRET, SYMBOL, DB_PATH

# Load /root/xrp_grid/.env (API keys, Sentry DSN, dashboard secrets).
load_dotenv()
from magi import adam
adam.init("observer")

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s — %(message)s')
log = logging.getLogger('observer')

COINBASE_REST = "https://api.coinbase.com/api/v3/brokerage"

# Lazy-initialised exchange instance for XRP candle fetches when EXCHANGE != "coinbase"
_xrp_exchange = None


def get_candles_coinbase(product_id, granularity, limit=300):
    """Fetch OHLCV candles from Coinbase Advanced REST API."""
    url = f"{COINBASE_REST}/market/products/{product_id}/candles"
    params = {"granularity": granularity, "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("candles", [])
        candles = []
        for c in data:
            candles.append({
                "timestamp": datetime.fromtimestamp(int(c["start"]), tz=timezone.utc).isoformat(),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c["volume"])
            })
        return sorted(candles, key=lambda x: x["timestamp"])
    except Exception as e:
        log.error(f"Candle fetch error {product_id}: {e}")
        return []


def get_candles_xrp(granularity: str, limit: int = 300) -> list:
    """Fetch XRP candles from whichever exchange is configured.

    BTC candles always stay on Coinbase — BTC is a market-context signal only.
    """
    global _xrp_exchange
    from config import EXCHANGE
    if EXCHANGE == "coinbase":
        return get_candles_coinbase("XRP-USD", granularity, limit)
    elif EXCHANGE == "kraken":
        if _xrp_exchange is None:
            from grid.exchanges.kraken import KrakenExchange
            _xrp_exchange = KrakenExchange(symbol="XRP-USD")
        return _xrp_exchange.get_candles(granularity, limit)
    else:
        log.error(f"Unknown EXCHANGE for XRP candles: {EXCHANGE}")
        return []


def compute_indicators(candles_1h, candles_6h, candles_1d, btc_candles_1d):
    """Compute all technical indicators from candle data."""
    if len(candles_1h) < 50:
        log.warning("Insufficient 1h candles for indicators")
        return None

    df1h = pd.DataFrame(candles_1h)
    df6h = pd.DataFrame(candles_6h) if candles_6h else pd.DataFrame()
    df1d = pd.DataFrame(candles_1d) if candles_1d else pd.DataFrame()
    dfbtc = pd.DataFrame(btc_candles_1d) if btc_candles_1d else pd.DataFrame()

    for df in [df1h, df6h, df1d, dfbtc]:
        if not df.empty:
            df['close'] = pd.to_numeric(df['close'])
            df['high'] = pd.to_numeric(df['high'])
            df['low'] = pd.to_numeric(df['low'])
            df['volume'] = pd.to_numeric(df['volume'])

    result = {"timestamp": df1h.iloc[-1]["timestamp"], "timeframe": "1h"}

    # Initialise all indicator keys to None so upsert always writes every column,
    # preventing stale values from persisting when a computation fails.
    result['vwap'] = None
    result['vwap_dev_pct'] = None
    result['atr'] = None
    result['atr_percentile'] = None
    result['vol_regime'] = None
    result['autocorr_1h'] = None
    result['autocorr_4h'] = None
    result['ema_50'] = None
    result['ema_200'] = None
    result['adx'] = None
    result['adx_pos'] = None
    result['adx_neg'] = None
    result['roc_6h'] = None
    result['bb_width'] = None
    result['bb_upper'] = None
    result['bb_lower'] = None
    result['btc_ema_50'] = None
    result['btc_ema_200'] = None

    # VWAP (1h data, rolling 24 periods)
    try:
        typical = (df1h['high'] + df1h['low'] + df1h['close']) / 3
        vwap = (typical * df1h['volume']).rolling(24).sum() / df1h['volume'].rolling(24).sum()
        result['vwap'] = round(float(vwap.iloc[-1]), 6)
        result['vwap_dev_pct'] = round(
            (df1h['close'].iloc[-1] - vwap.iloc[-1]) / vwap.iloc[-1] * 100, 4)
    except Exception as e:
        log.warning(f"VWAP error: {e}")
        result['vwap'] = None
        result['vwap_dev_pct'] = None

    # ATR and vol regime (1h)
    try:
        atr = ta.volatility.AverageTrueRange(df1h['high'], df1h['low'], df1h['close'], window=14)
        atr_series = atr.average_true_range()
        result['atr'] = round(float(atr_series.iloc[-1]), 6)
        pct = atr_series.rank(pct=True).iloc[-1] * 100
        result['atr_percentile'] = round(float(pct), 2)
        from config import VOL_REGIME_LOW_PCT, VOL_REGIME_HIGH_PCT
        if pct < VOL_REGIME_LOW_PCT:
            result['vol_regime'] = 'LOW'
        elif pct > VOL_REGIME_HIGH_PCT:
            result['vol_regime'] = 'HIGH'
        else:
            result['vol_regime'] = 'MEDIUM'
    except Exception as e:
        log.warning(f"ATR error: {e}")
        result['atr'] = None
        result['atr_percentile'] = None
        result['vol_regime'] = None

    # Autocorrelation (1h returns)
    try:
        returns = df1h['close'].pct_change().dropna()
        result['autocorr_1h'] = round(float(returns.autocorr(lag=1)), 4)
        result['autocorr_4h'] = round(float(returns.autocorr(lag=4)), 4)
    except Exception as e:
        log.warning(f"Autocorr error: {e}")
        result['autocorr_1h'] = None
        result['autocorr_4h'] = None

    # EMA 50/200 daily
    if len(df1d) >= 50:
        try:
            result['ema_50'] = round(float(ta.trend.EMAIndicator(df1d['close'], window=50).ema_indicator().iloc[-1]), 6)
            result['ema_200'] = round(float(ta.trend.EMAIndicator(df1d['close'], window=200).ema_indicator().iloc[-1]), 6) if len(df1d) >= 200 else None
        except Exception as e:
            log.warning(f"EMA error: {e}")
            result['ema_50'] = None
            result['ema_200'] = None

    # ADX daily
    if len(df1d) >= 14:
        try:
            adx = ta.trend.ADXIndicator(df1d['high'], df1d['low'], df1d['close'], window=14)
            result['adx'] = round(float(adx.adx().iloc[-1]), 4)
            result['adx_pos'] = round(float(adx.adx_pos().iloc[-1]), 4)
            result['adx_neg'] = round(float(adx.adx_neg().iloc[-1]), 4)
        except Exception as e:
            log.warning(f"ADX error: {e}")
            result['adx'] = None
            result['adx_pos'] = None
            result['adx_neg'] = None

    # ROC 6h
    if len(df6h) >= 6:
        try:
            result['roc_6h'] = round(float(ta.momentum.ROCIndicator(df6h['close'], window=6).roc().iloc[-1]), 4)
        except Exception as e:
            log.warning(f"ROC error: {e}")
            result['roc_6h'] = None

    # Bollinger Band Width daily
    if len(df1d) >= 20:
        try:
            bb = ta.volatility.BollingerBands(df1d['close'], window=20, window_dev=2)
            result['bb_width'] = round(float(bb.bollinger_wband().iloc[-1]), 6)
            result['bb_upper'] = round(float(bb.bollinger_hband().iloc[-1]), 6)
            result['bb_lower'] = round(float(bb.bollinger_lband().iloc[-1]), 6)
        except Exception as e:
            log.warning(f"BB error: {e}")
            result['bb_width'] = None
            result['bb_upper'] = None
            result['bb_lower'] = None

    # BTC EMA context — always from Coinbase regardless of EXCHANGE setting
    if len(dfbtc) >= 50:
        try:
            result['btc_ema_50'] = round(float(ta.trend.EMAIndicator(dfbtc['close'], window=50).ema_indicator().iloc[-1]), 2)
            result['btc_ema_200'] = round(float(ta.trend.EMAIndicator(dfbtc['close'], window=200).ema_indicator().iloc[-1]), 2) if len(dfbtc) >= 200 else None
        except Exception as e:
            log.warning(f"BTC EMA error: {e}")
            result['btc_ema_50'] = None
            result['btc_ema_200'] = None

    return result

# --- Phase 5: outcome backfill for debate_records ---

WINDOW_HOURS = {"1h": 1, "6h": 6, "24h": 24}

def _parse_iso_safe(ts):
    """Parse ISO timestamp from DB. Returns naive UTC datetime, or None."""
    if not ts:
        return None
    try:
        s = ts.replace('Z', '+00:00') if ts.endswith('Z') else ts
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception as e:
        log.warning(f"Could not parse timestamp {ts!r}: {e}")
        return None


def _compute_window_metrics(cycle_start, cycle_end, baseline_equity=None):
    """
    Return (fills_count, realized_pnl, unrealized_pnl) for the window
    [cycle_start, cycle_end).

    SCOPE — decided per cycle from ITS OWN timestamp, mirroring the
    grid/pnl.py:get_pnl_snapshot scope-split (2026-06-09): cycles that start
    at/after the `paper_run_started_utc` system_state cutoff count PAPER fills
    (non-txid order_ids filled at/after the cutoff); earlier cycles keep the
    LIVE-only txid basis, so the May live record is untouched. Era-by-timestamp
    is deterministic — correct even when this backfill runs long after the
    cycle, and repair-safe (re-backfilling an old row re-derives the same
    scope). The 2026-06-09→06-10 paper cycles were originally written with the
    live-only filter applied unconditionally, recording 0 fills for a grid that
    was actually filling — that poisoned the seat graders and the Journal
    recall; do not regress this to a single unconditional filter. NOTE: if the
    bot ever flips back to LIVE, blank `paper_run_started_utc` (or record a
    successor marker and update this scope rule), or post-flip cycles would be
    misclassified as paper.

    realized_pnl: FIFO-matched across the full in-scope fills history (so sells
    in window can match buys from before the window), then summed only for the
    sells whose fill time fell within the window. Out-of-scope fills are
    excluded from the FIFO queue — otherwise cross-scope sells drain the buy
    queue and every in-scope sell attributes to $0.

    unrealized_pnl: the windowed mark-to-market drift on the held position,
    computed as get_pnl_snapshot's decomposition (total = realized + unrealized,
    grid/pnl.py:147-148) restricted to this window, so realized + unrealized
    equals the full windowed equity change:
        window_total = equity_at(cycle_end) - baseline_equity   (decision-time)
        unrealized   = window_total - realized
    where equity = xrp_held*marked_price + usd_held (inventory.net_position_usd
    already stores xrp_held*price, so equity = net_position_usd + usd_held).

    Same strict in-scope basis as realized: unrealized is attributed ONLY when
    the window saw at least one in-scope fill (fills_count > 0). It is also 0.0
    when the decision-time baseline (baseline_equity is None) or the window-end
    equity is unrecoverable — the same no-baseline fallback get_pnl_snapshot
    uses at grid/pnl.py:149-153. (Trade-off: a fully quiescent window — held
    position bleeding with zero fills, e.g. during a HALT — reports 0.0 here,
    because there is no live/paper flag on inventory rows to gate on instead;
    the only scope discriminator in the system is order-id shape + the paper
    cutoff on fills.)
    """
    from database import get_conn, get_system_state
    from grid.pnl import _fifo_match, _is_live_order_id

    # Era of THIS cycle decides the scope (see docstring). ISO-string compare —
    # the same convention get_pnl_snapshot's paper filter uses.
    cutoff = get_system_state('paper_run_started_utc', default='') or ''
    paper_scope = bool(cutoff) and cycle_start.isoformat() >= cutoff

    conn = get_conn()
    rows = conn.execute('''
        SELECT order_id, side, price, size, fill_price, fee, filled_at, timestamp
        FROM grid_orders
        WHERE status='filled'
        ORDER BY COALESCE(filled_at, timestamp) ASC
    ''').fetchall()
    conn.close()

    fills = []
    for r in rows:
        f = dict(r)
        ft = f.get('filled_at') or f.get('timestamp')
        if paper_scope:
            # Paper era: only paper fills (non-txid) at/after the cutoff —
            # identical to get_pnl_snapshot(paper=True).
            if _is_live_order_id(f.get('order_id')) or (ft or '') < cutoff:
                continue
        else:
            # Live era: only Kraken txid fills.
            if not _is_live_order_id(f.get('order_id')):
                continue
        f['_dt'] = _parse_iso_safe(ft)
        if f['_dt'] is None:
            continue
        fills.append(f)

    in_window = [f for f in fills if cycle_start <= f['_dt'] < cycle_end]
    fills_count = len(in_window)

    if not fills:
        return 0, 0.0, 0.0

    matched, _unmatched = _fifo_match(fills)
    pnl_per_sell = {}
    for t in matched:
        pnl_per_sell[t['sell_id']] = pnl_per_sell.get(t['sell_id'], 0.0) + t['contribution']

    realized = 0.0
    for f in in_window:
        if f['side'] == 'sell':
            realized += pnl_per_sell.get(f['order_id'], 0.0)
    realized = round(realized, 4)

    # Unrealized — same in-scope basis as realized. Attributed only when the
    # window saw in-scope trading (fills_count > 0); 0.0 otherwise.
    unrealized = 0.0
    if fills_count > 0 and baseline_equity is not None:
        equity_end = _get_equity_at_or_before(cycle_end)
        if equity_end is not None:
            unrealized = round((equity_end - baseline_equity) - realized, 4)
        # equity_end unrecoverable -> stays 0.0 (no-baseline fallback, grid/pnl.py:149-153)

    return fills_count, realized, unrealized


def _get_skew_at_or_before(timestamp_dt):
    """Most recent inventory.inventory_skew row with timestamp <= given dt.
    Returns float or None. Inventory timestamps are written naive UTC ISO,
    matching cycle timestamps — lexicographic compare works correctly."""
    if timestamp_dt is None:
        return None
    from database import get_conn
    iso = (timestamp_dt.replace(tzinfo=None) if timestamp_dt.tzinfo
           else timestamp_dt).isoformat()
    conn = get_conn()
    row = conn.execute(
        "SELECT inventory_skew FROM inventory WHERE timestamp <= ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (iso,)
    ).fetchone()
    conn.close()
    if row and row['inventory_skew'] is not None:
        return float(row['inventory_skew'])
    return None


def _get_equity_at_or_before(timestamp_dt):
    """Account equity (net_position_usd + usd_held) from the most recent
    inventory row with timestamp <= given dt. net_position_usd stores
    xrp_value_usd (xrp_held * marked price) per engine.update_inventory, so this
    is the marked equity at that snapshot — the same equity definition
    get_pnl_snapshot uses (xrp*price + usd). Returns float or None. Same lookup
    pattern as _get_skew_at_or_before (lexicographic compare on naive UTC ISO)."""
    if timestamp_dt is None:
        return None
    from database import get_conn
    iso = (timestamp_dt.replace(tzinfo=None) if timestamp_dt.tzinfo
           else timestamp_dt).isoformat()
    conn = get_conn()
    row = conn.execute(
        "SELECT net_position_usd, usd_held FROM inventory WHERE timestamp <= ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (iso,)
    ).fetchone()
    conn.close()
    if row and row['net_position_usd'] is not None and row['usd_held'] is not None:
        return float(row['net_position_usd']) + float(row['usd_held'])
    return None


def _decision_baseline_equity(cycle_id):
    """Decision-time account equity recovered from the row's stored world_state
    snapshot (the flight-recorder JSON in debate_records.world_state). Returns
    float or None.

    equity = portfolio.xrp_value_usd + inventory.usd_held — the same
    xrp-marked-to-market + usd equity get_pnl_snapshot uses. Falls back to
    portfolio.total_universe_usd, then to inventory.xrp_held * price + usd_held.
    Returns None when the snapshot lacks the fields; the caller treats None as
    the no-baseline fallback (unrealized -> 0.0, mirroring grid/pnl.py:149-153)."""
    import json
    from database import get_conn
    conn = get_conn()
    row = conn.execute(
        "SELECT world_state FROM debate_records WHERE cycle_id=?", (cycle_id,)
    ).fetchone()
    conn.close()
    if not row or not row['world_state']:
        return None
    try:
        ws = json.loads(row['world_state'])
    except Exception:
        return None
    portfolio = ws.get("portfolio") or {}
    inv = ws.get("inventory") or {}
    xrp_value = portfolio.get("xrp_value_usd")
    usd_held = inv.get("usd_held")
    try:
        if xrp_value is not None and usd_held is not None:
            return float(xrp_value) + float(usd_held)
        tot = portfolio.get("total_universe_usd")
        if tot is not None:
            return float(tot)
        price = ws.get("price")
        xrp_held = inv.get("xrp_held")
        if price is not None and xrp_held is not None and usd_held is not None:
            return float(xrp_held) * float(price) + float(usd_held)
    except (TypeError, ValueError):
        return None
    return None


def _push_outcome_scores(cycle_id: str, window: str) -> bool:
    """Mirror the cycle's matured {window} outcome metrics onto its Langfuse
    trace as scores, so decision quality is monitorable next to cost/latency/
    config in Langfuse (the dashboard was trimmed 2026-06-10; Langfuse is the
    correlation surface). Score values are built from the debate_records row
    itself — the DB is the single source of truth, so a retry pushes exactly
    what was recorded. The 1h push also attaches the hard_rule_overridden
    boolean (known since cycle time; 1h is the first maturity touchpoint).

    Returns a delivery verdict for the convergent sweep (2026-06-11):
    True  -> every score landed (or there is nothing to deliver: pre-tracing
             row with trace_id NULL, missing row) — safe to mark pushed.
    False -> at least one score was dropped (429/outage) — leave the receipt
             unset so push_pending_outcome_scores retries next pass.
    Never raises."""
    try:
        from database import get_conn
        conn = get_conn()
        row = conn.execute(
            "SELECT id, trace_id, hard_rule_overrides, trigger, "
            "       final_grid_action, final_risk_action, "
            "       casper_r0_position, melchior_r0_position, "
            "       balthasar_r0_position, "
            "       casper_r0_conviction, melchior_r0_conviction, "
            "       balthasar_r0_conviction, "
            "       fills_1h, pnl_1h, "
            "       fills_6h, pnl_6h, unrealized_pnl_6h, grid_alive_6h, "
            "       fills_24h, pnl_24h, unrealized_pnl_24h "
            "FROM debate_records WHERE cycle_id=?",
            (cycle_id,),
        ).fetchone()
        if not row or not row['trace_id']:
            conn.close()
            return True
        if window == '6h':
            scores = {
                'fills_6h': row['fills_6h'],
                'pnl_6h': row['pnl_6h'],
                'unrealized_pnl_6h': row['unrealized_pnl_6h'],
                'grid_alive_6h': (bool(row['grid_alive_6h'])
                                  if row['grid_alive_6h'] is not None else None),
            }
        elif window == '24h':
            scores = {
                'fills_24h': row['fills_24h'],
                'pnl_24h': row['pnl_24h'],
                'unrealized_pnl_24h': row['unrealized_pnl_24h'],
            }
        else:  # 1h
            scores = {
                'fills_1h': row['fills_1h'],
                'pnl_1h': row['pnl_1h'],
            }
        if window == '1h':
            import json as _json
            try:
                tags = _json.loads(row['hard_rule_overrides'] or '[]')
            except (ValueError, TypeError):
                tags = []
            scores['hard_rule_overridden'] = bool(tags)
            # Reiteration metrics vs. the immediately-prior cycle — the
            # operator's gate-evaluation question: do triggered calls
            # produce a CHANGED judgment, or rubber-stamp the prior one?
            # judgment_changed compares all four decision outputs;
            # conviction_shift is the mean |delta| of the three seats'
            # R0 convictions (anchoring shows up as ~0.0 shift cycle
            # after cycle). trigger_class makes the slice filterable.
            scores['trigger_class'] = row['trigger'] or 'unknown'
            prior = conn.execute(
                "SELECT final_grid_action, final_risk_action, "
                "       casper_r0_position, melchior_r0_position, "
                "       balthasar_r0_position, "
                "       casper_r0_conviction, melchior_r0_conviction, "
                "       balthasar_r0_conviction "
                "FROM debate_records WHERE id < ? "
                "ORDER BY id DESC LIMIT 1",
                (row['id'],),
            ).fetchone()
            if prior:
                seat_changed = any(
                    (row[k] or '') != (prior[k] or '')
                    for k in ('casper_r0_position', 'melchior_r0_position',
                              'balthasar_r0_position')
                )
                final_changed = seat_changed or any(
                    (row[k] or '') != (prior[k] or '')
                    for k in ('final_grid_action', 'final_risk_action')
                )
                # council_changed isolates the SEATS' own movement;
                # judgment_changed additionally counts rule-forced action
                # changes. council_changed=False on a gate wake = the
                # council reiterated — the anchoring/loose-trigger smell.
                scores['council_changed'] = bool(seat_changed)
                scores['judgment_changed'] = bool(final_changed)
                deltas = [
                    abs((row[k] or 0.0) - (prior[k] or 0.0))
                    for k in ('casper_r0_conviction',
                              'melchior_r0_conviction',
                              'balthasar_r0_conviction')
                ]
                scores['conviction_shift'] = round(sum(deltas) / 3, 4)
        conn.close()
        from magi.agents import tracing
        return tracing.push_trace_scores(row['trace_id'], scores)
    except Exception as e:
        log.warning(f"backfill: score push for {cycle_id} {window} failed: {e}")
        return False


def push_pending_outcome_scores():
    """Convergent Langfuse mirror sweep (2026-06-11). For every window,
    re-attempt the score push for each backfilled-but-unconfirmed cycle and
    set the delivery receipt only when push_trace_scores reports every score
    landed. Replaces the fire-and-forget push at backfill time, which lost
    scores irrecoverably whenever Langfuse 429'd (Hobby tier rate-limits the
    public API at 30 req/min; a burst of maturing windows could silently
    drop most of its pushes — that is exactly how the 2026-06-10 corrected
    re-pushes got eaten).

    Runs every observer pass, right after backfill_outcomes, so a freshly
    matured window still mirrors in the same pass. Capped at 3 cycles per
    window per pass (≤ ~21 POSTs/pass at ~7 scores max for 1h) to stay
    under the rate limit in backlog situations; anything dropped converges
    on later passes. Never raises."""
    from database import get_pending_score_pushes, mark_outcome_scores_pushed
    for window in ('1h', '6h', '24h'):
        try:
            pending = get_pending_score_pushes(window)
        except Exception as e:
            log.error(f"score sweep: get_pending_score_pushes({window}) failed: {e}")
            continue
        for cycle_id in pending[:3]:
            try:
                if _push_outcome_scores(cycle_id, window):
                    mark_outcome_scores_pushed(cycle_id, window)
                    log.info(f"score sweep: {cycle_id} {window} delivered")
                else:
                    log.warning(
                        f"score sweep: {cycle_id} {window} not delivered — "
                        f"will retry next pass"
                    )
            except Exception as e:
                log.error(f"score sweep: {cycle_id} {window} failed: {e}")


def push_daily_effect_scores():
    """Once-per-UTC-day Langfuse mirror of the Fix-1 effect metrics
    (2026-06-11, spacing-floor rebuild): did widening the profit gap actually
    change the economics, measured in money, not activity?

      fee_share_7d   — fees paid / gross spread captured, trailing 7 days.
                       The 9.5y backtest showed 0.75% spacing lost because
                       fees ate ~2/3 of gross; the 6*fee floor targets <=1/3.
                       Healthy <=0.33; >0.40 sustained means the floor is not
                       doing its job. Clamped to 1.0 (fees exceeding all gross
                       is the worst expressible state; the score config is
                       NUMERIC 0-1).
      net_harvest_7d — gross spread captured minus ALL fees paid, trailing
                       7 days, USD. The equity-honest "is the grid earning"
                       number. 0.0 with no fills is a real (and reportable)
                       value, not an error.

    Gross is computed by FIFO-matching the current scope's fills with fees
    zeroed (so each trip's contribution is pure spread capture); fees are
    summed raw over every in-scope fill in the window — including buys that
    have not yet round-tripped, deliberately: fees you paid that produced no
    harvest yet count against you. Scope comes from grid/pnl.py's shared
    helpers (current_scope_cutoff / fill_in_current_scope), NEVER inlined —
    an inlined live-only copy in this file is exactly what poisoned the paper
    run's outcome records (fixed 2026-06-11).

    Delivery is receipt-convergent like push_pending_outcome_scores: the
    system_state key 'effect_scores_pushed_date' is set to today's UTC date
    only when push_trace_scores confirms every POST landed (2xx); until then
    every observer pass retries, so a Langfuse 429/outage delays the mirror
    but never loses it. Scores attach to the most recent council trace.
    Never raises."""
    from database import get_system_state, set_system_state, get_conn
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        if (get_system_state('effect_scores_pushed_date', default='') or '') == today:
            return
        conn = get_conn()
        target = conn.execute(
            "SELECT trace_id FROM debate_records "
            "WHERE trace_id IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not target:
            conn.close()
            return  # no trace exists yet to carry scores; retry next pass
        rows = conn.execute(
            "SELECT order_id, side, price, size, fill_price, fee, filled_at "
            "FROM grid_orders WHERE status='filled' AND filled_at IS NOT NULL "
            "ORDER BY filled_at ASC"
        ).fetchall()
        conn.close()

        from grid.pnl import current_scope_cutoff, fill_in_current_scope, _fifo_match
        cutoff = current_scope_cutoff()
        fills = [dict(r) for r in rows
                 if fill_in_current_scope(r['order_id'], r['filled_at'], cutoff)]
        # filled_at is naive UTC ISO; build the window edge in the same shape
        # so the string comparison is valid.
        window_start = (datetime.now(timezone.utc).replace(tzinfo=None)
                        - timedelta(days=7)).isoformat()

        fees_7d = sum(float(f['fee'] or 0) for f in fills
                      if (f['filled_at'] or '') >= window_start)
        gross_trips, _ = _fifo_match([{**f, 'fee': 0.0} for f in fills])
        sell_time = {f['order_id']: f['filled_at']
                     for f in fills if f['side'] == 'sell'}
        gross_7d = sum(t['contribution'] for t in gross_trips
                       if (sell_time.get(t['sell_id']) or '') >= window_start)

        scores = {'net_harvest_7d': round(gross_7d - fees_7d, 4)}
        if gross_7d > 0:
            scores['fee_share_7d'] = round(min(fees_7d / gross_7d, 1.0), 4)
        elif fees_7d > 0:
            scores['fee_share_7d'] = 1.0  # paid fees, captured nothing
        # else: no fills at all -> fee share undefined, skip the score

        from magi.agents import tracing
        if tracing.push_trace_scores(
                target['trace_id'], scores,
                comment=f"trailing-7d effect metrics ({today})"):
            set_system_state('effect_scores_pushed_date', today)
            log.info(
                f"effect scores: delivered for {today} "
                f"(net_harvest_7d={scores['net_harvest_7d']}, "
                f"fee_share_7d={scores.get('fee_share_7d', 'n/a')})"
            )
        else:
            log.warning("effect scores: delivery incomplete — retry next pass")
    except Exception as e:
        log.error(f"push_daily_effect_scores failed: {e}")


def backfill_outcomes():
    """
    Update debate_records with realised outcomes for the 1h / 6h / 24h
    windows whose timestamps are now mature. (The Letta-era 6h side-write
    to the shared `recent_outcomes` block was removed 2026-06-09, BU-3 —
    the stateless seats never read it; debate_records is the only
    outcome record.) Mirroring to Langfuse is NOT done here anymore —
    push_pending_outcome_scores (called right after this in the observer
    cycle) handles it convergently off the written rows (2026-06-11).
    """
    from database import get_pending_outcome_backfills, update_debate_outcomes

    for window in ('1h', '6h', '24h'):
        try:
            pending = get_pending_outcome_backfills(window)
        except Exception as e:
            log.error(f"backfill: get_pending_outcome_backfills({window}) failed: {e}")
            continue

        if not pending:
            continue

        log.info(f"backfill: {len(pending)} cycle(s) pending {window} backfill")
        hours = WINDOW_HOURS[window]

        for row in pending:
            cycle_id = row['cycle_id']
            cycle_start = _parse_iso_safe(row['timestamp'])
            if cycle_start is None:
                log.warning(f"backfill: bad timestamp for {cycle_id} — skip")
                continue
            cycle_end = cycle_start + timedelta(hours=hours)

            # Decision-time equity baseline from the row's stored world_state.
            # None => unrealized falls back to 0.0 inside _compute_window_metrics
            # (the same no-baseline fallback get_pnl_snapshot uses). Only the
            # 6h/24h windows have an unrealized column, but computing it for all
            # windows is harmless — it's only written for 6h/24h below.
            baseline_equity = _decision_baseline_equity(cycle_id)

            try:
                fills_count, pnl_value, unrealized_value = _compute_window_metrics(
                    cycle_start, cycle_end, baseline_equity
                )
            except Exception as e:
                log.error(f"backfill: metrics for {cycle_id} {window} failed: {e}")
                continue

            try:
                if window == '6h':
                    skew_start = _get_skew_at_or_before(cycle_start)
                    skew_end   = _get_skew_at_or_before(cycle_end)
                    skew_delta = ((skew_end - skew_start)
                                  if (skew_start is not None and skew_end is not None)
                                  else None)
                    grid_alive = 1 if fills_count > 0 else 0
                    update_debate_outcomes(
                        cycle_id, '6h', fills_count, pnl_value,
                        skew_delta=skew_delta, grid_alive=grid_alive,
                        unrealized_pnl=unrealized_value,
                    )
                    log.info(
                        f"backfill: {cycle_id} 6h → fills={fills_count} "
                        f"pnl=${pnl_value:.4f} unrealized=${unrealized_value:.4f} "
                        f"skew_delta={skew_delta} grid_alive={grid_alive}"
                    )
                elif window == '24h':
                    update_debate_outcomes(
                        cycle_id, '24h', fills_count, pnl_value,
                        unrealized_pnl=unrealized_value,
                    )
                    log.info(
                        f"backfill: {cycle_id} 24h → fills={fills_count} "
                        f"pnl=${pnl_value:.4f} unrealized=${unrealized_value:.4f}"
                    )
                else:  # 1h — no unrealized_pnl_1h column; realized only
                    update_debate_outcomes(cycle_id, window, fills_count, pnl_value)
                    log.info(
                        f"backfill: {cycle_id} {window} → fills={fills_count} "
                        f"pnl=${pnl_value:.4f}"
                    )
            except Exception as e:
                log.error(f"backfill: update for {cycle_id} {window} failed: {e}")


def backfill_seat_accuracy_scores():
    """Push per-seat forward-realized correctness onto each cycle's Langfuse
    trace as BOOLEAN scores (casper_correct / melchior_correct /
    balthasar_correct), with the grader's ground-truth one-liner as the
    score comment. Grading delegates to database._grade_*_row — the same
    single-source-of-truth the accuracy panel and the recall Journal use,
    so the three readers can never drift.

    One-touch per cycle: a row is attempted once it is >= 72h old (Casper's
    forward window) and flagged `seat_scores_pushed=1` only when EVERY seat
    resolves — either a grade or a permanent exclusion (overridden /
    non-verdict / missing world_state). Transient exclusions
    (not_matured_72h, missing_outcome) leave the flag unset so the next
    observer pass retries. Excluded seats get no score (an excluded call is
    neither right nor wrong). LIMIT 5 per pass keeps the score POSTs far
    under the Langfuse rate limit.
    """
    from database import (
        get_conn, _grade_casper_row, _grade_melchior_row, _grade_balthasar_row,
    )
    from magi.agents import tracing

    conn = get_conn()
    try:
        candidates = conn.execute(
            "SELECT cycle_id, timestamp, trace_id, "
            "       casper_r0_position, melchior_r0_position, "
            "       balthasar_r0_position, "
            "       fills_6h, pnl_6h, unrealized_pnl_6h, world_state, "
            "       geometry_veto, final_grid_action, final_risk_action, "
            "       hard_rule_overrides "
            "FROM debate_records "
            "WHERE trace_id IS NOT NULL AND seat_scores_pushed=0 "
            "AND timestamp <= datetime('now', '-72 hours') "
            "ORDER BY id LIMIT 5"
        ).fetchall()
        if not candidates:
            return

        from grid.forward_sim import load_1h
        bars = load_1h(conn)
        ts_keys = [b[0][:19] for b in bars]
        n = len(bars)
        TRANSIENT = {'not_matured_72h', 'missing_outcome'}

        for row in candidates:
            r = dict(row)
            seat_rows = {
                'casper': {**r, 'position': r['casper_r0_position']},
                'melchior': {**r, 'position': r['melchior_r0_position']},
                'balthasar': {**r, 'position': r['balthasar_r0_position']},
            }
            graders = {
                'casper': _grade_casper_row,
                'melchior': _grade_melchior_row,
                'balthasar': _grade_balthasar_row,
            }
            grades, all_resolved = {}, True
            for seat, grader in graders.items():
                if seat_rows[seat]['position'] is None:
                    continue  # seat never voted — permanently ungradeable
                grade, reason = grader(seat_rows[seat], bars, ts_keys, n)
                if grade is not None:
                    grades[seat] = grade
                elif reason in TRANSIENT:
                    all_resolved = False
            if not all_resolved:
                continue  # retry next pass once outcomes/bars mature

            for seat, grade in grades.items():
                comment = grade['raw_outcome']
                if grade.get('estimated'):
                    comment += ' (estimated)'
                tracing.push_trace_scores(
                    r['trace_id'], {f'{seat}_correct': bool(grade['correct'])},
                    comment=comment,
                )
            conn.execute(
                "UPDATE debate_records SET seat_scores_pushed=1 "
                "WHERE cycle_id=?", (r['cycle_id'],),
            )
            conn.commit()
            log.info(
                f"seat-accuracy: {r['cycle_id']} → "
                + (", ".join(f"{s}={'OK' if g['correct'] else 'WRONG'}"
                             for s, g in grades.items()) or "no gradeable seats")
            )
    finally:
        conn.close()


def backfill_stance_grades():
    """Grade each cycle's arbiter stance (DEPLOY / HOLD / STAND_ASIDE) against
    the realised 72h forward price path, then mirror the grade to Langfuse.

    Anchored grading rule: the correctness thresholds are derived from the
    grid's own band width — spacing_pct × max(1, levels//2), i.e. the
    fractional distance from centre to the outermost rung. That is an
    exogenous quantity set by fee economics and the level count, NOT a
    threshold fitted to outcome data. A forward 1h close below
    decision_price*(1-band) is a down-break (the grid would have bled through
    its buy rungs); a close above decision_price*(1+band) is an up-run.
    DEPLOY is correct iff no down-break; STAND_ASIDE is correct iff a
    down-break happened; HOLD is correct iff there was a down-break OR no
    up-run (wrong only when price ran up cleanly and standing pat left
    harvest on the table). Missing/zero spacing falls back to band=0.05
    (max spacing 2.5% × 2 pairs). The 72h maturity matches the per-seat
    grading window (backfill_seat_accuracy_scores) so both graders mature
    together. Requires >= 48 forward 1h bars before grading (window not yet
    fully covered → retry next pass). Langfuse delivery is receipt-convergent
    like push_pending_outcome_scores: stance_scores_pushed=1 only when
    push_trace_scores confirms every POST (2xx); NULL-trace rows are stamped
    delivered with nothing to push. Cap 3 pushes per pass.
    """
    import json
    from database import get_conn
    from magi.agents import tracing

    conn = get_conn()
    try:
        # --- Part A: grade matured, ungraded stance rows -------------------
        cutoff = (datetime.utcnow() - timedelta(hours=72)).isoformat()
        rows = conn.execute(
            "SELECT id, cycle_id, timestamp, stance, world_state, trace_id "
            "FROM debate_records "
            "WHERE stance IS NOT NULL AND stance_correct IS NULL "
            "AND timestamp <= ? ORDER BY id", (cutoff,)
        ).fetchall()

        for row in rows:
            r = dict(row)
            try:
                ws = json.loads(r['world_state'])
                decision_price = float(ws['price'])
            except (TypeError, ValueError, KeyError):
                log.warning(
                    f"stance-grade: {r['cycle_id']} — missing/unparseable "
                    f"world_state price; skip (not marked graded)"
                )
                continue

            gs = ws.get('grid_state') or {}
            try:
                spacing = float(gs.get('spacing_pct') or 0)
                levels = int(gs.get('levels') or 0)
            except (TypeError, ValueError):
                spacing, levels = 0.0, 0
            if spacing > 0:
                band = spacing * max(1, levels // 2)
            else:
                band = 0.05  # fallback: max spacing 2.5% × 2 pairs

            start = _parse_iso_safe(r['timestamp'])
            if start is None:
                log.warning(f"stance-grade: bad timestamp for {r['cycle_id']} — skip")
                continue
            end_iso = (start + timedelta(hours=72)).isoformat()
            # candles.timestamp is ISO text like debate_records.timestamp;
            # lexicographic comparison is order-correct at 1h granularity
            # (the date/hour prefix dominates; suffix-format differences
            # like '+00:00' only matter on an exact-second tie, which
            # hour-aligned candles vs microsecond decision stamps never hit).
            closes = [c['close'] for c in conn.execute(
                "SELECT close FROM candles WHERE timeframe='1h' "
                "AND timestamp > ? AND timestamp <= ? ORDER BY timestamp",
                (r['timestamp'], end_iso)
            ).fetchall()]
            if len(closes) < 48:
                continue  # window not fully covered yet; retry next pass

            down_break = min(closes) < decision_price * (1 - band)
            up_run = max(closes) > decision_price * (1 + band)
            stance = r['stance']
            if stance == 'DEPLOY':
                correct = not down_break
            elif stance == 'STAND_ASIDE':
                correct = down_break
            elif stance == 'HOLD':
                correct = down_break or not up_run
            else:
                log.warning(
                    f"stance-grade: {r['cycle_id']} — unknown stance "
                    f"{stance!r}; skip (not marked graded)"
                )
                continue

            conn.execute(
                "UPDATE debate_records SET stance_correct=? WHERE id=?",
                (1 if correct else 0, r['id']),
            )
            conn.commit()
            log.info(
                f"stance-grade: {r['cycle_id']} stance={stance} "
                f"price={decision_price} band={band:.4f} "
                f"down_break={down_break} up_run={up_run} → "
                f"{'OK' if correct else 'WRONG'}"
            )

        # --- Part B: convergent Langfuse delivery of graded rows ------------
        pending = conn.execute(
            "SELECT id, cycle_id, stance, stance_correct, trace_id "
            "FROM debate_records "
            "WHERE stance_correct IS NOT NULL AND stance_scores_pushed=0 "
            "ORDER BY id LIMIT 3"
        ).fetchall()
        for row in pending:
            r = dict(row)
            if not r['trace_id']:
                # No trace to score — nothing to deliver; stamp the receipt
                # so the row never clogs the sweep.
                conn.execute(
                    "UPDATE debate_records SET stance_scores_pushed=1 "
                    "WHERE id=?", (r['id'],),
                )
                conn.commit()
                continue
            if tracing.push_trace_scores(r['trace_id'], {
                'stance': r['stance'],                       # str → CATEGORICAL
                'stance_correct': bool(r['stance_correct']),  # bool → BOOLEAN
            }):
                conn.execute(
                    "UPDATE debate_records SET stance_scores_pushed=1 "
                    "WHERE id=?", (r['id'],),
                )
                conn.commit()
            else:
                log.warning(
                    f"stance-grade: Langfuse push unconfirmed for "
                    f"{r['cycle_id']} — retry next pass"
                )
    finally:
        conn.close()


def _ws_rest_divergence_check(rest_xrp_1h: list,
                              tolerance_pct: float = 0.001) -> None:
    """Compare the most-recent COMPLETED REST 1h candle against the
    candle currently in the DB for the same hour. The DB may hold a
    WS-derived candle written by gate_monitor; if it differs from REST
    by more than `tolerance_pct` (0.1% default) on close price, treat
    REST as ground truth and re-insert. Non-fatal — logs warning only
    when divergence exceeds the tolerance."""
    if not rest_xrp_1h:
        return
    from database import get_conn, insert_candle
    now_h = datetime.utcnow().replace(
        minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    ).isoformat()
    # Most-recent COMPLETED REST candle (skip in-progress hour)
    completed = [c for c in rest_xrp_1h if c.get('timestamp', '') < now_h]
    if not completed:
        return
    latest = max(completed, key=lambda c: c.get('timestamp', ''))
    ts = latest.get('timestamp')
    rest_close = float(latest.get('close') or 0)
    rest_high = float(latest.get('high') or 0)
    rest_low = float(latest.get('low') or 0)
    if rest_close <= 0:
        return
    conn = get_conn()
    row = conn.execute(
        "SELECT close, high, low FROM candles "
        "WHERE timeframe='1h' AND timestamp=? LIMIT 1",
        (ts,)
    ).fetchone()
    conn.close()
    if not row:
        # No matching candle in DB yet — observer-side insert_candle has
        # already happened earlier in this poll, so this branch is
        # unexpected. Skip.
        return
    db_close = float(row['close'] or 0)
    if db_close <= 0:
        return
    diff_pct = abs(rest_close - db_close) / db_close
    if diff_pct > tolerance_pct:
        log.warning(
            "WS/REST divergence at %s: db_close=%.5f rest_close=%.5f "
            "diff=%.4f%% (>%.4f%% tolerance) — overwriting with REST",
            ts, db_close, rest_close, diff_pct * 100, tolerance_pct * 100,
        )
        insert_candle(ts, '1h', float(latest.get('open') or 0),
                      rest_high, rest_low, rest_close,
                      float(latest.get('volume') or 0))


def _resample_6h_from_1h(candles_1h):
    """Bucket 1h candle dicts into 6h OHLC dicts. Fallback for when the exchange
    SIX_HOUR fetch flakes (a separate Kraken OHLC call that intermittently
    returns []), which nulled roc_6h and starved Casper's regime call. The 1h
    bars are already in hand this cycle, so resampling them is free and removes
    the second-fetch failure point. Mirrors KrakenExchange SIX_HOUR bucketing."""
    buckets = {}
    for c in candles_1h or []:
        try:
            dt = datetime.fromisoformat(str(c.get('timestamp')).replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            epoch = int(dt.timestamp())
        except Exception:
            continue
        buckets.setdefault(epoch // (6 * 3600), []).append((epoch, c))
    out = []
    for key in sorted(buckets):
        bars = [c for _, c in sorted(buckets[key], key=lambda x: x[0])]
        out.append({
            'timestamp': datetime.fromtimestamp(key * 6 * 3600, tz=timezone.utc).isoformat(),
            'open':   float(bars[0]['open']),
            'high':   max(float(b['high']) for b in bars),
            'low':    min(float(b['low']) for b in bars),
            'close':  float(bars[-1]['close']),
            'volume': sum(float(b.get('volume') or 0) for b in bars),
        })
    # Drop incomplete trailing bucket (fewer than 6 hourly bars)
    if out and len(buckets[sorted(buckets)[-1]]) < 6:
        out = out[:-1]
    return out


def poll_cycle():
    """One full data collection cycle."""
    log.info("Poll cycle starting")

    # Fetch candles — XRP from configured exchange, BTC always from Coinbase
    xrp_1h = get_candles_xrp("ONE_HOUR", 300)
    xrp_6h = get_candles_xrp("SIX_HOUR", 100)
    if len(xrp_6h) < 7:
        # SIX_HOUR fetch flaked — resample from the 1h bars already fetched so
        # roc_6h stays populated (else Casper's regime call loses its momentum
        # signal). 7 = ROC window (6) + 1.
        resampled = _resample_6h_from_1h(xrp_1h)
        log.warning(
            f"6h candle fetch returned {len(xrp_6h)} bars — "
            f"resampling {len(resampled)} from 1h fallback"
        )
        xrp_6h = resampled
    xrp_1d = get_candles_xrp("ONE_DAY", 300)
    btc_1d = get_candles_coinbase("BTC-USD", "ONE_DAY", 300)

    # Write candles to DB
    for c in xrp_1h:
        insert_candle(c['timestamp'], '1h', c['open'], c['high'], c['low'], c['close'], c['volume'])
    for c in xrp_1d:
        insert_candle(c['timestamp'], '1d', c['open'], c['high'], c['low'], c['close'], c['volume'])

    # Compute and store indicators
    indicators = compute_indicators(xrp_1h, xrp_6h, xrp_1d, btc_1d)
    if indicators:
        ts = indicators.pop('timestamp')
        tf = indicators.pop('timeframe')
        upsert_indicators(ts, tf, indicators)
        log.info(f"Indicators updated — vol_regime={indicators.get('vol_regime')} vwap_dev={indicators.get('vwap_dev_pct')}")
    else:
        log.warning("No indicators computed this cycle")

    # Phase 5: backfill realised outcomes for matured debate_records cycles.
    # Failures here must not break the rest of the poll cycle.
    try:
        backfill_outcomes()
    except Exception as e:
        log.error(f"backfill_outcomes failed: {e}")

    # Convergent Langfuse mirror: deliver any backfilled-but-unconfirmed
    # outcome scores (retries until confirmed; see push_pending_outcome_scores).
    try:
        push_pending_outcome_scores()
    except Exception as e:
        log.error(f"push_pending_outcome_scores failed: {e}")

    # Daily Fix-1 effect metrics (fee_share_7d / net_harvest_7d) onto the
    # latest council trace — receipt-convergent, once per UTC day.
    try:
        push_daily_effect_scores()
    except Exception as e:
        log.error(f"push_daily_effect_scores failed: {e}")

    # Per-seat forward-realized accuracy → Langfuse scores (72h maturity).
    try:
        backfill_seat_accuracy_scores()
    except Exception as e:
        log.error(f"backfill_seat_accuracy_scores failed: {e}")

    # Arbiter stance forward-outcome grading + convergent Langfuse mirror
    # (72h maturity, band-anchored thresholds; see backfill_stance_grades).
    try:
        backfill_stance_grades()
    except Exception as e:
        log.error(f"backfill_stance_grades failed: {e}")

    # Gate evaluation is now driven by magi/gate_monitor.py (Kraken WS v2
    # streaming). Observer's REST poll persists indicators and acts as a
    # safety check against WS-derived candles: compare the latest REST
    # 1h candle against the candle currently in the DB for the same
    # hour; if they diverge meaningfully, trust REST as ground truth.
    try:
        _ws_rest_divergence_check(xrp_1h)
    except Exception as e:
        log.warning(f"ws_rest_divergence_check failed (non-fatal): {e}")

def run_daemon(interval_seconds=3600):
    """Run observer as daemon, polling every interval."""
    init_db()
    log.info(f"Observer daemon starting — poll interval {interval_seconds}s")
    while True:
        try:
            poll_cycle()
        except Exception as e:
            log.error(f"Poll cycle error: {e}")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    init_db()
    poll_cycle()
    log.info("Single poll complete.")

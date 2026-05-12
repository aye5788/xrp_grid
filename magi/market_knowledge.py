import json
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from database import get_conn

log = logging.getLogger('magi.market_knowledge')

# ── Regime detection ────────────────────────────────────────────
# Mirrors the same logic used in observer.py compute_indicators()
# so regime labels are consistent across the system.

def _classify_regime(row) -> str:
    """Classify a single row into one of 4 regimes matching what
    agents can request: bearish_chop, bearish_trend,
    bullish_chop, bullish_trend."""
    try:
        ema_50  = row.get('ema_50')
        ema_200 = row.get('ema_200')
        adx     = row.get('adx')

        if any(v is None for v in [ema_50, ema_200, adx]):
            return 'unknown'

        bearish  = ema_50 < ema_200
        trending = adx > 25

        if bearish and trending:
            return 'bearish_trend'
        elif bearish:
            return 'bearish_chop'
        elif trending:
            return 'bullish_trend'
        else:
            return 'bullish_chop'
    except Exception:
        return 'unknown'


def _compute_regime_stats(df: pd.DataFrame) -> dict:
    """
    Compute forward-return and range stats per regime.
    df must have columns: timestamp, open, high, low, close,
    ema_50, ema_200, adx, roc_6h, atr_percentile
    """
    df = df.copy().sort_values('timestamp').reset_index(drop=True)
    df['regime'] = df.apply(_classify_regime, axis=1)
    df['hourly_range_pct'] = (df['high'] - df['low']) / df['low'] * 100

    # Forward returns
    df['fwd_4h']  = df['close'].shift(-4)   / df['close'] - 1
    df['fwd_24h'] = df['close'].shift(-24)  / df['close'] - 1
    df['fwd_7d']  = df['close'].shift(-168) / df['close'] - 1

    stats = {}
    for regime, grp in df.groupby('regime'):
        if regime == 'unknown' or len(grp) < 20:
            continue
        s = {}
        s['bars'] = len(grp)

        # Forward returns
        for col, label in [('fwd_4h', '4h'), ('fwd_24h', '24h'), ('fwd_7d', '7d')]:
            valid = grp[col].dropna() * 100
            if len(valid) >= 10:
                s[f'fwd_{label}_mean']     = round(valid.mean(), 3)
                s[f'fwd_{label}_win_rate'] = round((valid > 0).mean() * 100, 1)
            else:
                s[f'fwd_{label}_mean']     = None
                s[f'fwd_{label}_win_rate'] = None

        # Hourly range stats (for spacing calibration)
        rng = grp['hourly_range_pct']
        s['range_median'] = round(rng.median(), 3)
        s['range_mean']   = round(rng.mean(), 3)
        s['range_p75']    = round(rng.quantile(0.75), 3)
        s['range_p90']    = round(rng.quantile(0.90), 3)

        # Hit rates at key spacings
        s['hit_rates'] = {}
        for spacing in [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5]:
            s['hit_rates'][str(spacing)] = round(
                (rng >= spacing).mean() * 100, 1
            )

        # Drawdown before recovery (for Balthasar)
        # Sample winners randomly with fixed seed to avoid
        # chronological bias (oldest-first [:500] would sample
        # only 2018-2019 for large regimes like bearish_trend).
        import random as _random
        winners = grp[grp['fwd_24h'] > 0].index.tolist()
        rng_sample = _random.Random(42)
        sampled = rng_sample.sample(winners, min(500, len(winners)))
        drawdowns = []
        for idx in sampled:
            pos = df.index.get_loc(idx)
            window = df['close'].iloc[pos:pos + 24]
            if len(window) >= 2:
                # Use rolling max as reference peak, not just entry
                # price — correctly captures max adverse excursion
                # if price rises before falling.
                running_max = window.expanding().max()
                dd = ((window - running_max) / running_max * 100).min()
                drawdowns.append(float(dd))
        if drawdowns:
            dd_arr = np.array(drawdowns)
            s['drawdown_median']  = round(float(np.median(dd_arr)), 2)
            s['drawdown_gt3_pct'] = round(float((dd_arr < -3).mean() * 100), 1)
            s['drawdown_gt1_pct'] = round(float((dd_arr < -1).mean() * 100), 1)
        else:
            s['drawdown_median']  = None
            s['drawdown_gt3_pct'] = None
            s['drawdown_gt1_pct'] = None

        stats[regime] = s

    return stats


def recompute_stats() -> bool:
    """
    Read full candles table, compute indicators from raw OHLCV,
    classify regimes, compute stats, write to market_knowledge.
    Called daily by scheduler at midnight UTC.
    Returns True on success.
    """
    try:
        conn = get_conn()
        df = pd.read_sql_query(
            """SELECT timestamp, open, high, low, close
               FROM candles
               WHERE timeframe = '1h'
               ORDER BY timestamp ASC""",
            conn
        )
        # Normalize mixed tz-naive (Bitstamp) and tz-aware (Kraken) timestamp
        # strings to a single tz-naive datetime64 column. parse_dates silently
        # converts tz-aware strings to NaT, causing max() to return stale data.
        df['timestamp'] = pd.to_datetime(
            df['timestamp'], format='ISO8601', utc=True
        ).dt.tz_localize(None)
        conn.close()

        if len(df) < 1000:
            log.warning(f"Only {len(df)} candles — skipping recompute")
            return False

        log.info(f"Computing indicators + regime stats on {len(df)} candles...")

        # ── Compute indicators from raw OHLCV ──────────────────────
        # EMA-50 and EMA-200
        df['ema_50']  = df['close'].ewm(span=50,  adjust=False).mean()
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()

        # ATR (14-period)
        df['prev_close'] = df['close'].shift(1)
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                (df['high'] - df['prev_close']).abs(),
                (df['low']  - df['prev_close']).abs()
            )
        )
        df['atr'] = df['tr'].rolling(14).mean()
        df['atr_percentile'] = df['atr'].rank(pct=True) * 100

        # ADX (14-period)
        df['up_move']   = df['high'] - df['high'].shift(1)
        df['down_move'] = df['low'].shift(1) - df['low']
        df['plus_dm']  = np.where(
            (df['up_move'] > df['down_move']) & (df['up_move'] > 0),
            df['up_move'], 0.0
        )
        df['minus_dm'] = np.where(
            (df['down_move'] > df['up_move']) & (df['down_move'] > 0),
            df['down_move'], 0.0
        )
        atr14    = df['tr'].ewm(alpha=1/14, adjust=False).mean()
        plus_di  = 100 * df['plus_dm'].ewm(alpha=1/14, adjust=False).mean() / atr14
        minus_di = 100 * df['minus_dm'].ewm(alpha=1/14, adjust=False).mean() / atr14
        dx = (100 * (plus_di - minus_di).abs() /
              (plus_di + minus_di).replace(0, np.nan))
        df['adx'] = dx.ewm(alpha=1/14, adjust=False).mean()

        # ROC-6h
        df['roc_6h'] = df['close'].pct_change(6) * 100

        # Drop warmup period (need 200 bars for EMA-200)
        df = df.iloc[200:].copy().reset_index(drop=True)

        log.info(f"Indicators computed, {len(df)} bars after warmup")

        stats = _compute_regime_stats(df)

        now = datetime.now(timezone.utc).isoformat()
        conn = get_conn()
        conn.execute(
            """INSERT INTO market_knowledge
               (computed_at, data_from, data_to, total_bars, stats_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                now,
                str(df['timestamp'].dt.tz_localize(None).min()
                    if df['timestamp'].dt.tz is not None
                    else df['timestamp'].min()),
                str(df['timestamp'].dt.tz_localize(None).max()
                    if df['timestamp'].dt.tz is not None
                    else df['timestamp'].max()),
                len(df),
                json.dumps(stats)
            )
        )
        conn.commit()
        conn.close()
        log.info(
            f"market_knowledge updated — {len(stats)} regimes, "
            f"{len(df)} bars"
        )
        return True

    except Exception as e:
        log.error(f"recompute_stats failed: {e}")
        return False


def get_stats_for_casper(current_regime: str) -> str:
    """
    Return a formatted stats block for Casper.
    Focuses on forward returns and regime duration.
    """
    row, stats = _load_latest(current_regime)
    if not row or not stats:
        return ""

    s = stats
    lines = [
        f"HISTORICAL BASE RATES (computed {row['computed_at'][:10]}, "
        f"{row['total_bars']:,} bars, data through {row['data_to'][:10]}):",
        f"Current regime analog: {current_regime} ({s.get('bars', 0)} historical bars)",
    ]
    for horizon, label in [('4h', '4h'), ('24h', '24h'), ('7d', '7d')]:
        mean = s.get(f'fwd_{label}_mean')
        wr   = s.get(f'fwd_{label}_win_rate')
        if mean is not None:
            lines.append(
                f"  Forward {horizon}: mean {mean:+.2f}%, "
                f"win rate {wr:.1f}%"
            )
    lines.append(
        "This data is reference context. It does NOT override your "
        "regime call — use it to calibrate conviction only."
    )
    return '\n'.join(lines)


def get_stats_for_melchior(current_regime: str) -> str:
    """
    Return a formatted stats block for Melchior.
    Focuses on hourly range distribution and spacing hit rates.
    """
    row, stats = _load_latest(current_regime)
    if not row or not stats:
        return ""

    s = stats
    lines = [
        f"HISTORICAL BASE RATES (computed {row['computed_at'][:10]}, "
        f"{row['total_bars']:,} bars):",
        f"Current regime: {current_regime} ({s.get('bars', 0)} bars)",
        f"Median hourly range: {s.get('range_median', 'N/A')}% "
        f"(mean: {s.get('range_mean', 'N/A')}%)",
        "Hit rates by spacing (% of hours where a fill could trigger):",
    ]
    hit_rates = s.get('hit_rates', {})
    for spacing, rate in sorted(hit_rates.items(), key=lambda x: float(x[0])):
        fee_drag = round(0.5 / float(spacing) * 100, 0)
        lines.append(
            f"  {spacing}%: {rate}% of hours  "
            f"(fee drag at 0.25% maker: {fee_drag:.0f}% of gross profit)"
        )
    lines.append(
        "Use these rates to evaluate WIDEN/TIGHTEN. "
        "Spacing below 0.5% is fee-negative at current maker rate."
    )
    return '\n'.join(lines)


def get_stats_for_balthasar(current_regime: str) -> str:
    """
    Return a formatted stats block for Balthasar.
    Focuses on drawdown-before-recovery distribution.
    """
    row, stats = _load_latest(current_regime)
    if not row or not stats:
        return ""

    s = stats
    lines = [
        f"HISTORICAL BASE RATES (computed {row['computed_at'][:10]}, "
        f"{row['total_bars']:,} bars):",
        f"Current regime: {current_regime} ({s.get('bars', 0)} bars)",
    ]
    wr_24h = s.get('fwd_24h_win_rate')
    if wr_24h:
        lines.append(f"24h forward win rate: {wr_24h:.1f}%")
    dd_med  = s.get('drawdown_median')
    dd_gt3  = s.get('drawdown_gt3_pct')
    dd_gt1  = s.get('drawdown_gt1_pct')
    if dd_med is not None:
        lines.append(
            f"Among eventual 24h winners: median max drawdown "
            f"before recovery = {dd_med:.2f}%"
        )
        lines.append(
            f"  {dd_gt3:.1f}% drew down >3% before recovering; "
            f"{dd_gt1:.1f}% drew down >1%"
        )
    lines.append(
        "This data does NOT override Part A mechanical rules. "
        "Use as context only."
    )
    return '\n'.join(lines)


def _load_latest(current_regime: str):
    """Load latest market_knowledge row and extract regime stats."""
    try:
        conn = get_conn()
        row = conn.execute(
            """SELECT computed_at, data_from, data_to, total_bars, stats_json
               FROM market_knowledge
               ORDER BY computed_at DESC LIMIT 1"""
        ).fetchone()
        conn.close()
        if not row:
            return None, None
        all_stats = json.loads(row['stats_json'])
        regime_stats = all_stats.get(current_regime)
        if not regime_stats:
            # Fall back to nearest regime
            regime_stats = all_stats.get('bearish_chop') or \
                           next(iter(all_stats.values()), None)
        return dict(row), regime_stats
    except Exception as e:
        log.warning(f"_load_latest failed: {e}")
        return None, None

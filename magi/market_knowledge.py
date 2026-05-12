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
    """Classify a single candle row into a regime label."""
    try:
        ema_50  = row.get('ema_50')
        ema_200 = row.get('ema_200')
        adx     = row.get('adx')
        roc_6h  = row.get('roc_6h')
        atr_pct = row.get('atr_percentile')

        if any(v is None for v in [ema_50, ema_200, adx]):
            return 'unknown'

        bearish = ema_50 < ema_200
        high_vol = atr_pct is not None and atr_pct > 75
        low_vol  = atr_pct is not None and atr_pct < 25
        trending = adx > 25

        if bearish and not trending and not high_vol:
            return 'bearish_chop'
        elif bearish and trending:
            return 'bearish_trend'
        elif bearish and high_vol:
            return 'bearish_high_vol'
        elif not bearish and not trending and not high_vol:
            return 'bullish_chop'
        elif not bearish and trending:
            return 'bullish_trend'
        elif not bearish and high_vol:
            return 'bullish_high_vol'
        else:
            return 'neutral'
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
        # Find bars where fwd_24h > 0, measure max drawdown in that window
        winners = grp[grp['fwd_24h'] > 0].index.tolist()
        drawdowns = []
        for idx in winners[:500]:  # cap for performance
            pos = df.index.get_loc(idx)
            window = df['close'].iloc[pos:pos + 24]
            if len(window) >= 2:
                peak = window.iloc[0]
                trough = window.min()
                dd = (trough - peak) / peak * 100
                drawdowns.append(dd)
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
    Read full candles + indicators tables, compute regime stats,
    write to market_knowledge table.
    Called daily by scheduler at midnight UTC.
    Returns True on success.
    """
    try:
        conn = get_conn()

        # Join candles with indicators on timestamp
        df = pd.read_sql_query(
            """
            SELECT c.timestamp, c.open, c.high, c.low, c.close,
                   i.ema_50, i.ema_200, i.adx, i.roc_6h,
                   i.atr_percentile
            FROM candles c
            LEFT JOIN indicators i ON c.timestamp = i.timestamp
            WHERE c.timeframe = '1h'
            ORDER BY c.timestamp ASC
            """,
            conn,
            parse_dates=['timestamp']
        )
        conn.close()

        if len(df) < 1000:
            log.warning(f"Only {len(df)} candles — skipping recompute")
            return False

        log.info(f"Computing regime stats on {len(df)} candles...")
        stats = _compute_regime_stats(df)

        now = datetime.now(timezone.utc).isoformat()
        conn = get_conn()
        conn.execute(
            """INSERT INTO market_knowledge
               (computed_at, data_from, data_to, total_bars, stats_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                now,
                str(df['timestamp'].min()),
                str(df['timestamp'].max()),
                len(df),
                json.dumps(stats)
            )
        )
        conn.commit()
        conn.close()
        log.info(f"market_knowledge updated — {len(stats)} regimes computed")
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

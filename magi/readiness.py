"""
magi/readiness.py — live-readiness decision-support gates:

  LIVE READINESS — should real capital be deployed? Evaluated against
                   the entire trading history.

Neither gate enforces anything. Output is rendered on the dashboard
READINESS panel. The operator decides.

All computations are pure read-only against observer.db. No write paths
in this module.

A gate result is a dict:
    {
        'status': 'PASS' | 'FAIL' | 'NA',
        'value':   '<human-readable computed value>',
        'threshold': '<human-readable threshold>',
        'label':    '<short title>',
        'detail':   '<extra context / SQL hint>',
    }

PASS = the gate's condition is met.
FAIL = the gate's condition is NOT met.
NA   = insufficient data / undefined; counts as neither pass nor fail in
       the verdict aggregator.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from database import get_conn
from grid.pnl import _fifo_match


# ── Constants ────────────────────────────────────────────────────────

VALID_REGIMES = ('RANGING', 'TRENDING', 'UNCERTAIN')


# ── Internal helpers ─────────────────────────────────────────────────

def _parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _all_fills(conn, ts_floor: Optional[str] = None) -> list:
    """Time-ordered list of filled grid_orders. Optional floor on
    COALESCE(filled_at, timestamp) so we can scope to a window."""
    where = "WHERE status='filled'"
    params: tuple = ()
    if ts_floor:
        where += " AND COALESCE(filled_at, timestamp) >= ?"
        params = (ts_floor,)
    rows = conn.execute(
        f"SELECT order_id, side, price, size, fill_price, fee, "
        f"       filled_at, timestamp "
        f"FROM grid_orders {where} "
        f"ORDER BY COALESCE(filled_at, timestamp) ASC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def _trip_sell_ts(trip: dict, fills_by_id: dict) -> Optional[datetime]:
    """When the SELL leg of a round-trip closed. None if missing."""
    sell = fills_by_id.get(trip['sell_id'])
    if not sell:
        return None
    return _parse_ts(sell.get('filled_at') or sell.get('timestamp'))


def _trip_buy_ts(trip: dict, fills_by_id: dict) -> Optional[datetime]:
    """When the BUY leg opened. None if missing."""
    buy = fills_by_id.get(trip['buy_id'])
    if not buy:
        return None
    return _parse_ts(buy.get('filled_at') or buy.get('timestamp'))


def _trip_buy_notional(trip: dict, fills_by_id: dict) -> float:
    """Gross dollar notional of the BUY leg (size × fill_price). Used
    as 'gross volume' for the per-regime PnL ratio gates."""
    buy = fills_by_id.get(trip['buy_id'])
    if not buy:
        return 0.0
    price = float(buy.get('fill_price') or buy.get('price') or 0)
    size_full = float(buy.get('size') or 0)
    if size_full <= 0:
        return abs(price * float(trip.get('size') or 0))
    # Pro-rate to matched size — the trip may not have consumed the entire buy
    matched_size = float(trip.get('size') or 0)
    return abs(price * matched_size)


def _regime_at(conn, ts: datetime) -> Optional[str]:
    """Casper regime position from the most recent debate_records row
    with timestamp <= ts. Returns None if no row exists."""
    if not ts:
        return None
    row = conn.execute(
        "SELECT casper_r0_position FROM debate_records "
        "WHERE timestamp <= ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (ts.isoformat(),),
    ).fetchone()
    if not row:
        return None
    return row['casper_r0_position']


def _daily_pnl_series(trips: list, fills_by_id: dict) -> list:
    """Returns [(date_str, day_pnl), ...] ordered ASC by date. Trips are
    attributed to the date of their SELL fill (when realized)."""
    bucket: dict = defaultdict(float)
    for trip in trips:
        sell_ts = _trip_sell_ts(trip, fills_by_id)
        if not sell_ts:
            continue
        d = sell_ts.date().isoformat()
        bucket[d] += trip.get('contribution', 0.0)
    return sorted(bucket.items())


def _worst_drawdown_ratio(daily: list) -> Optional[float]:
    """For each day, compute ratio = abs(day_pnl) / cumulative_to_date,
    but ONLY when day_pnl < 0 AND cumulative_to_date > 0. Returns the
    maximum such ratio, or None if no qualifying day exists."""
    if not daily:
        return None
    running = 0.0
    worst: Optional[float] = None
    for _d, pnl in daily:
        running += pnl
        if pnl < 0 and running > 0:
            ratio = abs(pnl) / running
            if worst is None or ratio > worst:
                worst = ratio
    return worst


def _max_fill_gap_hours(fills: list) -> Optional[float]:
    """Longest gap between consecutive filled orders, in hours. None if
    fewer than 2 fills."""
    if len(fills) < 2:
        return None
    times = []
    for f in fills:
        ts = _parse_ts(f.get('filled_at') or f.get('timestamp'))
        if ts:
            times.append(ts)
    times.sort()
    if len(times) < 2:
        return None
    return max(
        (times[i] - times[i - 1]).total_seconds() / 3600.0
        for i in range(1, len(times))
    )


def _pass(value, threshold, label, detail=''):
    return {
        'status': 'PASS', 'value': str(value),
        'threshold': str(threshold), 'label': label, 'detail': detail,
    }


def _fail(value, threshold, label, detail=''):
    return {
        'status': 'FAIL', 'value': str(value),
        'threshold': str(threshold), 'label': label, 'detail': detail,
    }


def _na(value, threshold, label, detail=''):
    return {
        'status': 'NA', 'value': str(value),
        'threshold': str(threshold), 'label': label, 'detail': detail,
    }


# ── LIVE READINESS GATES (lifetime) ──────────────────────────────────

def gate_L1(conn, ctx) -> dict:
    """Round-trip volume (lifetime)."""
    n = len(ctx['life_trips'])
    label = 'L1 round-trip volume (lifetime)'
    if n >= 100:
        return _pass(f"{n} / 100", '>= 100', label,
                     'FIFO-matched round trips since first fill')
    return _fail(f"{n} / 100", '>= 100', label,
                 'FIFO-matched round trips since first fill')


def gate_L2(conn, ctx) -> dict:
    """Distinct trading days (lifetime)."""
    days = set()
    for f in ctx['life_fills']:
        ts = _parse_ts(f.get('filled_at') or f.get('timestamp'))
        if ts:
            days.add(ts.date().isoformat())
    n = len(days)
    label = 'L2 trading days (lifetime)'
    if n >= 30:
        return _pass(f"{n} / 30", '>= 30', label, '')
    return _fail(f"{n} / 30", '>= 30', label,
                 f"Distinct UTC dates with ≥1 fill: {n}")


def gate_L3(conn, ctx) -> dict:
    """Sustained TRENDING exposure: ≥12 consecutive cycles with Casper
    TRENDING & conviction>=0.6, AND ≥1 fill during the window."""
    rows = conn.execute(
        "SELECT timestamp, casper_r0_position, casper_r0_conviction "
        "FROM debate_records ORDER BY timestamp ASC"
    ).fetchall()
    label = 'L3 sustained TRENDING (≥12 cycles + fill)'
    if not rows:
        return _fail('no cycles', '≥12 cyc + 1 fill', label,
                     'No debate_records exist')

    run_start = None
    run_len = 0
    qualifying = []  # (start_ts, end_ts, length)
    for r in rows:
        pos = r['casper_r0_position']
        conv = r['casper_r0_conviction']
        if pos == 'TRENDING' and (conv or 0) >= 0.6:
            if run_start is None:
                run_start = r['timestamp']
            run_len += 1
            run_end = r['timestamp']
        else:
            if run_len >= 12:
                qualifying.append((run_start, run_end, run_len))
            run_start, run_len = None, 0
    if run_len >= 12:
        qualifying.append((run_start, run_end, run_len))

    if not qualifying:
        return _fail('0 windows', '≥12 cyc + 1 fill', label,
                     'No run of 12 consecutive TRENDING & conv≥0.6 cycles')

    # For each qualifying window, check if ≥1 fill happened during it
    for start_ts, end_ts, n in qualifying:
        row = conn.execute(
            "SELECT COUNT(*) FROM grid_orders WHERE status='filled' "
            "AND COALESCE(filled_at, timestamp) >= ? "
            "AND COALESCE(filled_at, timestamp) <= ?",
            (start_ts, end_ts),
        ).fetchone()
        if row[0] >= 1:
            return _pass(
                f"{len(qualifying)} window(s), longest={max(q[2] for q in qualifying)} cyc",
                '≥12 cyc + 1 fill',
                label,
                f"Windows: {qualifying}",
            )
    return _fail(
        f"{len(qualifying)} window(s) found but none with fills",
        '≥12 cyc + 1 fill',
        label,
        f"Windows: {qualifying}",
    )


def gate_L4(conn, ctx) -> dict:
    """Cumulative net realized PnL after fees (lifetime)."""
    trips = ctx['life_trips']
    net = sum(t['contribution'] for t in trips)
    label = 'L4 cumulative net PnL (lifetime)'
    val = f"${net:+.4f}"
    if net > 0:
        return _pass(val, '> 0', label,
                     f"Sum of {len(trips)} trip contributions (fee-net)")
    return _fail(val, '> 0', label,
                 f"Sum of {len(trips)} trip contributions (fee-net)")


def gate_L5(conn, ctx) -> dict:
    """Worst single-day drawdown (lifetime)."""
    ratio = _worst_drawdown_ratio(ctx['life_daily'])
    label = 'L5 worst-day drawdown (lifetime)'
    if ratio is None:
        return _na('n/a', '< 0.20', label, 'No qualifying losing day')
    val = f"{ratio:.3f}"
    if ratio < 0.20:
        return _pass(val, '< 0.20', label, f"Daily series: {ctx['life_daily']}")
    return _fail(val, '< 0.20', label, f"Daily series: {ctx['life_daily']}")


def gate_L6(conn, ctx) -> dict:
    """Max consecutive-fill gap (lifetime). Strict 12h. HALT not excluded."""
    gap = _max_fill_gap_hours(ctx['life_fills'])
    label = 'L6 max fill gap < 12h (lifetime)'
    if gap is None:
        return _na('1 fill', '< 12h', label,
                   'Need ≥2 lifetime fills to compute a gap')
    val = f"{gap:.1f}h"
    if gap < 12:
        return _pass(val, '< 12h', label,
                     'HALT periods NOT excluded — manual review if HALT was active during the worst gap.')
    return _fail(val, '< 12h', label,
                 'HALT periods NOT excluded — manual review if HALT was active during the worst gap.')


def gate_L7(conn, ctx) -> dict:
    """Per-regime PnL >= 0 for regimes with >=5 round trips."""
    fills_by_id = ctx['fills_by_id']
    pnl: dict = defaultdict(float)
    n: dict = defaultdict(int)
    for trip in ctx['life_trips']:
        buy_ts = _trip_buy_ts(trip, fills_by_id)
        if not buy_ts:
            continue
        regime = _regime_at(conn, buy_ts) or 'UNKNOWN'
        pnl[regime] += trip['contribution']
        n[regime] += 1

    label = 'L7 per-regime PnL ≥ 0 (lifetime, ≥5 trips)'
    qualifying = {r: pnl[r] for r in n if n[r] >= 5}
    if not qualifying:
        return _na(
            f"no regime ≥5 trips (have: " +
            ", ".join(f"{r}={n[r]}" for r in n) + ")",
            'pnl ≥ 0 per regime',
            label,
            f"Per-regime counts: {dict(n)}; pnl: " +
            ", ".join(f"{r}=${pnl[r]:+.4f}" for r in pnl),
        )
    negatives = [r for r, p in qualifying.items() if p < 0]
    breakdown = ', '.join(
        f"{r}: ${qualifying[r]:+.4f} (n={n[r]})" for r in qualifying
    )
    if not negatives:
        return _pass(breakdown, 'pnl ≥ 0 per regime', label,
                     f"All qualifying regimes non-negative. Counts: {dict(n)}")
    return _fail(breakdown, 'pnl ≥ 0 per regime', label,
                 f"Negative regime(s): {negatives}. Counts: {dict(n)}")


def gate_L8(conn, ctx) -> dict:
    """Hard-rule override rate (lifetime)."""
    rows = conn.execute(
        "SELECT COUNT(*) AS total, "
        "       SUM(CASE WHEN hard_rule_overrides IS NOT NULL "
        "                 AND hard_rule_overrides != '[]' "
        "                THEN 1 ELSE 0 END) AS with_override "
        "FROM debate_records"
    ).fetchone()
    total = rows['total'] or 0
    overrides = rows['with_override'] or 0
    label = 'L8 hard-rule override rate (lifetime)'
    if total == 0:
        return _na('0 cycles', '< 0.30', label, 'No debate_records yet')
    rate = overrides / total
    val = f"{rate:.3f} ({overrides}/{total})"
    if rate < 0.30:
        return _pass(val, '< 0.30', label,
                     'Fraction of cycles where hard_rule_overrides is non-empty')
    return _fail(val, '< 0.30', label,
                 'Fraction of cycles where hard_rule_overrides is non-empty — '
                 'rules driving most decisions')


def gate_L9(conn, ctx) -> dict:
    """Round-1 debate fire rate (lifetime)."""
    rows = conn.execute(
        "SELECT COUNT(*) AS total, "
        "       SUM(CASE WHEN debate_triggered=1 THEN 1 ELSE 0 END) AS triggered "
        "FROM debate_records"
    ).fetchone()
    total = rows['total'] or 0
    triggered = rows['triggered'] or 0
    label = 'L9 debate fire rate (lifetime)'
    if total == 0:
        return _na('0 cycles', '> 0.05', label, 'No debate_records yet')
    rate = triggered / total
    val = f"{rate:.3f} ({triggered}/{total})"
    if rate > 0.05:
        return _pass(val, '> 0.05', label,
                     'Fraction of cycles where Round-1 debate triggered')
    return _fail(val, '> 0.05', label,
                 'Fraction of cycles where Round-1 debate triggered — '
                 'multi-agent disagreement is rare')


# ── Verdict aggregation ──────────────────────────────────────────────

def _live_verdict(gates: dict) -> str:
    fails = sum(1 for g in gates.values() if g['status'] == 'FAIL')
    if fails == 0:
        return 'GREEN'
    if fails <= 2:
        return 'YELLOW'
    return 'RED'


# ── Public API ───────────────────────────────────────────────────────

def evaluate() -> dict:
    """Compute the live-readiness gates (lifetime). Returns the
    JSON-serialisable payload the dashboard /api/readiness endpoint
    surfaces."""
    conn = get_conn()
    try:
        # --- Pre-compute lifetime fills + trips ---
        life_fills = _all_fills(conn)
        life_trips, _ = _fifo_match(life_fills)
        life_fills_by_id = {f['order_id']: f for f in life_fills if f.get('order_id')}
        life_daily = _daily_pnl_series(life_trips, life_fills_by_id)

        ctx = {
            'life_fills':     life_fills,
            'life_trips':     life_trips,
            'life_daily':     life_daily,
            'fills_by_id':    life_fills_by_id,
        }

        live_gates = {
            'L1': gate_L1(conn, ctx),
            'L2': gate_L2(conn, ctx),
            'L3': gate_L3(conn, ctx),
            'L4': gate_L4(conn, ctx),
            'L5': gate_L5(conn, ctx),
            'L6': gate_L6(conn, ctx),
            'L7': gate_L7(conn, ctx),
            'L8': gate_L8(conn, ctx),
            'L9': gate_L9(conn, ctx),
        }

        return {
            'live': {
                'verdict':           _live_verdict(live_gates),
                'gates':             live_gates,
            },
            'generated_at_utc':  _utc_now().isoformat(),
        }
    finally:
        conn.close()


if __name__ == '__main__':
    from magi import adam
    adam.init_oneshot("readiness")
    print(json.dumps(evaluate(), indent=2, default=str))

import logging
import re
from collections import deque
from datetime import datetime, date, timezone

log = logging.getLogger('grid.pnl')

# Kraken order txids look like O5SRYD-UASK7-EA4OTU; pre-live paper fills use
# internal hex UUIDs with no dashes. grid_orders has no paper flag, so the txid
# shape is the only available discriminator. PnL is scoped to live fills only —
# commingling paper history overstated the headline number ~$10.
_KRAKEN_TXID = re.compile(r'^O[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{6}$')


def _is_live_order_id(order_id) -> bool:
    return bool(_KRAKEN_TXID.match(str(order_id or '')))


def current_scope_cutoff() -> str:
    """The paper_run_started_utc system_state value, or '' when no paper run is
    recorded. '' means the current scope is LIVE. Single read point so every
    fill-reader resolves scope the same way. NOTE: a future flip back to live
    trading must blank this key (or this rule must gain a successor marker)."""
    from database import get_system_state
    return get_system_state('paper_run_started_utc', default='') or ''


def fill_in_current_scope(order_id, filled_at, cutoff) -> bool:
    """True iff a fill belongs to the CURRENT run's scope. cutoff is the value
    from current_scope_cutoff(): non-empty -> paper scope (non-txid order_id
    filled at/after the cutoff, same rule as get_pnl_snapshot(paper=True));
    empty -> live scope (Kraken txid order_ids only). Keeps every current-state
    fill reader (world_state, gate triggers) on the one discriminator instead
    of each rolling its own — added 2026-06-10 after an unconditional live-only
    filter in the outcome backfill poisoned the paper run's seat grading."""
    if cutoff:
        return (not _is_live_order_id(order_id)) and (filled_at or '') >= cutoff
    return _is_live_order_id(order_id)


def _fifo_match(fills: list) -> tuple:
    """
    FIFO-match buys to sells from a time-ordered fills list.
    Returns (matched_trips, unmatched_buy_queue) where:
      matched_trips: list of {buy_id, sell_id, contribution, size}
      unmatched_buy_queue: list of remaining unmatched buy dicts
    """
    buy_queue = deque()
    for f in fills:
        if f['side'] == 'buy':
            buy_queue.append({
                'order_id': f['order_id'],
                'fill_price': float(f.get('fill_price') or f.get('price') or 0),
                'size': float(f.get('size') or 0),
                'fee': float(f.get('fee') or 0),
            })

    matched_trips = []

    for f in fills:
        if f['side'] != 'sell':
            continue
        sell_fp = float(f.get('fill_price') or f.get('price') or 0)
        sell_remaining = float(f.get('size') or 0)
        sell_fee_total = float(f.get('fee') or 0)
        sell_size_orig = sell_remaining

        while sell_remaining > 0.0001 and buy_queue:
            buy = buy_queue[0]
            if buy['size'] < 0.0001:
                buy_queue.popleft()
                continue

            match_size = min(sell_remaining, buy['size'])
            buy_fee_frac = buy['fee'] * (match_size / buy['size'])
            sell_fee_frac = sell_fee_total * (match_size / sell_size_orig) if sell_size_orig > 0 else 0

            contribution = (sell_fp - buy['fill_price']) * match_size \
                           - buy_fee_frac - sell_fee_frac

            matched_trips.append({
                'buy_id': buy['order_id'],
                'sell_id': f['order_id'],
                'contribution': contribution,
                'size': match_size,
            })

            buy['size'] -= match_size
            buy['fee'] -= buy_fee_frac
            sell_remaining -= match_size

            if buy['size'] < 0.0001:
                buy_queue.popleft()

    return matched_trips, list(buy_queue)


def get_pnl_snapshot(current_price: float, paper: bool = False) -> dict:
    """
    Compute P&L from filled grid_orders, reconciled against account equity.

    Scope (live, default): only Kraken live fills (txid-shaped order_id, see
    _is_live_order_id) are counted. Pre-live paper fills are excluded — they
    were inflating the headline by ~$10.

    Scope (paper=True, added 2026-06-09 for the paper bring-up): the inverse —
    only paper fills (non-txid order_id) AND only those filled at/after the
    `paper_run_started_utc` system_state cutoff (set at the 2026-06-09 paper
    book reset), so the pre-live May paper-era fills stay excluded. All other
    mechanics (FIFO matching, equity baseline anchored at the first in-scope
    fill) are identical to the live path.

    Total P&L is equity-based and marked to current_price:
        total = current_equity - baseline_equity
    where baseline_equity is the account value at the first live fill (held XRP
    marked at that fill's price, plus USD) and current_equity is the latest
    inventory snapshot marked at current_price. This is robust to selling seed
    inventory that has no recorded buy — a pure buy/sell FIFO reports those held
    lots as $0 unrealized, which hid the inventory drawdown.

    realized   = FIFO-matched profit from closed live round trips (net of fees).
    unrealized = total - realized (mark-to-market on the net open position).

    DECOMPOSITION (2026-07-02) — do not cite `total` alone as the profitability
    verdict; it is dominated by inventory beta (the price path of the standing
    ~23-30 XRP book vs 1.65-XRP trades). Judge the grid on:
      harvest             = realized (alias) — what the grid actually earned.
      alpha_vs_hold       = current_equity - (run-start book marked at today's
                            price) — the bot's contribution vs doing nothing.
      inventory_hold_delta = pure beta of the run-start book
                            (total = alpha_vs_hold + inventory_hold_delta).

    Returns a dict with:
      realized, unrealized, total, fees, fill_count, fills_today,
      win_rate, avg_pnl_per_round_trip, time_since_last_fill_minutes,
      matched_round_trips, unmatched_buys, baseline_equity, current_equity,
      order_pnl_map  — {sell_order_id: contribution} for matched sells
    """
    from database import get_conn
    conn = get_conn()
    rows = conn.execute('''
        SELECT order_id, side, price, size, fill_price, fee, filled_at, timestamp
        FROM grid_orders
        WHERE status='filled'
        ORDER BY COALESCE(filled_at, timestamp) ASC
    ''').fetchall()

    if paper:
        from database import get_system_state
        cutoff = get_system_state('paper_run_started_utc', default='') or ''
        fills = [
            dict(r) for r in rows
            if not _is_live_order_id(dict(r).get('order_id'))
            and (dict(r).get('filled_at') or dict(r).get('timestamp') or '') >= cutoff
        ]
    else:
        fills = [dict(r) for r in rows if _is_live_order_id(dict(r).get('order_id'))]

    if not fills:
        conn.close()
        return {
            'realized': 0.0, 'unrealized': 0.0, 'total': 0.0,
            'fees': 0.0, 'fill_count': 0, 'fills_today': 0,
            'win_rate': 0.0, 'avg_pnl_per_round_trip': None,
            'time_since_last_fill_minutes': None,
            'matched_round_trips': 0, 'unmatched_buys': 0,
            'baseline_equity': None, 'current_equity': None,
            'order_pnl_map': {}
        }

    matched_trips, unmatched_buys = _fifo_match(fills)

    realized = sum(t['contribution'] for t in matched_trips)

    # Equity-based total. Baseline = inventory at the first live fill, marked at
    # that fill's price; current = latest inventory marked at current_price.
    first_live_ts = fills[0].get('filled_at') or fills[0].get('timestamp')
    first_live_px = float(fills[0].get('fill_price') or fills[0].get('price') or 0)
    base_row = conn.execute('''
        SELECT xrp_held, usd_held FROM inventory
        WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT 1
    ''', (first_live_ts,)).fetchone()
    cur_row = conn.execute('''
        SELECT xrp_held, usd_held FROM inventory
        ORDER BY timestamp DESC LIMIT 1
    ''').fetchone()
    conn.close()

    baseline_equity = current_equity = None
    alpha_vs_hold = hold_equity_now = inventory_hold_delta = None
    if base_row and cur_row and first_live_px > 0:
        baseline_equity = float(base_row['xrp_held']) * first_live_px + float(base_row['usd_held'])
        current_equity = float(cur_row['xrp_held']) * current_price + float(cur_row['usd_held'])
        total = current_equity - baseline_equity
        unrealized = total - realized
        # Decomposition (2026-07-02): `total` is dominated by the market's price
        # path over the standing inventory (~23-30 XRP held vs 1.65-XRP trades),
        # so it is NOT a grid-performance verdict. hold_equity_now marks the
        # RUN-START book at today's price — the do-nothing counterfactual.
        # alpha_vs_hold = what the bot's actions (trades + posture) added or
        # subtracted vs. that passive hold. inventory_hold_delta is the pure
        # beta component (total = alpha_vs_hold + inventory_hold_delta).
        hold_equity_now = (
            float(base_row['xrp_held']) * current_price + float(base_row['usd_held'])
        )
        alpha_vs_hold = current_equity - hold_equity_now
        inventory_hold_delta = hold_equity_now - baseline_equity
    else:
        # No inventory baseline available — fall back to FIFO realized only.
        log.warning('pnl: no inventory baseline for equity total; using FIFO realized only')
        total = realized
        unrealized = 0.0

    fees = sum(float(f.get('fee') or 0) for f in fills)

    n_trips = len(matched_trips)
    wins = sum(1 for t in matched_trips if t['contribution'] > 0)
    win_rate = (wins / n_trips * 100) if n_trips > 0 else 0.0
    avg_pnl = (realized / n_trips) if n_trips > 0 else None

    # Time since last fill
    now_utc = datetime.now(timezone.utc)
    last_fill_ts = max(
        (f.get('filled_at') or f['timestamp'] or '') for f in fills
    )
    time_since_minutes = None
    if last_fill_ts:
        try:
            ts = last_fill_ts.replace('Z', '+00:00')
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            time_since_minutes = round((now_utc - dt).total_seconds() / 60, 1)
        except Exception:
            pass

    # Fills today
    today_str = date.today().isoformat()
    fills_today = sum(
        1 for f in fills
        if (f.get('filled_at') or f.get('timestamp') or '').startswith(today_str)
    )

    # Build sell → contribution map (only matched sells carry realized P&L)
    order_pnl_map = {}
    for t in matched_trips:
        order_pnl_map[t['sell_id']] = order_pnl_map.get(t['sell_id'], 0) + t['contribution']

    return {
        'realized': round(realized, 4),
        'harvest': round(realized, 4),  # first-class alias: fee-adjusted round-trip PnL
        'unrealized': round(unrealized, 4),
        'total': round(total, 4),
        'alpha_vs_hold': round(alpha_vs_hold, 4) if alpha_vs_hold is not None else None,
        'hold_baseline_equity_now': round(hold_equity_now, 4) if hold_equity_now is not None else None,
        'inventory_hold_delta': round(inventory_hold_delta, 4) if inventory_hold_delta is not None else None,
        'fees': round(fees, 4),
        'fill_count': len(fills),
        'fills_today': fills_today,
        'win_rate': round(win_rate, 1),
        'avg_pnl_per_round_trip': round(avg_pnl, 4) if avg_pnl is not None else None,
        'time_since_last_fill_minutes': time_since_minutes,
        'matched_round_trips': n_trips,
        'unmatched_buys': len(unmatched_buys),
        'baseline_equity': round(baseline_equity, 4) if baseline_equity is not None else None,
        'current_equity': round(current_equity, 4) if current_equity is not None else None,
        'order_pnl_map': order_pnl_map,
    }

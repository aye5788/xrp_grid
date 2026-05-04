import logging
from collections import deque
from datetime import datetime, date, timezone

log = logging.getLogger('grid.pnl')


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


def get_pnl_snapshot(current_price: float) -> dict:
    """
    Compute realized, unrealized, and total P&L from all filled grid_orders.

    Returns a dict with:
      realized, unrealized, total, fees, fill_count, fills_today,
      win_rate, avg_pnl_per_round_trip, time_since_last_fill_minutes,
      matched_round_trips, unmatched_buys,
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
    conn.close()

    fills = [dict(r) for r in rows]

    if not fills:
        return {
            'realized': 0.0, 'unrealized': 0.0, 'total': 0.0,
            'fees': 0.0, 'fill_count': 0, 'fills_today': 0,
            'win_rate': 0.0, 'avg_pnl_per_round_trip': None,
            'time_since_last_fill_minutes': None,
            'matched_round_trips': 0, 'unmatched_buys': 0,
            'order_pnl_map': {}
        }

    matched_trips, unmatched_buys = _fifo_match(fills)

    realized = sum(t['contribution'] for t in matched_trips)

    unrealized = sum(
        (current_price - float(b.get('fill_price') or 0)) * b['size'] - b['fee']
        for b in unmatched_buys
    )

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
        'unrealized': round(unrealized, 4),
        'total': round(realized + unrealized, 4),
        'fees': round(fees, 4),
        'fill_count': len(fills),
        'fills_today': fills_today,
        'win_rate': round(win_rate, 1),
        'avg_pnl_per_round_trip': round(avg_pnl, 4) if avg_pnl is not None else None,
        'time_since_last_fill_minutes': time_since_minutes,
        'matched_round_trips': n_trips,
        'unmatched_buys': len(unmatched_buys),
        'order_pnl_map': order_pnl_map,
    }

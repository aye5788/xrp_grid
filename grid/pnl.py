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
    Compute live-only P&L from filled grid_orders, reconciled against account
    equity.

    Scope: only Kraken live fills (txid-shaped order_id, see _is_live_order_id)
    are counted. Pre-live paper fills are excluded — they were inflating the
    headline by ~$10.

    Total P&L is equity-based and marked to current_price:
        total = current_equity - baseline_equity
    where baseline_equity is the account value at the first live fill (held XRP
    marked at that fill's price, plus USD) and current_equity is the latest
    inventory snapshot marked at current_price. This is robust to selling seed
    inventory that has no recorded buy — a pure buy/sell FIFO reports those held
    lots as $0 unrealized, which hid the inventory drawdown.

    realized   = FIFO-matched profit from closed live round trips (net of fees).
    unrealized = total - realized (mark-to-market on the net open position).

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
    if base_row and cur_row and first_live_px > 0:
        baseline_equity = float(base_row['xrp_held']) * first_live_px + float(base_row['usd_held'])
        current_equity = float(cur_row['xrp_held']) * current_price + float(cur_row['usd_held'])
        total = current_equity - baseline_equity
        unrealized = total - realized
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
        'unrealized': round(unrealized, 4),
        'total': round(total, 4),
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

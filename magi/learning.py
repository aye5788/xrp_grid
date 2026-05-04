import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from database import get_latest_inventory, get_conn

log = logging.getLogger('magi.learning')
EST = ZoneInfo('America/New_York')
LEARNING_LOG_PATH = '/root/xrp_grid/learning_log.md'


def has_open_position() -> bool:
    inv = get_latest_inventory()
    if not inv:
        return False
    xrp = inv.get('xrp_held', 0) or 0
    return abs(xrp) > 0.01


def is_weekend() -> bool:
    now = datetime.now(timezone.utc).astimezone(EST)
    return now.weekday() >= 5


def should_run_learning() -> tuple:
    if not is_weekend():
        return True, "Weekday"
    if has_open_position():
        return True, "Weekend with open position"
    return False, "Weekend with no position — skipping"


def get_today_decisions(hours_back: int = 24) -> list:
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(hours=hours_back)).isoformat()
    rows = conn.execute('''SELECT * FROM magi_decisions
        WHERE timestamp > ?
        ORDER BY timestamp ASC''', (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_today_orders(hours_back: int = 24) -> dict:
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(hours=hours_back)).isoformat()
    rows = conn.execute('''SELECT status, COUNT(*) as cnt FROM grid_orders
        WHERE timestamp > ? GROUP BY status''', (cutoff,)).fetchall()
    conn.close()
    return {r['status']: r['cnt'] for r in rows}


def compute_net_pnl(hours_back: int = 24) -> float:
    """Return net P&L: check pnl_daily first, fall back to computing from fills."""
    conn = get_conn()
    today = datetime.now(EST).strftime('%Y-%m-%d')
    row = conn.execute(
        'SELECT net_pnl FROM pnl_daily WHERE date = ?', (today,)
    ).fetchone()
    if row and row['net_pnl'] is not None:
        conn.close()
        return float(row['net_pnl'])
    cutoff = (datetime.utcnow() - timedelta(hours=hours_back)).isoformat()
    fills = conn.execute(
        "SELECT side, price, fill_price, size, fee FROM grid_orders "
        "WHERE filled_at > ? AND status='filled'", (cutoff,)
    ).fetchall()
    conn.close()
    pnl = 0.0
    for f in fills:
        if f['side'] == 'buy':
            pnl -= (f['fill_price'] or f['price']) * f['size']
        else:
            pnl += (f['fill_price'] or f['price']) * f['size']
        pnl -= (f['fee'] or 0)
    return round(pnl, 4)


def get_guardrail_trips_today() -> list:
    """Scan magi.log for today's guardrail-blocked lines."""
    trips = []
    log_path = '/root/xrp_grid/magi.log'
    today = datetime.now(EST).strftime('%Y-%m-%d')
    try:
        with open(log_path, 'r') as f:
            for line in f:
                if today in line and 'Guardrails blocked cycle' in line:
                    trips.append(line.strip())
    except Exception:
        pass
    return trips


def build_summary(decisions: list, orders_by_status: dict,
                  net_pnl: float, inventory: dict, guardrail_trips: list) -> str:
    today = datetime.now(EST).strftime('%Y-%m-%d')

    triggers: dict = {}
    for d in decisions:
        t = d.get('trigger') or 'unknown'
        triggers[t] = triggers.get(t, 0) + 1

    lines = [
        f"## {today}",
        "",
        f"MAGI cycles: {len(decisions)} | triggers: {triggers}",
        "",
    ]

    if decisions:
        lines.append("Cycle outputs:")
        for d in decisions:
            ts = (d.get('timestamp') or '')[:16]
            grid = d.get('consensus_grid_action', '?')
            risk = d.get('consensus_risk_action', '?')
            regime = d.get('consensus_regime', '?')
            reason = d.get('notes', '')
            lines.append(f"  {ts} | grid={grid} risk={risk} regime={regime} — {reason}")
        lines.append("")

    placed = sum(orders_by_status.values())
    filled = orders_by_status.get('filled', 0)
    cancelled = orders_by_status.get('cancelled', 0)

    lines += [
        f"Paper orders: placed={placed} filled={filled} cancelled={cancelled}",
        f"Day net P&L: ${net_pnl:.4f}",
        "",
        "Inventory snapshot:",
        f"  xrp_held={inventory.get('xrp_held', 0)}",
        f"  usd_held={inventory.get('usd_held', 0)}",
        f"  net_position_usd={inventory.get('net_position_usd', 0)}",
        f"  inventory_skew={inventory.get('inventory_skew', 0)}",
        "",
    ]

    if guardrail_trips:
        lines.append("Guardrail trips:")
        for trip in guardrail_trips:
            lines.append(f"  {trip}")
    else:
        lines.append("Guardrail trips: none")

    lines += ["", "---", ""]
    return '\n'.join(lines)


def run_learning_cycle(force: bool = False):
    should_run, reason = should_run_learning()
    if not should_run and not force:
        log.info(f"Skipping learning cycle — {reason}")
        return {'skipped': True, 'reason': reason}

    log.info(f"Learning cycle starting — {reason}")

    decisions = get_today_decisions()
    orders_by_status = get_today_orders()
    net_pnl = compute_net_pnl()
    inventory = get_latest_inventory() or {}
    guardrail_trips = get_guardrail_trips_today()

    summary = build_summary(decisions, orders_by_status, net_pnl, inventory, guardrail_trips)

    with open(LEARNING_LOG_PATH, 'a') as f:
        f.write(summary)

    log.info("Learning cycle: summary appended to learning_log.md")

    return {
        'reason': reason,
        'decisions_count': len(decisions),
        'pnl': net_pnl,
        'log_path': LEARNING_LOG_PATH
    }


if __name__ == "__main__":
    import argparse
    import json
    logging.basicConfig(level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s — %(message)s')
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    result = run_learning_cycle(force=args.force)
    print(json.dumps(result, indent=2, default=str))

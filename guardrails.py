import os
import logging
from datetime import datetime, timedelta
from config import DAILY_LOSS_LIMIT_USD, KILL_SWITCH_FILE
from database import get_conn

log = logging.getLogger('guardrails')


def kill_switch_active() -> bool:
    """Check if the manual kill switch file exists."""
    return os.path.exists(KILL_SWITCH_FILE)


def check_daily_loss() -> tuple:
    """Return (ok, daily_pnl, reason)."""
    conn = get_conn()
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    fills = conn.execute('''SELECT side, price, size, fill_price, fee
        FROM grid_orders
        WHERE filled_at > ? AND status='filled' ''', (today_start,)).fetchall()
    conn.close()
    fills = [dict(f) for f in fills]

    pnl = 0.0
    for f in fills:
        if f['side'] == 'buy':
            pnl -= (f['fill_price'] or f['price']) * f['size']
        else:
            pnl += (f['fill_price'] or f['price']) * f['size']
        pnl -= (f['fee'] or 0)

    if pnl < -DAILY_LOSS_LIMIT_USD:
        return False, pnl, f"Daily loss ${pnl:.2f} exceeds limit ${DAILY_LOSS_LIMIT_USD}"
    return True, pnl, "OK"


def check_all_guardrails() -> tuple:
    """Run all guardrails. Return (all_ok, list_of_failures)."""
    failures = []

    if kill_switch_active():
        failures.append("KILL SWITCH ACTIVE — /root/xrp_grid/HALT exists")

    pnl_ok, daily_pnl, pnl_reason = check_daily_loss()
    if not pnl_ok:
        failures.append(f"Daily loss limit: {pnl_reason}")

    return len(failures) == 0, failures

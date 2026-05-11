import os
import logging
from datetime import datetime, timedelta
from config import KILL_SWITCH_FILE
from database import get_conn

log = logging.getLogger('guardrails')


def kill_switch_active() -> bool:
    """Check if the manual kill switch file exists."""
    return os.path.exists(KILL_SWITCH_FILE)


def check_daily_loss() -> tuple:
    """
    Daily loss guardrail. Compares current total universe value against the
    universe value at start of UTC day. Trips when delta is below
    -DAILY_LOSS_LIMIT_PCT (default -15%).

    Total universe = xrp_value_usd + usd_held, where xrp_value_usd is mark-to-market
    at the price stored in the inventory snapshot.

    Returns (ok, delta_pct, message).
    """
    from config import DAILY_LOSS_LIMIT_PCT

    conn = get_conn()
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    # Earliest inventory snapshot at or after midnight UTC = baseline
    baseline = conn.execute('''
        SELECT xrp_held, usd_held, net_position_usd
        FROM inventory
        WHERE timestamp >= ?
        ORDER BY timestamp ASC
        LIMIT 1
    ''', (today_start,)).fetchone()

    # Latest snapshot = current
    current = conn.execute('''
        SELECT xrp_held, usd_held, net_position_usd
        FROM inventory
        ORDER BY timestamp DESC
        LIMIT 1
    ''').fetchone()

    conn.close()

    # If we have no baseline (e.g., service started today after midnight and inventory
    # hasn't been updated yet), pass — we have no measurement to fail against.
    if baseline is None:
        return True, 0.0, "OK (no baseline yet today)"

    # If we have no current, also pass for the same reason.
    if current is None:
        return True, 0.0, "OK (no current snapshot)"

    baseline_universe = (baseline['net_position_usd'] or 0) + (baseline['usd_held'] or 0)
    from grid.engine import GridEngine
    _e = GridEngine(paper=True)
    current_price = _e.get_current_price()
    xrp_held = current['xrp_held'] or 0
    usd_held = current['usd_held'] or 0
    if current_price:
        current_universe = xrp_held * current_price + usd_held
    else:
        current_universe = (current['net_position_usd'] or 0) + usd_held

    if baseline_universe <= 0:
        return True, 0.0, "OK (baseline universe is zero or negative)"

    delta_usd = current_universe - baseline_universe
    delta_pct = delta_usd / baseline_universe

    if delta_pct < -DAILY_LOSS_LIMIT_PCT:
        return False, delta_pct, (
            f"Daily loss {delta_pct*100:.2f}% (${delta_usd:+.2f}) "
            f"exceeds limit -{DAILY_LOSS_LIMIT_PCT*100:.0f}% "
            f"(baseline ${baseline_universe:.2f}, current ${current_universe:.2f})"
        )

    return True, delta_pct, (
        f"OK ({delta_pct*100:+.2f}%, "
        f"baseline ${baseline_universe:.2f} → current ${current_universe:.2f})"
    )


def check_all_guardrails() -> tuple:
    """Run all guardrails. Return (all_ok, list_of_failures)."""
    failures = []

    if kill_switch_active():
        failures.append("KILL SWITCH ACTIVE — /root/xrp_grid/HALT exists")

    pnl_ok, daily_pnl, pnl_reason = check_daily_loss()
    if not pnl_ok:
        failures.append(f"Daily loss limit: {pnl_reason}")

    return len(failures) == 0, failures

"""Real-time survival invariants — plan layer 2 (2026-07-02, see
02_NEXT_BUILD_TASKS.md PLAN block).

The ONE on-box piece of the proactive bug-catching architecture, kept on-box
deliberately: these are promises that deserve SAME-CYCLE detection because a
violation means the money path is actively doing something the council
forbade — a class of bug that historically sat visible in the data for days
(the June replenishment council-bypass was one SQL line away from detection
the hour it first fired).

Design constraints:
  * read-only over observer.db (the only write is the alert row on violation,
    via the existing database.insert_alert single capture point);
  * millisecond-scale — rides the existing 10-min observer tick, adds no
    latency to any decision;
  * each check is total: an internal error in one check logs and moves on
    (a broken auditor must never break trading);
  * alert dedup: insert_alert dedups on (category, agent_id,
    provider_category) within 60 min — provider_category carries the
    invariant id so distinct invariants never suppress each other.

Severities: INV-1 is 'critical' (fires ntfy — an open buy under a protective
posture is the live council-bypass class, the worst regression this system
has had). The rest are 'warn' (dashboard only).
"""

import json
import logging
from datetime import datetime, timezone

log = logging.getLogger('magi.invariants')

CATEGORY = 'invariant_violation'


def _latest_close(conn):
    row = conn.execute(
        "SELECT close FROM candles WHERE timeframe='1h' "
        "ORDER BY timestamp DESC LIMIT 1").fetchone()
    return float(row[0]) if row and row[0] else None


def _inv1_no_buys_under_protective_posture(conn):
    """No open BUY order may exist while the council's protective posture
    stands (pause_longs set, or standing stance STAND_ASIDE). This is the
    replenishment council-bypass class (2026-06-26/27, fixed 06-28) as a
    standing detector instead of a one-time fix."""
    from database import get_system_state
    gs = conn.execute(
        "SELECT pause_longs FROM grid_state "
        "ORDER BY timestamp DESC LIMIT 1").fetchone()
    pause_longs = bool(gs[0]) if gs else False
    stance = (get_system_state('council_stance', default='') or '').strip()
    if not (pause_longs or stance == 'STAND_ASIDE'):
        return None
    n = conn.execute(
        "SELECT COUNT(*) FROM grid_orders "
        "WHERE status='open' AND side='buy'").fetchone()[0]
    if n:
        return (f"{n} open BUY order(s) while protective posture stands "
                f"(pause_longs={pause_longs}, stance={stance or 'none'}) — "
                f"a council-bypass is live on the book")
    return None


def _inv2_workoff_ladder_alive(conn):
    """While the STAND_ASIDE work-off ladder is armed and has floor headroom,
    resting sell rungs must exist — the 'work inventory off' promise
    (verified broken 2026-07-02: sells exhausted and the book sat empty).
    Mirrors maintain_workoff_ladder's own gating so it cannot false-positive
    before the arm time or below the floor."""
    from database import get_system_state
    from config import ORDER_SIZE_XRP
    from magi.orchestrator import HARD_RULES

    stance = (get_system_state('council_stance', default='') or '').strip()
    if stance != 'STAND_ASIDE':
        return None
    armed_after = (get_system_state('workoff_armed_after_utc',
                                    default='') or '').strip()
    last_cycle = conn.execute(
        "SELECT MAX(timestamp) FROM debate_records").fetchone()[0]
    if not last_cycle or (armed_after and last_cycle < armed_after):
        return None  # ladder not yet armed — silence is correct
    gs = conn.execute(
        "SELECT pause_shorts FROM grid_state "
        "ORDER BY timestamp DESC LIMIT 1").fetchone()
    if gs and gs[0]:
        return None  # sell side legitimately off (XRP buffer floor)
    price = _latest_close(conn)
    inv = conn.execute(
        "SELECT xrp_held FROM inventory "
        "ORDER BY timestamp DESC LIMIT 1").fetchone()
    if not price or not inv:
        return None
    headroom_rungs = ((float(inv[0] or 0)
                       - HARD_RULES['min_xrp_buffer_usd'] / price)
                      / ORDER_SIZE_XRP)
    if headroom_rungs < 1:
        return None  # nothing left to work off — silence is correct
    n_sells = conn.execute(
        "SELECT COUNT(*) FROM grid_orders "
        "WHERE status='open' AND side='sell'").fetchone()[0]
    if n_sells == 0:
        return (f"work-off ladder armed under STAND_ASIDE with "
                f"{headroom_rungs:.1f} rungs of floor headroom but 0 resting "
                f"sells — the 'work inventory off' promise is not executing")
    return None


def _inv3_no_stuck_wake_events(conn):
    """No fired wake-class gate event may sit unconsumed for over 2h — the
    wake wire (or its startup-gate counterpart) is stuck or looping. The
    2026-07-02 restart storm consumed hourly re-fires that should have been
    suppressed; this watches the opposite failure too (events nobody
    processes)."""
    import time as _time
    row = conn.execute(
        "SELECT trigger_id, timestamp FROM magi_gate_events "
        "WHERE fired=1 AND consumed_in_cycle IS NULL "
        "AND trigger_id IN ('W1','W2') AND timestamp < ? "
        "ORDER BY timestamp LIMIT 1",
        (_time.time() - 2 * 3600,)).fetchone()
    if row:
        age_h = (_time.time() - row[1]) / 3600.0
        return (f"fired {row[0]} gate event unconsumed for {age_h:.1f}h — "
                f"wake wire stuck or suppression not marking")
    return None


# (id, severity, check_fn) — severity feeds insert_alert; 'critical' pages.
CHECKS = (
    ('INV1_buys_under_protection', 'critical',
     _inv1_no_buys_under_protective_posture),
    ('INV2_workoff_ladder_alive', 'warn', _inv2_workoff_ladder_alive),
    ('INV3_stuck_wake_events', 'warn', _inv3_no_stuck_wake_events),
)


def check_invariants(conn=None) -> list:
    """Run every invariant; alert on violations. Returns the violation list
    (for tests/CLI). Caller-supplied conn is used read-only and NOT closed
    (testability); otherwise a fresh conn is opened and closed."""
    from database import get_conn, insert_alert
    own = conn is None
    if own:
        conn = get_conn()
    violations = []
    try:
        for inv_id, severity, fn in CHECKS:
            try:
                detail = fn(conn)
            except Exception as e:
                log.warning("invariant %s check error (skipped): %r",
                            inv_id, e)
                continue
            if detail:
                violations.append((inv_id, severity, detail))
                log.warning("INVARIANT VIOLATION %s: %s", inv_id, detail)
                try:
                    insert_alert(severity, CATEGORY,
                                 f"{inv_id}: {detail}",
                                 provider_category=inv_id)
                except Exception as e:
                    log.warning("invariant alert write failed: %r", e)
    finally:
        if own:
            conn.close()
    return violations


if __name__ == '__main__':
    # Manual run: python -m magi.invariants  (prints violations, exit 1 if any)
    import sys
    logging.basicConfig(level=logging.INFO)
    v = check_invariants()
    ts = datetime.now(timezone.utc).isoformat()
    print(json.dumps({'checked_at_utc': ts,
                      'checks': [c[0] for c in CHECKS],
                      'violations': [
                          {'id': i, 'severity': s, 'detail': d}
                          for i, s, d in v]}, indent=2))
    sys.exit(1 if v else 0)

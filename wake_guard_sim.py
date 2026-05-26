"""
wake_guard_sim.py — dry-run verification of the gate-wake guards in
scheduler.py. Supersedes the earlier t2_guard_sim.py now that the guard
covers all wake-class triggers (T2/T11/T14) and adds a persistence dwell.

Exercises the REAL shipped functions (imported from scheduler), not a
reimplementation:
  - _is_wake_suppressed_nontrading()  — non-trading suppression (all triggers)
  - _dwell_t2 / _dwell_t14 / _dwell_t11 — persistence dwell, fed in-memory DBs

Reads production observer.db read-only for the suppression check; all dwell
scenarios run against throwaway in-memory SQLite DBs. Does NOT modify any
production data and does NOT start the service.

Run from /root/xrp_grid:
    python3 wake_guard_sim.py

NOTE: importing scheduler initialises the live engine (read-only — it places
no orders on import) and prints a few engine/ADAM log lines first. That is
expected.
"""

import sqlite3
import sys

DWELL_MIN = 15.0  # matches config.WAKE_DWELL_MINUTES default

# Grid band used across the T2 scenarios: centre 1.33618, 0.75% spacing,
# 5 levels -> n_pairs=2 -> upper 1.356243 / lower ~1.316. "above" = > upper.
CENTRE, SPACING, LEVELS = 1.33618, 0.0075, 5
ABOVE = 1.3570   # outside the upper band
INSIDE = 1.3400  # inside the band


def header(title):
    print("\n" + "=" * 64 + f"\n  {title}\n" + "=" * 64)


def _mem(rows_sql):
    """Build an in-memory DB with the minimal schema the dwell helpers read,
    apply the given list of (sql, params) inserts, and return a Row-factory
    connection."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE grid_state (id INTEGER PRIMARY KEY, centre_price REAL, "
        "  spacing_pct REAL, levels INTEGER, halt INTEGER DEFAULT 0);"
        "CREATE TABLE candles (id INTEGER PRIMARY KEY, timeframe TEXT, "
        "  timestamp TEXT, close REAL);"
        "CREATE TABLE grid_orders (id INTEGER PRIMARY KEY, side TEXT, "
        "  status TEXT);"
        "CREATE TABLE indicators (id INTEGER PRIMARY KEY, timeframe TEXT, "
        "  vol_regime TEXT);"
    )
    c.execute(
        "INSERT INTO grid_state (centre_price, spacing_pct, levels) "
        "VALUES (?,?,?)", (CENTRE, SPACING, LEVELS),
    )
    for sql, params in rows_sql:
        c.execute(sql, params)
    c.commit()
    return c


def _insert_1m(conn, closes):
    """closes is newest-first; store with descending timestamps so
    'ORDER BY timestamp DESC' returns them newest-first."""
    for i, cl in enumerate(closes):
        ts = f"2026-05-25T20:{59 - i:02d}:00+00:00"
        conn.execute(
            "INSERT INTO candles (timeframe, timestamp, close) "
            "VALUES ('1m', ?, ?)", (ts, cl),
        )
    conn.commit()


def check(label, got, expect):
    ok = got[0] == expect
    mark = "✓" if ok else "✗"
    print(f"  {mark} {label}: status={got[0]!r}  ({got[1]})")
    if not ok:
        print(f"      EXPECTED {expect!r}")
        sys.exit(1)


def main():
    import scheduler as S

    # ---------------------------------------------------------------
    header("STAGE 1 — non-trading suppression (real DB, all triggers)")
    # _is_wake_suppressed_nontrading is trigger-agnostic; it reads the latest
    # debate_records / grid_state from the production DB. Current state is the
    # PAUSE_INVALID + RISK_BLOCK standdown, so it must suppress.
    suppressed, reason = S._is_wake_suppressed_nontrading()
    print(f"  suppressed={suppressed}  reason={reason!r}")
    if not suppressed:
        print("  NOTE: current DB is no longer in a non-trading state — the")
        print("  suppression branch can't be exercised against live data now.")
    else:
        print("  → A T2/T11/T14 wake in this state is consumed, not run. ✓")

    # ---------------------------------------------------------------
    header("STAGE 2 — T2 price-breach dwell (1m candles)")
    # wake: 15 consecutive 1m closes above the band
    c = _mem([]); _insert_1m(c, [ABOVE] * 15)
    check("sustained breach 15min", S._dwell_t2(c, 0.0, DWELL_MIN), "wake")
    c.close()
    # drop: most recent 1m close back inside the band
    c = _mem([]); _insert_1m(c, [INSIDE] + [ABOVE] * 14)
    check("breach cleared (newest inside)", S._dwell_t2(c, 99.0, DWELL_MIN), "drop")
    c.close()
    # defer: breaching now but the run is shorter than the dwell window
    c = _mem([]); _insert_1m(c, [ABOVE] * 8 + [INSIDE] * 7)
    check("breach not yet sustained", S._dwell_t2(c, 5.0, DWELL_MIN), "defer")
    c.close()

    # ---------------------------------------------------------------
    header("STAGE 3 — T14 book-one-sided dwell")
    # wake: still one-sided (sells only) and event older than dwell
    c = _mem([("INSERT INTO grid_orders (side,status) VALUES ('sell','open')", ())])
    check("one-sided, aged past dwell", S._dwell_t14(c, 20.0, DWELL_MIN), "wake")
    c.close()
    # drop: book refilled both sides within the window
    c = _mem([
        ("INSERT INTO grid_orders (side,status) VALUES ('sell','open')", ()),
        ("INSERT INTO grid_orders (side,status) VALUES ('buy','open')", ()),
    ])
    check("book refilled -> transient", S._dwell_t14(c, 20.0, DWELL_MIN), "drop")
    c.close()
    # defer: one-sided but younger than dwell
    c = _mem([("INSERT INTO grid_orders (side,status) VALUES ('buy','open')", ())])
    check("one-sided, still young", S._dwell_t14(c, 3.0, DWELL_MIN), "defer")
    c.close()

    # ---------------------------------------------------------------
    header("STAGE 4 — T11 vol-regime-flip dwell")
    flip = {"prior": "LOW", "current": "HIGH"}
    # drop: regime reverted to prior
    c = _mem([("INSERT INTO indicators (timeframe,vol_regime) VALUES ('1h','LOW')", ())])
    check("regime reverted -> transient", S._dwell_t11(c, flip, 20.0, DWELL_MIN), "drop")
    c.close()
    # wake: regime still flipped and aged past dwell
    c = _mem([("INSERT INTO indicators (timeframe,vol_regime) VALUES ('1h','HIGH')", ())])
    check("flip held, aged past dwell", S._dwell_t11(c, flip, 20.0, DWELL_MIN), "wake")
    c.close()
    # defer: still flipped but younger than dwell
    c = _mem([("INSERT INTO indicators (timeframe,vol_regime) VALUES ('1h','HIGH')", ())])
    check("flip held, still young", S._dwell_t11(c, flip, 2.0, DWELL_MIN), "defer")
    c.close()

    header("SIMULATION COMPLETE — all dwell assertions passed")
    print("\n  Service status: magi.service unchanged by this script.\n")


if __name__ == "__main__":
    main()

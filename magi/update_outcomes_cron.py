#!/usr/bin/env python3
"""
Standalone script for updating retroactive outcome columns in magi_decisions.
Designed to run from cron every hour — independent of MAGI trigger conditions.

Calls update_outcomes() from orchestrator.py for new rows, then runs a
supplemental backfill for rows that already have outcome_1h set but are still
missing outcome_4h or outcome_8h (which update_outcomes() skips because its
query gates on outcome_1h IS NULL).
"""

import logging
import sqlite3
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.expanduser("~/eth_observer"))

from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/eth_observer/.env"))

from magi.orchestrator import update_outcomes, DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def backfill_partial_outcomes(conn: sqlite3.Connection):
    """
    update_outcomes() only touches rows where outcome_1h IS NULL.
    Rows that were updated when <4h old (so 4h/8h were set to NULL at that
    time) are never revisited.  This function fills those gaps.
    """
    partial = conn.execute("""
        SELECT id, timestamp, consensus_result, eth_price_at_trigger,
               outcome_1h, outcome_4h, outcome_8h
        FROM magi_decisions
        WHERE outcome_1h IS NOT NULL
          AND (outcome_4h IS NULL OR outcome_8h IS NULL)
          AND consensus_result IN ('long', 'short')
        ORDER BY timestamp ASC
    """).fetchall()

    if not partial:
        logger.info("backfill_partial_outcomes: nothing to backfill")
        return

    now_utc = datetime.now(timezone.utc)
    updated = 0

    for row in partial:
        decision_ts = datetime.fromisoformat(row["timestamp"])
        if decision_ts.tzinfo is None:
            decision_ts = decision_ts.replace(tzinfo=timezone.utc)

        age_hours = (now_utc - decision_ts).total_seconds() / 3600
        direction = row["consensus_result"]

        # Re-derive entry price from hourly table (same logic as update_outcomes)
        price_row = conn.execute("""
            SELECT eth_close FROM hourly
            WHERE timestamp <= ?
            ORDER BY timestamp DESC LIMIT 1
        """, (row["timestamp"],)).fetchone()

        if not price_row or not price_row["eth_close"]:
            continue

        entry_price = price_row["eth_close"]

        def get_outcome(hours_after):
            target_ts = (decision_ts + timedelta(hours=hours_after)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            result = conn.execute("""
                SELECT eth_close FROM hourly
                WHERE timestamp >= ?
                ORDER BY timestamp ASC LIMIT 1
            """, (target_ts,)).fetchone()
            if result and result["eth_close"]:
                ret_pct = (result["eth_close"] - entry_price) / entry_price * 100
                if direction == "short":
                    ret_pct = -ret_pct
                return round(ret_pct, 4), (1 if ret_pct > 0 else 0)
            return None, None

        outcome_4h, win_4h = (get_outcome(4) if age_hours >= 4 else (None, None))
        outcome_8h, win_8h = (get_outcome(8) if age_hours >= 8 else (None, None))

        if outcome_4h is None and outcome_8h is None:
            continue  # still too early, nothing new to write

        conn.execute("""
            UPDATE magi_decisions
            SET outcome_4h = COALESCE(?, outcome_4h),
                outcome_8h = COALESCE(?, outcome_8h),
                win_4h     = COALESCE(?, win_4h),
                win_8h     = COALESCE(?, win_8h)
            WHERE id = ?
        """, (outcome_4h, outcome_8h, win_4h, win_8h, row["id"]))
        updated += 1
        logger.info(
            "Backfilled id=%d (%s %s): 4h=%s 8h=%s",
            row["id"], row["timestamp"], direction,
            outcome_4h, outcome_8h,
        )

    if updated:
        conn.commit()
        logger.info("backfill_partial_outcomes: filled %d rows", updated)


def main():
    logger.info("update_outcomes_cron: starting")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        pending_before = conn.execute("""
            SELECT COUNT(*) FROM magi_decisions
            WHERE outcome_1h IS NULL
              AND consensus_result IN ('long', 'short')
        """).fetchone()[0]

        partial_before = conn.execute("""
            SELECT COUNT(*) FROM magi_decisions
            WHERE outcome_1h IS NOT NULL
              AND (outcome_4h IS NULL OR outcome_8h IS NULL)
              AND consensus_result IN ('long', 'short')
        """).fetchone()[0]

        logger.info(
            "Before update: %d fully-null rows, %d partial rows (1h filled, 4h/8h missing)",
            pending_before, partial_before,
        )

        # Step 1: standard update for rows where 1h is still null
        update_outcomes(conn)

        # Step 2: backfill rows that 1h already set but 4h/8h still null
        backfill_partial_outcomes(conn)

        pending_after = conn.execute("""
            SELECT COUNT(*) FROM magi_decisions
            WHERE outcome_1h IS NULL
              AND consensus_result IN ('long', 'short')
        """).fetchone()[0]

        partial_after = conn.execute("""
            SELECT COUNT(*) FROM magi_decisions
            WHERE outcome_1h IS NOT NULL
              AND (outcome_4h IS NULL OR outcome_8h IS NULL)
              AND consensus_result IN ('long', 'short')
        """).fetchone()[0]

        logger.info(
            "After update: %d fully-null rows, %d partial rows remaining",
            pending_after, partial_after,
        )
        logger.info("update_outcomes_cron: done")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

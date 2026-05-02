# =============================================================
# ETH OBSERVER BOT - DATABASE
# Handles all SQLite reads and writes.
# Three tables: ticks (minute), hourly, signal_events
# =============================================================

import sqlite3
import logging
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_connection():
    """Get a database connection with row factory for easy access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database():
    """
    Create all tables if they don't exist.
    Safe to call on every startup - won't overwrite existing data.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # --- MINUTE TICKS TABLE ---
    # Stores one row per minute: prices, spread, volume
    # We don't store raw WebSocket ticks - too much data.
    # We aggregate to 1-minute candles in memory then write here.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticks (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT NOT NULL,          -- ISO format UTC
            eth_perp      REAL,                   -- ETH perp mid price
            eth_bid       REAL,                   -- ETH perp best bid
            eth_ask       REAL,                   -- ETH perp best ask
            eth_spread    REAL,                   -- ask - bid in dollars
            eth_spread_pct REAL,                  -- spread as % of mid
            btc_price     REAL,                   -- BTC spot mid price
            eth_spot      REAL,                   -- ETH spot price
            volume_1m     REAL                    -- ETH perp volume this minute
        )
    """)

    # --- HOURLY TABLE ---
    # One row per completed hour. This is the main analysis table.
    # All computed signals are stored here.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hourly (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL UNIQUE,  -- Hour start, UTC ISO format
            hour_of_day     INTEGER,               -- 0-23 UTC
            day_of_week     INTEGER,               -- 0=Mon, 6=Sun
            
            -- Price data
            eth_open        REAL,
            eth_high        REAL,
            eth_low         REAL,
            eth_close       REAL,
            eth_volume      REAL,
            btc_open        REAL,
            btc_close       REAL,
            
            -- Returns
            eth_ret_pct     REAL,                  -- ETH % return this hour
            btc_ret_pct     REAL,                  -- BTC % return this hour
            eth_btc_ratio_ret REAL,                -- ETH/BTC ratio return (ETH-specific move)
            
            -- VWAP signals
            vwap_24h        REAL,                  -- 24h rolling VWAP
            vwap_dev_pct    REAL,                  -- % deviation from VWAP
            
            -- Volatility regime
            vol_24h_std     REAL,                  -- Rolling 24h std of returns
            vol_regime      TEXT,                  -- 'low', 'medium', 'high'
            
            -- Spread quality
            avg_spread_pct  REAL,                  -- Average bid-ask spread % this hour
            
            -- Premium proxy
            kraken_eth      REAL,                  -- Kraken ETH price (hourly poll)
            premium_pct     REAL,                  -- (Coinbase - Kraken) / Kraken * 100
            premium_change  REAL,                  -- Change in premium vs prior hour
            
            -- Funding rate
            funding_rate    REAL,                  -- Current hourly funding rate
            funding_change  REAL,                  -- Change vs prior hour reading
            
            -- Signal flags
            signal_long     INTEGER DEFAULT 0,     -- 1 if long signal conditions met
            signal_short    INTEGER DEFAULT 0,     -- 1 if short signal conditions met
            btc_filtered    INTEGER DEFAULT 0      -- 1 if BTC move disqualified signal
        )
    """)

    # --- SIGNAL EVENTS TABLE ---
    # Written only when signal conditions are met.
    # Forward outcomes filled in retroactively each hour.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signal_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,         -- When signal fired, UTC ISO
            direction       TEXT NOT NULL,         -- 'long' or 'short'
            
            -- Conditions at signal time
            eth_price       REAL,                  -- ETH price when signal fired
            vwap_dev_pct    REAL,                  -- How far from VWAP (%)
            btc_ret_pct     REAL,                  -- BTC move that hour
            premium_pct     REAL,                  -- Premium at signal time
            premium_rising  INTEGER,               -- 1 if premium was rising
            vol_regime      TEXT,                  -- Vol regime at signal time
            funding_rate    REAL,                  -- Funding rate at signal time
            avg_spread_pct  REAL,                  -- Spread cost at signal time
            
            -- Forward outcomes (filled in retroactively)
            outcome_1h      REAL,                  -- ETH % return 1h after signal
            outcome_4h      REAL,                  -- ETH % return 4h after signal
            outcome_8h      REAL,                  -- ETH % return 8h after signal
            win_1h          INTEGER,               -- 1 if correct direction after 1h
            win_4h          INTEGER,               -- 1 if correct direction after 4h
            win_8h          INTEGER,               -- 1 if correct direction after 8h
            
            -- Notes
            notes           TEXT                   -- Any additional context
        )
    """)

    # --- MARKET CONTEXT TABLE ---
    # One row per hour. Written by background polling threads (Deribit, Etherscan).
    # Each poll upserts only its own columns so both can share the same row.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_context (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp             TEXT NOT NULL UNIQUE,  -- Hour start, UTC ISO format
            eth_dvol              REAL,                  -- ETH implied vol (Deribit DVOL index)
            put_call_ratio        REAL,                  -- Put OI / Call OI
            put_call_direction    TEXT,                  -- 'above_1' or 'below_1'
            eth_netflow_4h        REAL,                  -- Net ETH flow from exchanges (4h); negative = net inflow (bearish)
            eth_netflow_direction TEXT                   -- 'inflow', 'outflow', or 'neutral'
        )
    """)

    # Migrate: add any missing columns to an already-existing market_context table
    _new_cols = [
        ("eth_dvol",              "REAL"),
        ("put_call_ratio",        "REAL"),
        ("put_call_direction",    "TEXT"),
        ("eth_netflow_4h",        "REAL"),
        ("eth_netflow_direction", "TEXT"),
        ("dxy_value",             "REAL"),
        ("yield_10y",             "REAL"),
    ]
    existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(market_context)").fetchall()}
    for col, col_type in _new_cols:
        if col not in existing_cols:
            cursor.execute(f"ALTER TABLE market_context ADD COLUMN {col} {col_type}")
            logger.info("Added column market_context.%s", col)

    # Migrate: add strategy_type to magi_decisions if not present.
    existing_md = {row[1] for row in cursor.execute(
        "PRAGMA table_info(magi_decisions)"
    ).fetchall()}
    if "strategy_type" not in existing_md:
        try:
            cursor.execute("ALTER TABLE magi_decisions ADD COLUMN strategy_type TEXT")
            logger.info("Added column magi_decisions.strategy_type")
        except Exception:
            pass  # table may not exist yet on first run — orchestrator creates it

    # --- LIQUIDATION SIGNALS TABLE ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS liquidation_signals (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp         TEXT NOT NULL UNIQUE,
            short_liq_usd     REAL,
            long_liq_usd      REAL,
            short_long_ratio  REAL,
            price_4h_return   REAL,
            funding_rate      REAL,
            funding_elevated  INTEGER DEFAULT 0,
            signal_confirmed  INTEGER DEFAULT 0,
            magi_triggered    INTEGER DEFAULT 0,
            created_at        TEXT
        )
    """)

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully at %s", DB_PATH)


def write_liquidation_signal(signal: dict) -> int:
    """Write or update one liquidation signal row. Returns the row id."""
    from datetime import datetime, timezone
    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT OR REPLACE INTO liquidation_signals (
                timestamp, short_liq_usd, long_liq_usd, short_long_ratio,
                price_4h_return, funding_rate, funding_elevated,
                signal_confirmed, magi_triggered, created_at
            ) VALUES (
                :timestamp, :short_liq_usd, :long_liq_usd, :short_long_ratio,
                :price_4h_return, :funding_rate, :funding_elevated,
                :signal_confirmed, :magi_triggered, :created_at
            )
        """, {
            **signal,
            "created_at": signal.get(
                "created_at",
                datetime.now(timezone.utc).isoformat()
            ),
        })
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        logger.error("Error writing liquidation signal: %s", e)
        return -1
    finally:
        conn.close()


def get_latest_liquidation_signal() -> dict | None:
    """Return the most recent liquidation_signals row, or None."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT * FROM liquidation_signals
            ORDER BY timestamp DESC LIMIT 1
        """).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Error fetching latest liquidation signal: %s", e)
        return None
    finally:
        conn.close()


def mark_liq_signal_magi_triggered(timestamp: str):
    """Set magi_triggered=1 for the given signal timestamp."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE liquidation_signals SET magi_triggered=1 WHERE timestamp=?",
            (timestamp,)
        )
        conn.commit()
    except Exception as e:
        logger.error("Error marking liq signal triggered: %s", e)
    finally:
        conn.close()


def write_tick(tick_data: dict):
    """Write one minute of aggregated tick data."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO ticks
            (timestamp, eth_perp, eth_bid, eth_ask, eth_spread,
             eth_spread_pct, btc_price, eth_spot, volume_1m)
            VALUES
            (:timestamp, :eth_perp, :eth_bid, :eth_ask, :eth_spread,
             :eth_spread_pct, :btc_price, :eth_spot, :volume_1m)
        """, tick_data)
        conn.commit()
    except Exception as e:
        logger.error("Error writing tick: %s", e)
    finally:
        conn.close()


def write_hourly(hourly_data: dict):
    """Write one completed hour of computed signals."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO hourly
            (timestamp, hour_of_day, day_of_week,
             eth_open, eth_high, eth_low, eth_close, eth_volume,
             btc_open, btc_close,
             eth_ret_pct, btc_ret_pct, eth_btc_ratio_ret,
             vwap_24h, vwap_dev_pct,
             vol_24h_std, vol_regime,
             avg_spread_pct,
             kraken_eth, premium_pct, premium_change,
             funding_rate, funding_change,
             signal_long, signal_short, btc_filtered)
            VALUES
            (:timestamp, :hour_of_day, :day_of_week,
             :eth_open, :eth_high, :eth_low, :eth_close, :eth_volume,
             :btc_open, :btc_close,
             :eth_ret_pct, :btc_ret_pct, :eth_btc_ratio_ret,
             :vwap_24h, :vwap_dev_pct,
             :vol_24h_std, :vol_regime,
             :avg_spread_pct,
             :kraken_eth, :premium_pct, :premium_change,
             :funding_rate, :funding_change,
             :signal_long, :signal_short, :btc_filtered)
        """, hourly_data)
        conn.commit()
        logger.info("Hourly row written: %s", hourly_data['timestamp'])
    except Exception as e:
        logger.error("Error writing hourly: %s", e)
    finally:
        conn.close()


def write_signal_event(event_data: dict):
    """Write a signal event when conditions are met."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO signal_events
            (timestamp, direction,
             eth_price, vwap_dev_pct, btc_ret_pct,
             premium_pct, premium_rising, vol_regime,
             funding_rate, avg_spread_pct, notes)
            VALUES
            (:timestamp, :direction,
             :eth_price, :vwap_dev_pct, :btc_ret_pct,
             :premium_pct, :premium_rising, :vol_regime,
             :funding_rate, :avg_spread_pct, :notes)
        """, event_data)
        conn.commit()
        logger.info("Signal event written: %s %s @ %.2f",
                    event_data['direction'], event_data['timestamp'],
                    event_data['eth_price'])
    except Exception as e:
        logger.error("Error writing signal event: %s", e)
    finally:
        conn.close()


def update_signal_outcomes():
    """
    Fill in forward outcomes for signal events that are old enough.
    Called every hour. Looks for signals with null outcomes where
    enough time has passed to measure the 1h, 4h, 8h results.
    """
    conn = get_connection()
    try:
        # Get signal events missing outcomes
        pending = conn.execute("""
            SELECT s.id, s.timestamp, s.direction
            FROM signal_events s
            WHERE s.outcome_8h IS NULL
        """).fetchall()

        for signal in pending:
            sig_ts = signal['timestamp']

            # Get ETH prices at 1h, 4h, 8h after signal
            outcomes = {}
            for hours, col in [(1, 'outcome_1h'), (4, 'outcome_4h'), (8, 'outcome_8h')]:
                # Find the hourly row N hours after signal
                row = conn.execute("""
                    SELECT eth_ret_pct FROM hourly
                    WHERE timestamp > ?
                    ORDER BY timestamp ASC
                    LIMIT 1 OFFSET ?
                """, (sig_ts, hours - 1)).fetchone()

                if row:
                    ret = row['eth_ret_pct']
                    outcomes[col] = ret
                    win_col = col.replace('outcome', 'win')
                    # Win = positive return for long, negative for short
                    if signal['direction'] == 'long':
                        outcomes[win_col] = 1 if ret > 0 else 0
                    else:
                        outcomes[win_col] = 1 if ret < 0 else 0

            # Only update if we have all three outcomes
            if len(outcomes) == 6:
                conn.execute("""
                    UPDATE signal_events
                    SET outcome_1h=:outcome_1h, outcome_4h=:outcome_4h,
                        outcome_8h=:outcome_8h,
                        win_1h=:win_1h, win_4h=:win_4h, win_8h=:win_8h
                    WHERE id=:id
                """, {**outcomes, 'id': signal['id']})

        conn.commit()

    except Exception as e:
        logger.error("Error updating signal outcomes: %s", e)
    finally:
        conn.close()


def prune_old_ticks(days_to_keep: int = 7):
    """
    Delete tick data older than N days.
    Tick data is the only table that grows meaningfully.
    We keep 7 days of minute-level data for recent analysis,
    but rely on the hourly table for long-term storage.
    """
    conn = get_connection()
    try:
        conn.execute("""
            DELETE FROM ticks
            WHERE timestamp < datetime('now', ? || ' days')
        """, (f'-{days_to_keep}',))
        deleted = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        if deleted > 0:
            logger.info("Pruned %d old tick rows (kept last %d days)",
                        deleted, days_to_keep)
    except Exception as e:
        logger.error("Error pruning ticks: %s", e)
    finally:
        conn.close()


def get_recent_hourly(n: int = 24) -> list:
    """Fetch the last N hourly rows for in-memory signal calculations."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM hourly
            ORDER BY timestamp DESC
            LIMIT ?
        """, (n,)).fetchall()
        return [dict(row) for row in reversed(rows)]
    except Exception as e:
        logger.error("Error fetching recent hourly: %s", e)
        return []
    finally:
        conn.close()


def upsert_market_context(timestamp: str, updates: dict):
    """
    Upsert market context columns for a given hour timestamp.
    Creates the row if it doesn't exist, then updates only the provided columns.
    This lets the Deribit and Etherscan pollers share the same hourly row without
    overwriting each other's columns.
    """
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO market_context (timestamp) VALUES (?)",
            (timestamp,),
        )
        set_clause = ", ".join(f"{col} = :{col}" for col in updates)
        conn.execute(
            f"UPDATE market_context SET {set_clause} WHERE timestamp = :ts",
            {**updates, "ts": timestamp},
        )
        conn.commit()
    except Exception as e:
        logger.error("Error upserting market context: %s", e)
    finally:
        conn.close()


def get_last_market_context() -> dict:
    """Return the most recent market_context row as a dict, or None."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT * FROM market_context
            ORDER BY timestamp DESC
            LIMIT 1
        """).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Error fetching market context: %s", e)
        return None
    finally:
        conn.close()


def get_signal_summary() -> dict:
    """
    Return a quick summary of signal performance.
    Used for daily status reporting.
    """
    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) as n FROM signal_events"
        ).fetchone()['n']

        completed = conn.execute("""
            SELECT
                COUNT(*) as n,
                AVG(win_1h) as win_rate_1h,
                AVG(win_4h) as win_rate_4h,
                AVG(outcome_1h) as avg_ret_1h,
                AVG(outcome_4h) as avg_ret_4h
            FROM signal_events
            WHERE outcome_8h IS NOT NULL
        """).fetchone()

        return {
            'total_signals': total,
            'completed_signals': completed['n'],
            'win_rate_1h': completed['win_rate_1h'],
            'win_rate_4h': completed['win_rate_4h'],
            'avg_ret_1h':  completed['avg_ret_1h'],
            'avg_ret_4h':  completed['avg_ret_4h'],
        }
    except Exception as e:
        logger.error("Error getting signal summary: %s", e)
        return {}
    finally:
        conn.close()

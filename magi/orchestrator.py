# =============================================================
# MAGI SYSTEM — ORCHESTRATOR
# Trigger check, parallel agent coordination, consensus gate.
# Writes to magi_decisions, execution_queue, and api_costs tables.
# Also updates retroactive outcome columns hourly.
#
# Two entry points:
#   run()            — full MAGI assessment (triggered by signal)
#   update_outcomes()— fills in retroactive outcome columns
#
# In paper mode: decisions are logged, execution_queue is written
# but nothing is executed. execution.py (Phase 4) reads the queue.
# =============================================================

import json
import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from magi.melchior import Melchior, get_24h_rows as melchior_get_rows, compute_trends
from magi.balthasar import Balthasar, get_24h_rows as balthasar_get_rows
from magi.casper import Casper

load_dotenv(os.path.expanduser("~/eth_observer/.env"))
logger = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/eth_observer/observer.db")
ET      = ZoneInfo("America/New_York")

# ── Constants ──────────────────────────────────────────────────
MIN_HOURS_BETWEEN_RUNS = 1
STOP_LOSS_PCT          = 0.015
PROFIT_TARGET_PCT      = 0.010

# Cost rates per token (USD)
# Source: Anthropic and OpenAI pricing as of April 2026
# Casper is free tier — always $0
COST_RATES = {
    "melchior":  {"input": 3.00 / 1_000_000, "output": 15.00 / 1_000_000},
    "balthasar": {"input": 2.50 / 1_000_000, "output": 10.00 / 1_000_000},
    "casper":    {"input": 0.0,               "output": 0.0},
}

# Monthly spend caps — matches what is set in each provider console
SPEND_CAPS = {
    "melchior":  10.00,
    "balthasar": 10.00,
    "casper":     0.00,
}


# ── Database setup ─────────────────────────────────────────────

def ensure_tables(conn: sqlite3.Connection):
    """
    Create all MAGI tables if they don't exist.
    Safe to call on every run.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS magi_decisions (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp             TEXT NOT NULL,
            trigger_reasons       TEXT,
            eth_price_at_trigger  REAL,
            vol_regime_at_trigger TEXT,
            vwap_dev_at_trigger   REAL,
            spread_at_trigger     REAL,

            melchior_vote         TEXT,
            melchior_conviction   TEXT,
            melchior_reasoning    TEXT,
            melchior_concerns     TEXT,

            balthasar_vote        TEXT,
            balthasar_conviction  TEXT,
            balthasar_reasoning   TEXT,
            balthasar_concerns    TEXT,
            balthasar_veto        INTEGER DEFAULT 0,
            balthasar_veto_reason TEXT,

            casper_vote           TEXT,
            casper_conviction     TEXT,
            casper_reasoning      TEXT,
            casper_concerns       TEXT,
            casper_macro_lean     TEXT,

            consensus_result      TEXT,
            consensus_reason      TEXT,
            contracts_decided     INTEGER DEFAULT 0,

            outcome_1h            REAL,
            outcome_4h            REAL,
            outcome_8h            REAL,
            win_1h                INTEGER,
            win_4h                INTEGER,
            win_8h                INTEGER
        );

        CREATE TABLE IF NOT EXISTS execution_queue (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp           TEXT NOT NULL,
            decision_id         INTEGER,
            direction           TEXT,
            contracts           INTEGER,
            eth_price_at_signal REAL,
            spread_at_signal    REAL,
            stop_loss_price     REAL,
            target_price        REAL,
            time_stop_et        TEXT,
            status              TEXT DEFAULT 'pending',
            executed_at         TEXT,
            cancelled_reason    TEXT,
            FOREIGN KEY (decision_id) REFERENCES magi_decisions(id)
        );

        CREATE TABLE IF NOT EXISTS api_costs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT NOT NULL,
            decision_id   INTEGER,
            agent         TEXT NOT NULL,
            model         TEXT NOT NULL,
            input_tokens  INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost_usd      REAL DEFAULT 0.0,
            FOREIGN KEY (decision_id) REFERENCES magi_decisions(id)
        );
    """)
    conn.commit()

    # Migrate: add assumption-level columns to magi_decisions if not present.
    # Safe pattern — same as database.py. Existing rows will have NULL.
    _assumption_cols = [
        ("melchior_vol_regime_assumed",    "TEXT"),
        ("melchior_btc_direction_assumed", "TEXT"),
        ("melchior_signal_basis",          "TEXT"),
        ("balthasar_risk_assessment",      "TEXT"),
        ("balthasar_account_health",       "TEXT"),
        ("casper_macro_regime",            "TEXT"),
        ("casper_btc_direction_assumed",   "TEXT"),
    ]
    existing_cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(magi_decisions)"
    ).fetchall()}
    for col, col_type in _assumption_cols:
        if col not in existing_cols:
            conn.execute(
                f"ALTER TABLE magi_decisions ADD COLUMN {col} {col_type}"
            )
            logger.info("Migrated magi_decisions: added column %s", col)
    conn.commit()


# ── Trigger logic ──────────────────────────────────────────────

def check_triggers(conn: sqlite3.Connection) -> list:
    """
    Check whether conditions have changed enough to wake MAGI.
    Reads directly from the hourly table (signals.py not built yet).
    Returns a list of trigger reason strings — empty means no trigger.

    Trigger conditions (any one sufficient):
    - VWAP deviation crosses +/- 0.5% threshold
    - BTC moved more than 0.8% in the last hour
    - Funding rate changed direction vs 2 hours ago
    - Vol regime changed in last 2 hours
    - Prior MAGI decision is more than 8 hours old with open position
    """
    triggers = []

    try:
        rows = conn.execute("""
            SELECT timestamp, vwap_dev_pct, btc_ret_pct,
                   funding_rate, vol_regime
            FROM hourly
            ORDER BY timestamp DESC
            LIMIT 3
        """).fetchall()

        if not rows:
            return []

        latest = rows[0]

        # VWAP deviation threshold
        vwap_dev = latest["vwap_dev_pct"]
        if vwap_dev is not None and abs(vwap_dev) >= 0.5:
            triggers.append(f"vwap_dev_{vwap_dev:+.2f}pct")

        # BTC move threshold
        btc_ret = latest["btc_ret_pct"]
        if btc_ret is not None and abs(btc_ret) >= 0.8:
            triggers.append(f"btc_move_{btc_ret:+.2f}pct")

        # Funding rate direction change
        if len(rows) >= 3:
            f_now = latest["funding_rate"]
            f_old = rows[2]["funding_rate"]
            if f_now is not None and f_old is not None:
                if (f_now > f_old + 0.000001) or (f_now < f_old - 0.000001):
                    triggers.append("funding_direction_changed")

        # Vol regime change
        if len(rows) >= 2:
            v_now = latest["vol_regime"]
            v_old = rows[1]["vol_regime"]
            if v_now and v_old and v_now != v_old:
                triggers.append(f"vol_regime_changed_{v_old}_to_{v_now}")

        # Prior decision stale check (8h with open position)
        last_decision = conn.execute("""
            SELECT timestamp, consensus_result
            FROM magi_decisions
            ORDER BY timestamp DESC
            LIMIT 1
        """).fetchone()

        if last_decision:
            last_ts = datetime.fromisoformat(last_decision["timestamp"])
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600
            result    = last_decision["consensus_result"]
            if age_hours >= 8 and result in ("long", "short"):
                triggers.append("prior_decision_stale_8h")

    except Exception as e:
        logger.error("Trigger check failed: %s", e)

    return triggers


def was_run_recently(conn: sqlite3.Connection) -> bool:
    """
    Returns True if MAGI ran within the last hour.
    Enforces maximum one activation per hour.
    """
    try:
        row = conn.execute("""
            SELECT timestamp FROM magi_decisions
            ORDER BY timestamp DESC LIMIT 1
        """).fetchone()

        if not row:
            return False

        last_ts = datetime.fromisoformat(row["timestamp"])
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)

        age_minutes = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60
        return age_minutes < 60

    except Exception as e:
        logger.error("Recent run check failed: %s", e)
        return False


# ── Agent runner ───────────────────────────────────────────────

def run_agents_parallel(rows: list) -> dict:
    """
    Fire all three agents simultaneously using ThreadPoolExecutor.
    Each agent gets the same data snapshot.
    Returns dict with melchior, balthasar, casper results.
    Each result includes _input_tokens and _output_tokens for cost logging.
    These private keys are stripped before any external use.
    """
    results = {}

    def run_melchior():
        result = Melchior().assess(rows=rows)
        result.setdefault("_input_tokens", 0)
        result.setdefault("_output_tokens", 0)
        return "melchior", result

    def run_balthasar():
        result = Balthasar().assess(rows=rows)
        result.setdefault("_input_tokens", 0)
        result.setdefault("_output_tokens", 0)
        return "balthasar", result

    def run_casper():
        try:
            # Casper is free tier — log zero tokens, always $0
            result = Casper().assess()
            result["_input_tokens"]  = 0
            result["_output_tokens"] = 0
            return "casper", result
        except Exception as e:
            logger.error("Casper thread error: %s", e)
            return "casper", {
                "agent": "casper", "status": "error",
                "vote": "flat", "conviction": "low",
                "reasoning": f"Agent error: {e}",
                "concerns": ["agent unavailable"],
                "veto": False, "macro_lean": "mixed",
                "_input_tokens": 0, "_output_tokens": 0
            }

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(run_melchior),
            executor.submit(run_balthasar),
            executor.submit(run_casper),
        ]
        for future in as_completed(futures):
            try:
                name, result = future.result()
                results[name] = result
            except Exception as e:
                logger.error("Agent future error: %s", e)

    return results


# ── Cost logging ───────────────────────────────────────────────

def log_api_costs(conn: sqlite3.Connection, timestamp: str,
                  decision_id: int, votes: dict):
    """
    Extract token usage from each agent vote and write to api_costs.
    Token counts are stored in _input_tokens / _output_tokens keys.
    Never raises — cost logging failure must never affect decisions.
    """
    models = {
        "melchior":  "claude-sonnet-4-6",
        "balthasar": "gpt-4o",
        "casper":    "gemini-2.5-pro",
    }

    try:
        for agent, vote in votes.items():
            input_tokens  = vote.get("_input_tokens", 0) or 0
            output_tokens = vote.get("_output_tokens", 0) or 0
            rates         = COST_RATES.get(agent, {"input": 0, "output": 0})
            cost_usd      = (
                input_tokens  * rates["input"] +
                output_tokens * rates["output"]
            )

            conn.execute("""
                INSERT INTO api_costs (
                    timestamp, decision_id, agent, model,
                    input_tokens, output_tokens, cost_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp, decision_id,
                agent, models.get(agent, "unknown"),
                input_tokens, output_tokens,
                round(cost_usd, 8)
            ))

        conn.commit()
        logger.info("API costs logged for decision %d", decision_id)

    except Exception as e:
        logger.error("Cost logging failed (non-fatal): %s", e)


# ── Consensus gate ─────────────────────────────────────────────

def apply_consensus_gate(votes: dict) -> dict:
    """
    Apply the MAGI consensus gate to three agent votes.

    Rules (in priority order):
    1. Balthasar hard veto → no trade regardless of other votes
    2. All 3 agree, all high conviction → trade, 2 contracts
    3. All 3 agree, mixed conviction → trade, 1 contract
    4. 2 of 3 agree, Balthasar not vetoing → trade, 1 contract
    5. All 3 flat → no trade
    6. Everything else → no trade (deadlock)

    API outage handling:
    - If one agent has status=error, require unanimous from remaining two
    - If two or more agents have status=error, no trade
    """
    m = votes.get("melchior", {})
    b = votes.get("balthasar", {})
    c = votes.get("casper", {})

    m_vote = m.get("vote", "flat")
    b_vote = b.get("vote", "flat")
    c_vote = c.get("vote", "flat")

    m_conv = m.get("conviction", "low")
    b_conv = b.get("conviction", "low")
    c_conv = c.get("conviction", "low")

    m_ok = m.get("status") != "error"
    b_ok = b.get("status") != "error"
    c_ok = c.get("status") != "error"

    agents_ok = sum([m_ok, b_ok, c_ok])

    if agents_ok < 2:
        return {
            "consensus_result": "no_trade",
            "consensus_reason": "two_or_more_agents_unavailable",
            "contracts_decided": 0
        }

    if agents_ok == 2:
        available_votes = [v for v, ok in [
            (m_vote, m_ok), (b_vote, b_ok), (c_vote, c_ok)
        ] if ok]
        if len(set(available_votes)) == 1 and available_votes[0] != "flat":
            if b_ok and b.get("veto"):
                return {
                    "consensus_result": "no_trade",
                    "consensus_reason": "balthasar_veto_with_one_agent_down",
                    "contracts_decided": 0
                }
            return {
                "consensus_result": available_votes[0],
                "consensus_reason": "unanimous_two_agents_one_unavailable",
                "contracts_decided": 1
            }
        else:
            return {
                "consensus_result": "no_trade",
                "consensus_reason": "no_consensus_with_one_agent_down",
                "contracts_decided": 0
            }

    # All three available
    if b.get("veto"):
        return {
            "consensus_result": "no_trade",
            "consensus_reason": f"balthasar_veto: {b.get('veto_reason', 'unspecified')}",
            "contracts_decided": 0
        }

    if m_vote == b_vote == c_vote:
        if m_vote == "flat":
            return {
                "consensus_result": "no_trade",
                "consensus_reason": "all_three_flat",
                "contracts_decided": 0
            }
        all_high  = all(conv == "high" for conv in [m_conv, b_conv, c_conv])
        contracts = 2 if all_high else 1
        reason    = "3_way_unanimous_all_high" if all_high else "3_way_unanimous_mixed_conviction"
        return {
            "consensus_result": m_vote,
            "consensus_reason": reason,
            "contracts_decided": contracts
        }

    votes_list = [m_vote, b_vote, c_vote]
    for direction in ("long", "short"):
        if votes_list.count(direction) >= 2:
            return {
                "consensus_result": direction,
                "consensus_reason": "2_of_3_agreement_balthasar_not_vetoing",
                "contracts_decided": 1
            }

    return {
        "consensus_result": "no_trade",
        "consensus_reason": "deadlock_no_majority",
        "contracts_decided": 0
    }


# ── Assumption extraction ──────────────────────────────────────

def extract_assumption_fields(latest_row: dict, rows: list,
                               votes: dict, triggers: list) -> dict:
    """
    Derive the regime each agent operated from — not what they concluded.
    Melchior's fields come from the Python-computed trends it received
    (deterministic, no keyword parsing). Balthasar's account health comes
    from the private key its assess() now returns. Casper's BTC direction
    similarly. All fallbacks produce a safe non-null string.
    """
    m = votes.get("melchior", {})
    b = votes.get("balthasar", {})
    c = votes.get("casper", {})

    # ── Melchior ─────────────────────────────────────────────────
    # compute_trends() is the exact same function Melchior called — fully deterministic.
    trends = compute_trends(rows) if rows else {}

    vol_summary = trends.get("vol_regime_summary") or ""
    # vol_regime_summary is "low (stable)", "high (transitioning)", etc.
    melchior_vol = vol_summary.split()[0] if vol_summary else (
        latest_row.get("vol_regime") or "unknown"
    )

    btc_dir_raw = trends.get("btc_6h_direction", "unknown") or "unknown"
    _btc_map    = {"up": "bullish", "down": "bearish", "flat": "neutral",
                   "unknown": "neutral"}
    melchior_btc = _btc_map.get(btc_dir_raw, "neutral")

    trigger_str  = " ".join(triggers)
    has_vwap     = "vwap_dev" in trigger_str
    has_btc      = "btc_move" in trigger_str
    has_fund     = "funding"  in trigger_str
    n_primary    = sum([has_vwap, has_btc, has_fund])
    if n_primary >= 2:
        melchior_basis = "mixed"
    elif has_vwap:
        melchior_basis = "vwap_dev"
    elif has_btc:
        melchior_basis = "btc_lead"
    elif has_fund:
        melchior_basis = "funding"
    else:
        melchior_basis = "mixed"

    # ── Balthasar ─────────────────────────────────────────────────
    if b.get("veto"):
        balt_risk = "blocking"
    elif b.get("vote") in ("long", "short") and b.get("conviction") in ("high", "medium"):
        balt_risk = "permissive"
    else:
        balt_risk = "cautious"

    # _account_health_status is set by Balthasar.assess() from acct_anal["status"]
    acct_status = b.get("_account_health_status", "") or ""
    if any(kw in acct_status for kw in ("HARD VETO", "CRITICAL")):
        balt_health = "critical"
    elif any(kw in acct_status for kw in ("THIN", "MONITOR")):
        balt_health = "stressed"
    elif "HEALTHY" in acct_status:
        balt_health = "healthy"
    else:
        # Fallback: scan free-text reasoning
        text = (b.get("reasoning", "") + " " +
                " ".join(b.get("concerns", []))).upper()
        if any(kw in text for kw in ("HARD VETO", "LIQUIDATION", "CRITICAL")):
            balt_health = "critical"
        elif any(kw in text for kw in ("THIN", "MONITOR", "STRESSED")):
            balt_health = "stressed"
        else:
            balt_health = "healthy"

    # ── Casper ────────────────────────────────────────────────────
    # macro_lean is already in the vote dict ("risk-on"/"risk-off"/"mixed")
    macro_lean   = (c.get("macro_lean") or "mixed").replace("-", "_")

    # _btc_6h_direction is set by Casper.assess() from observer context
    casper_btc_raw = (c.get("_btc_6h_direction") or "unknown").lower()
    casper_btc     = _btc_map.get(casper_btc_raw, "neutral")

    return {
        "melchior_vol_regime_assumed":    melchior_vol,
        "melchior_btc_direction_assumed": melchior_btc,
        "melchior_signal_basis":          melchior_basis,
        "balthasar_risk_assessment":      balt_risk,
        "balthasar_account_health":       balt_health,
        "casper_macro_regime":            macro_lean,
        "casper_btc_direction_assumed":   casper_btc,
    }


# ── Database writes ────────────────────────────────────────────

def write_decision(conn: sqlite3.Connection, timestamp: str,
                   trigger_reasons: list, latest_row: dict,
                   votes: dict, consensus: dict,
                   assumptions: dict) -> int:
    """
    Write the full MAGI decision to magi_decisions table.
    Returns the new row id for use in execution_queue and api_costs.
    """
    m = votes.get("melchior", {})
    b = votes.get("balthasar", {})
    c = votes.get("casper", {})

    def concerns_str(vote_dict):
        concerns = vote_dict.get("concerns", [])
        if isinstance(concerns, list):
            return ", ".join(str(x) for x in concerns)
        return str(concerns)

    cursor = conn.execute("""
        INSERT INTO magi_decisions (
            timestamp, trigger_reasons,
            eth_price_at_trigger, vol_regime_at_trigger,
            vwap_dev_at_trigger, spread_at_trigger,

            melchior_vote, melchior_conviction,
            melchior_reasoning, melchior_concerns,

            balthasar_vote, balthasar_conviction,
            balthasar_reasoning, balthasar_concerns,
            balthasar_veto, balthasar_veto_reason,

            casper_vote, casper_conviction,
            casper_reasoning, casper_concerns,
            casper_macro_lean,

            melchior_vol_regime_assumed, melchior_btc_direction_assumed,
            melchior_signal_basis,
            balthasar_risk_assessment, balthasar_account_health,
            casper_macro_regime, casper_btc_direction_assumed,

            consensus_result, consensus_reason, contracts_decided
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?
        )
    """, (
        timestamp,
        ", ".join(trigger_reasons),
        latest_row.get("eth_close"),
        latest_row.get("vol_regime"),
        latest_row.get("vwap_dev_pct"),
        latest_row.get("avg_spread_pct"),

        m.get("vote"), m.get("conviction"),
        m.get("reasoning"), concerns_str(m),

        b.get("vote"), b.get("conviction"),
        b.get("reasoning"), concerns_str(b),
        1 if b.get("veto") else 0,
        b.get("veto_reason"),

        c.get("vote"), c.get("conviction"),
        c.get("reasoning"), concerns_str(c),
        c.get("macro_lean"),

        assumptions.get("melchior_vol_regime_assumed"),
        assumptions.get("melchior_btc_direction_assumed"),
        assumptions.get("melchior_signal_basis"),
        assumptions.get("balthasar_risk_assessment"),
        assumptions.get("balthasar_account_health"),
        assumptions.get("casper_macro_regime"),
        assumptions.get("casper_btc_direction_assumed"),

        consensus["consensus_result"],
        consensus["consensus_reason"],
        consensus["contracts_decided"],
    ))
    conn.commit()
    return cursor.lastrowid


def write_execution_queue(conn: sqlite3.Connection, decision_id: int,
                          timestamp: str, consensus: dict,
                          latest_row: dict):
    """
    Write a trade instruction to execution_queue.
    Only called when consensus_result is long or short.
    """
    direction = consensus["consensus_result"]
    contracts = consensus["contracts_decided"]
    eth_price = latest_row.get("eth_close") or 0
    spread    = latest_row.get("avg_spread_pct") or 0

    if direction == "long":
        stop_loss = eth_price * (1 - STOP_LOSS_PCT)
        target    = eth_price * (1 + PROFIT_TARGET_PCT)
    else:
        stop_loss = eth_price * (1 + STOP_LOSS_PCT)
        target    = eth_price * (1 - PROFIT_TARGET_PCT)

    now_et    = datetime.now(ET)
    time_stop = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    if now_et >= time_stop:
        time_stop = time_stop + timedelta(days=1)

    conn.execute("""
        INSERT INTO execution_queue (
            timestamp, decision_id, direction, contracts,
            eth_price_at_signal, spread_at_signal,
            stop_loss_price, target_price, time_stop_et,
            status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (
        timestamp, decision_id, direction, contracts,
        eth_price, spread,
        round(stop_loss, 2), round(target, 2),
        time_stop.strftime("%Y-%m-%d %H:%M ET"),
    ))
    conn.commit()
    logger.info(
        "Execution queue: %s %d contracts | stop $%.2f | target $%.2f | time stop %s",
        direction.upper(), contracts, stop_loss, target,
        time_stop.strftime("%H:%M ET")
    )


# ── Outcome updater ────────────────────────────────────────────

def update_outcomes(conn: sqlite3.Connection):
    """
    Fill in retroactive outcome columns on past magi_decisions rows.
    Called hourly alongside the main run() check.
    """
    try:
        pending = conn.execute("""
            SELECT id, timestamp, consensus_result
            FROM magi_decisions
            WHERE outcome_1h IS NULL
              AND consensus_result IN ('long', 'short')
            ORDER BY timestamp ASC
        """).fetchall()

        if not pending:
            return

        now_utc = datetime.now(timezone.utc)
        updated = 0

        for row in pending:
            decision_ts = datetime.fromisoformat(row["timestamp"])
            if decision_ts.tzinfo is None:
                decision_ts = decision_ts.replace(tzinfo=timezone.utc)

            age_hours = (now_utc - decision_ts).total_seconds() / 3600
            if age_hours < 1.0:
                continue

            direction = row["consensus_result"]

            price_at_decision = conn.execute("""
                SELECT eth_close FROM hourly
                WHERE timestamp <= ?
                ORDER BY timestamp DESC LIMIT 1
            """, (row["timestamp"],)).fetchone()

            if not price_at_decision or not price_at_decision["eth_close"]:
                continue

            entry_price = price_at_decision["eth_close"]

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
                    win = 1 if ret_pct > 0 else 0
                    return round(ret_pct, 4), win
                return None, None

            outcome_1h, win_1h = get_outcome(1) if age_hours >= 1 else (None, None)
            outcome_4h, win_4h = get_outcome(4) if age_hours >= 4 else (None, None)
            outcome_8h, win_8h = get_outcome(8) if age_hours >= 8 else (None, None)

            conn.execute("""
                UPDATE magi_decisions
                SET outcome_1h = ?, outcome_4h = ?, outcome_8h = ?,
                    win_1h = ?, win_4h = ?, win_8h = ?
                WHERE id = ?
            """, (outcome_1h, outcome_4h, outcome_8h,
                  win_1h, win_4h, win_8h, row["id"]))
            updated += 1

        if updated:
            conn.commit()
            logger.info("Updated outcomes for %d past decisions", updated)

    except Exception as e:
        logger.error("update_outcomes failed: %s", e)


# ── Main entry points ──────────────────────────────────────────

def run(force: bool = False) -> dict:
    """
    Main MAGI assessment entry point.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        ensure_tables(conn)
        update_outcomes(conn)

        if not force and was_run_recently(conn):
            logger.info("MAGI ran recently — skipping this cycle")
            return {"status": "skipped", "reason": "ran_recently"}

        triggers = check_triggers(conn) if not force else ["manual_force"]
        if not triggers:
            logger.info("No triggers fired — MAGI staying dormant")
            return {"status": "skipped", "reason": "no_triggers"}

        logger.info("MAGI triggered: %s", ", ".join(triggers))

        rows = melchior_get_rows()
        if not rows:
            logger.error("No hourly data available — aborting")
            return {"status": "error", "reason": "no_data"}

        latest    = rows[0]
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        logger.info("Firing Melchior, Balthasar, Casper in parallel...")
        votes = run_agents_parallel(rows)

        for agent, vote in votes.items():
            logger.info(
                "%s → %s / %s | veto: %s",
                agent.upper(),
                vote.get("vote", "?").upper(),
                vote.get("conviction", "?"),
                vote.get("veto", False)
            )

        consensus   = apply_consensus_gate(votes)

        logger.info(
            "CONSENSUS: %s | reason: %s | contracts: %d",
            consensus["consensus_result"].upper(),
            consensus["consensus_reason"],
            consensus["contracts_decided"]
        )

        # Extract assumption fields before writing — used for both DB logging
        # and the mismatch check that follows.
        assumptions = extract_assumption_fields(dict(latest), rows, votes, triggers)

        decision_id = write_decision(
            conn, timestamp, triggers, dict(latest), votes, consensus, assumptions
        )

        # Log API costs — always after decision is written, never blocks it
        log_api_costs(conn, timestamp, decision_id, votes)

        # ── Assumption mismatch detection ─────────────────────────
        # Fires only when agents agreed on a trade direction.
        # Surfaces silent divergence: same vote, incompatible premises.
        if consensus["consensus_result"] in ("long", "short"):
            direction  = consensus["consensus_result"].upper()
            m_btc      = assumptions.get("melchior_btc_direction_assumed", "")
            c_btc      = assumptions.get("casper_btc_direction_assumed", "")
            b_risk     = assumptions.get("balthasar_risk_assessment", "")

            # BTC direction: apples-to-apples — both agents read the same market
            # over the same timeframe. Divergence here is a genuine signal conflict.
            if (m_btc and c_btc and m_btc != c_btc
                    and m_btc != "neutral" and c_btc != "neutral"):
                logger.warning(
                    "ASSUMPTION MISMATCH: %s consensus from different BTC reads — "
                    "Melchior=%s, Casper=%s",
                    direction, m_btc, c_btc,
                )

            # Balthasar permissive while Melchior reads falling BTC: risk manager
            # should be cautious when the macro trend is bearish.
            if m_btc == "bearish" and b_risk == "permissive":
                logger.warning(
                    "ASSUMPTION MISMATCH: Balthasar is permissive but Melchior reads "
                    "BTC as bearish — risk stance may be too loose for current conditions",
                )

        if consensus["consensus_result"] in ("long", "short"):
            write_execution_queue(
                conn, decision_id, timestamp, consensus, dict(latest)
            )
        else:
            logger.info("No trade — execution queue not written")

        return {
            "status":      "ok",
            "timestamp":   timestamp,
            "triggers":    triggers,
            "votes":       {k: {"vote": v.get("vote"), "conviction": v.get("conviction"),
                                "veto": v.get("veto")} for k, v in votes.items()},
            "consensus":   consensus,
            "decision_id": decision_id,
        }

    except Exception as e:
        logger.error("Orchestrator run() failed: %s", e)
        return {"status": "error", "reason": str(e)}

    finally:
        conn.close()


def run_paper_mode():
    """
    Wrapper for running as a standalone script in paper mode.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("=" * 60)
    logger.info("MAGI ORCHESTRATOR — PAPER MODE")
    logger.info("=" * 60)

    result = run()
    logger.info("Result: %s", json.dumps(result, indent=2, default=str))
    return result


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    if force:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        logger.info("Force flag set — bypassing trigger and recency checks")
        result = run(force=True)
    else:
        result = run_paper_mode()

    print(json.dumps(result, indent=2, default=str))

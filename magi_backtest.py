#!/usr/bin/env python3
# =============================================================
# MAGI HISTORICAL BACKTEST — LIQUIDATION SQUEEZE SIGNALS
# Replays MAGI deliberation for every confirmed failed short-
# squeeze signal in the database and simulates trade outcomes.
# =============================================================

import sys, os
sys.path.insert(0, '/root/eth_observer')
os.chdir('/root/eth_observer')

import sqlite3
import json
import time
import logging
import statistics
import math
import urllib.request
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv('/root/eth_observer/.env')
logging.basicConfig(level=logging.WARNING)  # suppress agent noise

# ── PART 1A: Mock Balthasar account health ─────────────────────
import magi.balthasar as _bm
_bm.get_account_health = lambda: {
    "futures_buying_power":      134.0,
    "available_margin":          134.0,
    "liquidation_buffer_pct":    1000.0,
    "liquidation_buffer_amount": 134.0,
    "unrealized_pnl":            0.0,
    "initial_margin_used":       0.0,
    "total_usd_balance":         134.0,
    "funding_pnl":               0.0,
    "margin_window_type":        "FCM_MARGIN_WINDOW_TYPE_INTRADAY",
    "api_status":                "ok"
}

# Also mock get_recent_signal_history to return empty (clean backtest)
_bm.get_recent_signal_history = lambda: []

# ── PART 1B: Load F&G history (500 days from Alternative.me) ───
print("Loading Fear & Greed history...")
fg_by_date = {}
try:
    url = "https://api.alternative.me/fng/?limit=500&format=json"
    with urllib.request.urlopen(url, timeout=20) as resp:
        raw = json.loads(resp.read())

    fg_entries = raw.get("data", [])
    # Sort oldest first so we can look up prior entries
    fg_sorted = sorted(fg_entries, key=lambda x: int(x["timestamp"]))

    for i, entry in enumerate(fg_sorted):
        val_now = int(entry["value"])
        date_ts = datetime.utcfromtimestamp(int(entry["timestamp"]))
        date_str = date_ts.strftime("%Y-%m-%d")

        # Compute 3-day trend (compare to 2 entries back if available)
        if i >= 2:
            val_old = int(fg_sorted[i - 2]["value"])
            diff = val_now - val_old
            if diff > 5:
                trend = "IMPROVING"
            elif diff < -5:
                trend = "DETERIORATING"
            else:
                trend = "STABLE"
            three_d_vals = [
                int(fg_sorted[i]["value"]),
                int(fg_sorted[i - 1]["value"]),
                int(fg_sorted[i - 2]["value"]),
            ]
        else:
            trend = "STABLE"
            three_d_vals = [val_now]

        fg_by_date[date_str] = {
            "fear_greed_index":          val_now,
            "fear_greed_classification": entry["value_classification"],
            "fear_greed_3d_trend":       trend,
            "fear_greed_3d_values":      three_d_vals,
            "status":                    "ok",
        }

    print(f"  Loaded F&G data for {len(fg_by_date)} dates")
    print(f"  Date range: {min(fg_by_date)} → {max(fg_by_date)}")
except Exception as e:
    print(f"  WARNING: F&G fetch failed — {e}")
    print("  Proceeding with empty F&G cache (signals will show error)")

# ── PART 1C: Patch Casper data fetchers ────────────────────────
import magi.casper as _cm

_current_fg  = {}
_current_obs = {}
_current_dxy = {}
_current_yld = {}

_cm.get_fear_greed       = lambda: _current_fg
_cm.get_coingecko_data   = lambda: {
    "btc_dominance_pct":         55.0,
    "eth_dominance_pct":         12.0,
    "total_market_cap_usd":      3.0e12,
    "market_cap_change_24h_pct": 0.0,
    "status":                    "ok",
}
_cm.get_dxy_data         = lambda: _current_dxy
_cm.get_yield_data       = lambda: _current_yld
_cm.get_observer_context = lambda: _current_obs

# Now import agents and orchestrator pieces
from magi.melchior import Melchior, compute_trends
from magi.balthasar import Balthasar, compute_time_analysis, compute_account_analysis, \
    compute_friction_analysis, compute_drawdown_analysis
from magi.casper import Casper
from magi.orchestrator import apply_consensus_gate
from magi.conflict_detector import detect as detect_conflicts

# ── PART 2: Signal extraction ───────────────────────────────────
print("\nConnecting to database...")
conn = sqlite3.connect('/root/eth_observer/observer.db')
conn.row_factory = sqlite3.Row

# Compute p90 short liquidations threshold
p90_vals = conn.execute("""
    SELECT short_liquidations_usd FROM coinglass_liquidations
    WHERE short_liquidations_usd > 0
    ORDER BY short_liquidations_usd
""").fetchall()
all_vals = [r[0] for r in p90_vals]
p90_idx  = int(len(all_vals) * 0.90)
p90_threshold = sorted(all_vals)[p90_idx]
print(f"  p90 short liq threshold: ${p90_threshold:,.0f}")

# Fetch base signals
base_signals = conn.execute("""
    SELECT
        l.timestamp,
        l.short_liquidations_usd AS short_liq,
        l.long_liquidations_usd  AS long_liq,
        l.short_liquidations_usd / l.long_liquidations_usd AS ratio,
        b.outcome_4h             AS ret_4h,
        b.eth_close,
        b.btc_close,
        b.vol_regime,
        b.signal_long,
        b.signal_short,
        b.eth_ret_pct,
        b.btc_ret_pct,
        b.vwap_24h,
        b.vwap_dev_pct,
        b.vol_24h_std
    FROM coinglass_liquidations l
    JOIN backtest_results b ON substr(b.timestamp,1,16) = substr(l.timestamp,1,16)
    WHERE l.short_liquidations_usd > 0
      AND l.long_liquidations_usd  > 0
      AND l.short_liquidations_usd > 2.0 * l.long_liquidations_usd
      AND b.outcome_4h < 0.5
      AND b.outcome_4h IS NOT NULL
    ORDER BY l.timestamp
""").fetchall()

# Apply p90 filter
signals = [dict(r) for r in base_signals if r['short_liq'] >= p90_threshold]
print(f"  Signals after p90 filter: {len(signals)}")

# ── PART 3: Create output table ────────────────────────────────
conn.execute("""
    CREATE TABLE IF NOT EXISTS magi_backtest_decisions (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_timestamp      TEXT NOT NULL UNIQUE,
        short_liq_usd         REAL,
        long_liq_usd          REAL,
        short_long_ratio      REAL,
        price_4h_return       REAL,
        funding_rate          REAL,
        funding_elevated      INTEGER,
        eth_price_at_signal   REAL,
        btc_4h_return         REAL,
        vol_regime            TEXT,
        fear_greed_at_signal  INTEGER,

        melchior_vote         TEXT,
        melchior_conviction   TEXT,
        melchior_reasoning    TEXT,
        balthasar_vote        TEXT,
        balthasar_conviction  TEXT,
        balthasar_reasoning   TEXT,
        balthasar_veto        INTEGER DEFAULT 0,
        casper_vote           TEXT,
        casper_conviction     TEXT,
        casper_reasoning      TEXT,
        casper_macro_lean     TEXT,

        conflicts_detected    INTEGER DEFAULT 0,
        challenge_ran         INTEGER DEFAULT 0,
        consensus_result      TEXT,
        consensus_reason      TEXT,
        contracts_decided     INTEGER DEFAULT 0,

        trade_taken           INTEGER DEFAULT 0,
        entry_price           REAL,
        stop_price            REAL,
        target_price          REAL,
        exit_price            REAL,
        exit_reason           TEXT,
        exit_hours            INTEGER,
        gross_pct             REAL,
        fees_pct              REAL,
        net_pct               REAL,
        win                   INTEGER,

        created_at            TEXT
    )
""")
conn.commit()

# Constants
FUNDING_MEDIAN = 0.00202

def safe(val, decimals=4):
    """Safe float formatter."""
    if val is None:
        return None
    try:
        return round(float(val), decimals)
    except (TypeError, ValueError):
        return None

def build_assumption_matrix(votes: dict, vol_regime: str, btc_dir: str) -> dict:
    """
    Build assumption_matrix for conflict_detector.detect().
    Uses the actual vote results to populate the matrix.
    """
    m = votes.get("melchior", {})
    b = votes.get("balthasar", {})
    c = votes.get("casper", {})

    # Map BTC direction strings
    btc_dir_lower = (btc_dir or "unknown").lower()
    btc_map = {"up": "bullish", "down": "bearish", "flat": "neutral", "unknown": "neutral"}
    mel_btc = btc_map.get(btc_dir_lower, "neutral")

    # Casper macro_lean → btc direction proxy
    c_macro = (c.get("macro_lean") or "mixed").replace("-", "_")
    c_btc_map = {"risk_on": "bullish", "risk_off": "bearish", "mixed": "neutral"}
    cas_btc = c_btc_map.get(c_macro, "neutral")

    # Melchior vol regime
    vol_str = (vol_regime or "unknown").lower()

    return {
        "melchior": {
            "btc_direction":    mel_btc,
            "vol_regime":       vol_str,
            "risk_environment": "risk_off" if m.get("vote") == "short" else "risk_on",
            "_vote":            m.get("vote", "flat"),
            "_key_evidence":    [],
        },
        "balthasar": {
            "vol_regime": vol_str,
            "_vote":      b.get("vote", "flat"),
        },
        "casper": {
            "btc_direction": cas_btc,
            "macro_regime":  c_macro,
            "_vote":         c.get("vote", "flat"),
        },
    }


def build_hourly_rows_from_backtest(conn, signal_ts: str) -> list:
    """
    Build 24 synthetic hourly rows from backtest_results for Melchior/Balthasar.
    Computes hour_of_day and day_of_week from timestamp.
    Sets unavailable columns (funding_rate, avg_spread_pct, etc.) to None.
    """
    rows = conn.execute("""
        SELECT timestamp, eth_close, btc_close, eth_ret_pct, btc_ret_pct,
               eth_24h_ret, vwap_24h, vwap_dev_pct, vol_regime, vol_24h_std,
               signal_long, signal_short
        FROM backtest_results
        WHERE timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT 24
    """, (signal_ts,)).fetchall()

    result = []
    for r in rows:
        ts_str = r['timestamp']
        # Parse timestamp
        try:
            ts = datetime.strptime(ts_str[:16], '%Y-%m-%d %H:%M')
            hour_of_day = ts.hour
            day_of_week = ts.weekday()  # 0=Mon, 6=Sun
        except Exception:
            hour_of_day = None
            day_of_week = None

        result.append({
            'timestamp':        ts_str,
            'hour_of_day':      hour_of_day,
            'day_of_week':      day_of_week,
            'eth_close':        r['eth_close'],
            'eth_ret_pct':      r['eth_ret_pct'],
            'btc_ret_pct':      r['btc_ret_pct'],
            'eth_btc_ratio_ret': None,  # not in backtest_results
            'vwap_24h':         r['vwap_24h'],
            'vwap_dev_pct':     r['vwap_dev_pct'],
            'vol_24h_std':      r['vol_24h_std'],
            'vol_regime':       r['vol_regime'],
            'avg_spread_pct':   None,   # not in backtest_results
            'funding_rate':     None,   # not in backtest_results
            'signal_long':      r['signal_long'],
            'signal_short':     r['signal_short'],
            'premium_pct':      None,   # not in backtest_results
            'premium_change':   None,
            'btc_close':        r['btc_close'],
        })
    return result


# ── PART 4: Per-signal loop ────────────────────────────────────
total = len(signals)
print(f"\nStarting MAGI deliberation for {total} signals...\n")
print("=" * 70)

results_rows = []

for sig_idx, signal in enumerate(signals):
    sig_ts = signal['timestamp']

    # ── 4a. BTC 4h return ──────────────────────────────────────
    btc_rows = conn.execute("""
        SELECT btc_close FROM backtest_results
        WHERE timestamp <= ? ORDER BY timestamp DESC LIMIT 5
    """, (sig_ts,)).fetchall()

    btc_4h_return = None
    if btc_rows and len(btc_rows) >= 5:
        btc_now   = btc_rows[0]['btc_close']
        btc_4h_ago = btc_rows[4]['btc_close']
        if btc_now and btc_4h_ago and btc_4h_ago != 0:
            btc_4h_return = (btc_now - btc_4h_ago) / btc_4h_ago * 100

    # ── 4b. Get 24 hourly rows for Melchior/Balthasar context ──
    hourly_rows_24h = build_hourly_rows_from_backtest(conn, sig_ts)

    # ── 4c. Build liq_signal dict ─────────────────────────────
    # funding_rate from backtest_results is None (column doesn't exist)
    # Use 0 as default (no funding rate data available for backtest period)
    funding_rate_val = None  # backtest_results has no funding_rate column

    liq_signal = {
        'short_liq_usd':    signal['short_liq'],
        'long_liq_usd':     signal['long_liq'],
        'short_long_ratio': signal['ratio'],
        'price_4h_return':  signal['ret_4h'],
        'funding_rate':     funding_rate_val,
        'funding_elevated': 1 if (funding_rate_val and funding_rate_val > FUNDING_MEDIAN) else 0,
        'signal_confirmed': 1,
    }

    # ── 4d. Set Casper patches for this signal timestamp ───────
    date_str = sig_ts[:10]  # YYYY-MM-DD
    _current_fg.clear()
    _current_fg.update(fg_by_date.get(date_str, {'status': 'error', 'error': 'no data'}))

    # Try DXY / yield from market_context (allow lookback)
    mc_row = conn.execute("""
        SELECT dxy_value, yield_10y FROM market_context
        WHERE timestamp <= ? ORDER BY timestamp DESC LIMIT 1
    """, (sig_ts,)).fetchone()

    _current_dxy.clear()
    _current_yld.clear()

    if mc_row and mc_row['dxy_value']:
        _current_dxy.update({
            'dxy_close':      mc_row['dxy_value'],
            'dxy_change_pct': 0.0,
            'dxy_direction':  'STABLE',
            'status':         'ok',
        })
    else:
        _current_dxy.update({'status': 'error', 'error': 'no historical DXY data'})

    if mc_row and mc_row['yield_10y']:
        _current_yld.update({
            'yield_10y':        mc_row['yield_10y'],
            'yield_direction':  'STABLE',
            'yield_change_bps': 0.0,
            'status':           'ok',
        })
    else:
        _current_yld.update({'status': 'error', 'error': 'no historical yield data'})

    # Build observer context from historical backtest rows
    obs_rows = conn.execute("""
        SELECT timestamp, eth_close, btc_close, btc_ret_pct,
               vol_regime
        FROM backtest_results WHERE timestamp <= ? ORDER BY timestamp DESC LIMIT 6
    """, (sig_ts,)).fetchall()

    btc_rets = [r['btc_ret_pct'] for r in obs_rows if r['btc_ret_pct'] is not None]
    btc_sum  = sum(btc_rets) if btc_rets else 0
    btc_dir  = 'up' if btc_sum > 0.3 else 'down' if btc_sum < -0.3 else 'flat'

    latest_obs = dict(obs_rows[0]) if obs_rows else {}

    # Compute hour/day from timestamp
    try:
        ts_dt = datetime.strptime(latest_obs.get('timestamp', '')[:16], '%Y-%m-%d %H:%M')
        hour_utc = ts_dt.hour
        day_idx  = ts_dt.weekday()
    except Exception:
        hour_utc = None
        day_idx  = 0

    days_list = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

    _current_obs.clear()
    _current_obs.update({
        "premium_pct":           None,   # not in backtest_results
        "premium_direction":     "UNKNOWN",
        "vol_regime":            latest_obs.get('vol_regime'),
        "btc_6h_direction":      btc_dir,
        "hour_of_day_utc":       hour_utc,
        "day_of_week":           days_list[day_idx],
        "eth_price":             latest_obs.get('eth_close'),
        "status":                "ok",
        "liq_short_usd":         liq_signal['short_liq_usd'],
        "liq_long_usd":          liq_signal['long_liq_usd'],
        "liq_ratio":             liq_signal['short_long_ratio'],
        "liq_4h_return":         liq_signal['price_4h_return'],
        "liq_funding_elevated":  liq_signal['funding_elevated'],
        "liq_signal_confirmed":  1,
    })

    # ── 4e. Run all three agents in parallel ───────────────────
    m_result = None
    b_result = None
    c_result = None

    def run_melchior_bt():
        try:
            return "melchior", Melchior().assess(rows=hourly_rows_24h, liq_signal=liq_signal)
        except Exception as e:
            return "melchior", {
                "agent": "melchior", "status": "error", "vote": "flat",
                "conviction": "low", "reasoning": f"AGENT_ERROR: {e}",
                "concerns": ["agent error"], "veto": False,
                "_input_tokens": 0, "_output_tokens": 0,
            }

    def run_balthasar_bt():
        try:
            return "balthasar", Balthasar().assess(rows=hourly_rows_24h, liq_signal=liq_signal)
        except Exception as e:
            return "balthasar", {
                "agent": "balthasar", "status": "error", "vote": "flat",
                "conviction": "low", "reasoning": f"AGENT_ERROR: {e}",
                "concerns": ["agent error"], "veto": False,
                "veto_reason": None, "_input_tokens": 0, "_output_tokens": 0,
            }

    def run_casper_bt():
        try:
            return "casper", Casper().assess(liq_signal=liq_signal)
        except Exception as e:
            return "casper", {
                "agent": "casper", "status": "error", "vote": "flat",
                "conviction": "low", "reasoning": f"AGENT_ERROR: {e}",
                "concerns": ["agent error"], "veto": False,
                "macro_lean": "mixed", "_input_tokens": 0, "_output_tokens": 0,
            }

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(run_melchior_bt),
            executor.submit(run_balthasar_bt),
            executor.submit(run_casper_bt),
        ]
        for future in as_completed(futures):
            try:
                name, result = future.result()
                if name == "melchior":
                    m_result = result
                elif name == "balthasar":
                    b_result = result
                elif name == "casper":
                    c_result = result
            except Exception as e:
                pass

    # Fallback if any agent is None
    if m_result is None:
        m_result = {"agent": "melchior", "status": "error", "vote": "flat",
                    "conviction": "low", "reasoning": "AGENT_ERROR: no result",
                    "concerns": [], "veto": False}
    if b_result is None:
        b_result = {"agent": "balthasar", "status": "error", "vote": "flat",
                    "conviction": "low", "reasoning": "AGENT_ERROR: no result",
                    "concerns": [], "veto": False, "veto_reason": None}
    if c_result is None:
        c_result = {"agent": "casper", "status": "error", "vote": "flat",
                    "conviction": "low", "reasoning": "AGENT_ERROR: no result",
                    "concerns": [], "veto": False, "macro_lean": "mixed"}

    m_vote = m_result.get("vote", "flat")
    b_vote = b_result.get("vote", "flat")
    c_vote = c_result.get("vote", "flat")

    # ── 4f. Conflict detection ─────────────────────────────────
    votes = {
        "melchior":  m_result,
        "balthasar": b_result,
        "casper":    c_result,
    }

    trends = compute_trends(hourly_rows_24h)
    btc_dir_str = trends.get("btc_6h_direction", "flat")
    vol_regime_str = signal.get("vol_regime", "unknown")

    assumption_matrix = build_assumption_matrix(votes, vol_regime_str, btc_dir_str)
    conflicts = detect_conflicts(assumption_matrix)

    # Challenge round: SKIP (prompts not available for liquidation strategy)
    challenge_ran = 0  # challenge_round_skipped

    # ── 4g. Apply consensus gate ───────────────────────────────
    consensus = apply_consensus_gate(votes)

    m_conv = m_result.get("conviction", "low")
    b_conv = b_result.get("conviction", "low")
    c_conv = c_result.get("conviction", "low")

    print(f"Signal {sig_idx+1}/{total}: {sig_ts} | "
          f"Mel:{m_vote}/{m_conv} Bal:{b_vote}/{b_conv} Cas:{c_vote}/{c_conv} "
          f"→ {consensus['consensus_result']} | conflicts:{len(conflicts)}")

    # ── 4h. Trade simulation ───────────────────────────────────
    trade_taken    = 0
    entry_price    = None
    stop_price     = None
    target_price   = None
    exit_price     = None
    exit_reason    = None
    exit_hours     = None
    gross_pct      = None
    fees_pct       = None
    net_pct        = None
    win            = None

    if consensus['consensus_result'] == 'short':
        trade_taken = 1
        entry = signal['eth_close']
        stop  = entry * 1.0075   # +0.75%
        tgt   = entry * 0.9800   # -2.0%

        entry_price  = entry
        stop_price   = round(stop, 2)
        target_price = round(tgt, 2)

        # fees: 0.09% taker each side + $0.30 flat ($0.15 each way)
        # notional ≈ 0.1 * entry
        taker_rt_pct = 0.18      # 2 × 0.09%
        nfa_rt_pct   = 0.30 / (0.1 * entry) * 100
        fees_pct     = taker_rt_pct + nfa_rt_pct

        # Walk forward hour by hour (max 12 rows after signal)
        fwd_rows = conn.execute("""
            SELECT timestamp, eth_close FROM backtest_results
            WHERE timestamp > ? ORDER BY timestamp ASC LIMIT 12
        """, (sig_ts,)).fetchall()

        for fwd_i, frow in enumerate(fwd_rows):
            p = frow['eth_close']
            if p is None:
                continue
            if p >= stop:
                exit_price  = p
                exit_reason = 'stop'
                exit_hours  = fwd_i + 1
                break
            if p <= tgt:
                exit_price  = p
                exit_reason = 'target'
                exit_hours  = fwd_i + 1
                break
        else:
            if fwd_rows:
                exit_price  = fwd_rows[-1]['eth_close']
                exit_reason = 'time'
                exit_hours  = len(fwd_rows)

        if exit_price and entry:
            # Short P&L: positive if price fell
            gross_pct = (entry - exit_price) / entry * 100
            net_pct   = gross_pct - fees_pct
            win       = 1 if net_pct > 0 else 0

    # ── 4i. Insert into magi_backtest_decisions ────────────────
    fear_greed_val = _current_fg.get("fear_greed_index")

    conn.execute("""
        INSERT OR REPLACE INTO magi_backtest_decisions (
            signal_timestamp, short_liq_usd, long_liq_usd, short_long_ratio,
            price_4h_return, funding_rate, funding_elevated,
            eth_price_at_signal, btc_4h_return, vol_regime, fear_greed_at_signal,

            melchior_vote, melchior_conviction, melchior_reasoning,
            balthasar_vote, balthasar_conviction, balthasar_reasoning, balthasar_veto,
            casper_vote, casper_conviction, casper_reasoning, casper_macro_lean,

            conflicts_detected, challenge_ran,
            consensus_result, consensus_reason, contracts_decided,

            trade_taken, entry_price, stop_price, target_price,
            exit_price, exit_reason, exit_hours,
            gross_pct, fees_pct, net_pct, win,
            created_at
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?
        )
    """, (
        sig_ts,
        signal['short_liq'], signal['long_liq'], signal['ratio'],
        signal['ret_4h'], funding_rate_val, liq_signal['funding_elevated'],
        signal['eth_close'], btc_4h_return, signal['vol_regime'], fear_greed_val,

        m_vote, m_conv, m_result.get("reasoning", ""),
        b_vote, b_conv, b_result.get("reasoning", ""),
        1 if b_result.get("veto") else 0,
        c_vote, c_conv, c_result.get("reasoning", ""),
        c_result.get("macro_lean", "mixed"),

        len(conflicts), challenge_ran,
        consensus['consensus_result'], consensus['consensus_reason'],
        consensus['contracts_decided'],

        trade_taken, entry_price, stop_price, target_price,
        exit_price, exit_reason, exit_hours,
        round(gross_pct, 4) if gross_pct is not None else None,
        round(fees_pct, 4) if fees_pct is not None else None,
        round(net_pct, 4) if net_pct is not None else None,
        win,
        datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    ))
    conn.commit()

    # Store for report
    results_rows.append({
        'signal_timestamp':   sig_ts,
        'short_liq':          signal['short_liq'],
        'long_liq':           signal['long_liq'],
        'ratio':              signal['ratio'],
        'ret_4h':             signal['ret_4h'],
        'eth_close':          signal['eth_close'],
        'vol_regime':         signal['vol_regime'],
        'fear_greed':         fear_greed_val,
        'm_vote':             m_vote,
        'm_conv':             m_conv,
        'b_vote':             b_vote,
        'b_conv':             b_conv,
        'b_veto':             1 if b_result.get("veto") else 0,
        'c_vote':             c_vote,
        'c_conv':             c_conv,
        'c_macro_lean':       c_result.get("macro_lean", "mixed"),
        'consensus':          consensus['consensus_result'],
        'consensus_reason':   consensus['consensus_reason'],
        'contracts':          consensus['contracts_decided'],
        'conflicts':          len(conflicts),
        'trade_taken':        trade_taken,
        'entry_price':        entry_price,
        'gross_pct':          gross_pct,
        'fees_pct':           fees_pct,
        'net_pct':            net_pct,
        'win':                win,
        'exit_reason':        exit_reason,
        'exit_hours':         exit_hours,
    })

    time.sleep(0.5)  # avoid rate limits

print("\n" + "=" * 70)
print("DELIBERATION COMPLETE — generating report...\n")

# ── PART 5: REPORT ─────────────────────────────────────────────

# ── Helpers ────────────────────────────────────────────────────
def avg(lst):
    clean = [x for x in lst if x is not None]
    return statistics.mean(clean) if clean else None

def pct_fmt(val):
    if val is None: return "N/A"
    return f"{val:+.3f}%"

def n_pct(n, total):
    if total == 0: return f"{n} (0.0%)"
    return f"{n} ({n/total*100:.1f}%)"

# ── Dataset stats ──────────────────────────────────────────────
min_ts = min(r['signal_timestamp'] for r in results_rows)
max_ts = max(r['signal_timestamp'] for r in results_rows)

# ── MAGI filter stats ──────────────────────────────────────────
approved = [r for r in results_rows if r['consensus'] == 'short']
filtered = [r for r in results_rows if r['consensus'] != 'short']
veto_count = sum(1 for r in results_rows if r['b_veto'])

# ── Trade outcomes — MAGI-approved signals ─────────────────────
trades = [r for r in approved if r['trade_taken'] == 1]
wins   = [r for r in trades if r['win'] == 1]
losses = [r for r in trades if r['win'] == 0]

wins_net   = [r['net_pct'] for r in wins if r['net_pct'] is not None]
losses_net = [r['net_pct'] for r in losses if r['net_pct'] is not None]
all_net    = [r['net_pct'] for r in trades if r['net_pct'] is not None]
all_gross  = [r['gross_pct'] for r in trades if r['gross_pct'] is not None]

target_hits = sum(1 for r in trades if r['exit_reason'] == 'target')
stop_hits   = sum(1 for r in trades if r['exit_reason'] == 'stop')
time_exits  = sum(1 for r in trades if r['exit_reason'] == 'time')

win_rate_magi = len(wins) / len(trades) * 100 if trades else 0.0

# ── Baseline — ALL 29 signals, no MAGI filter ─────────────────
# Win = outcome_4h > 0 (did short direction work?)
# Since outcome_4h is the 4h price return: for a short, win if ret < 0
# But the backtest_results.outcome_4h column is the raw 4h price move
# A short wins if outcome_4h < 0 (price fell)
baseline_wins  = [r for r in results_rows if r['ret_4h'] < 0]
baseline_rets  = [r['ret_4h'] for r in results_rows if r['ret_4h'] is not None]
baseline_win_rate = len(baseline_wins) / len(results_rows) * 100 if results_rows else 0.0

# Baseline net (deduct fees using avg eth_close)
baseline_net_rets = []
for r in results_rows:
    if r['ret_4h'] is not None and r['eth_close']:
        taker = 0.18
        nfa   = 0.30 / (0.1 * r['eth_close']) * 100
        f_pct = taker + nfa
        # For baseline: short P&L = -ret_4h (we short, price went down is good)
        gross = -r['ret_4h']  # flip sign: short gains when price falls
        net   = gross - f_pct
        baseline_net_rets.append(net)

# ── MAGI value-add ─────────────────────────────────────────────
approved_rets  = [-r['ret_4h'] for r in approved if r['ret_4h'] is not None]  # short direction
filtered_rets  = [-r['ret_4h'] for r in filtered if r['ret_4h'] is not None]

# ── Expectancy ─────────────────────────────────────────────────
def expectancy(net_list):
    if not net_list: return None
    w = [x for x in net_list if x >= 0]
    l = [x for x in net_list if x < 0]
    if not net_list: return 0
    wr = len(w) / len(net_list)
    avg_w = avg(w) or 0
    avg_l = avg(l) or 0
    return wr * avg_w + (1 - wr) * avg_l

# ── Conviction breakdown ───────────────────────────────────────
def conviction_group(trade_list, level: str):
    """Group by majority conviction across agents."""
    # Use melchior conviction as primary (it's the quantitative analyst)
    group = [r for r in trade_list if r['m_conv'] == level]
    nets  = [r['net_pct'] for r in group if r['net_pct'] is not None]
    ws    = [r for r in group if r['win'] == 1]
    return {
        'n':       len(group),
        'avg_net': avg(nets),
        'win_rate': len(ws) / len(group) * 100 if group else 0.0,
    }

high_grp = conviction_group(trades, 'high')
med_grp  = conviction_group(trades, 'medium')
low_grp  = conviction_group(trades, 'low')

# ── Best/worst trades ──────────────────────────────────────────
sorted_by_net = sorted(
    [r for r in trades if r['net_pct'] is not None],
    key=lambda x: x['net_pct']
)
worst_3 = sorted_by_net[:3]
best_3  = sorted_by_net[-3:][::-1]

# ── Print report ───────────────────────────────────────────────
print()
print("=" * 70)
print("  MAGI HISTORICAL BACKTEST — LIQUIDATION SQUEEZE SIGNALS")
print("=" * 70)

print("""
DATASET""")
print(f"  Signals replayed:           {len(results_rows)}")
print(f"  Date range:                 {min_ts} → {max_ts}")
print(f"  p90 short liq threshold:    ${p90_threshold:>14,.0f}")

print("""
MAGI FILTER""")
print(f"  Signals MAGI approved (SHORT): {n_pct(len(approved), total)}")
print(f"  Signals MAGI filtered (FLAT/veto): {n_pct(len(filtered), total)}")
print(f"  Balthasar veto count:          {veto_count}")

print("""
TRADE OUTCOMES — MAGI-APPROVED SIGNALS""")
print(f"  Trades taken:               {len(trades)}")
if trades:
    print(f"  Win rate:                   {win_rate_magi:.1f}%")
    print(f"  Avg gross return:           {pct_fmt(avg(all_gross))}")
    print(f"  Avg net return (after fees): {pct_fmt(avg(all_net))}")
    print(f"  Avg net return winners:     {pct_fmt(avg(wins_net))}")
    print(f"  Avg net return losers:      {pct_fmt(avg(losses_net))}")
    print(f"  Exit breakdown:             {target_hits} target hits, {stop_hits} stop hits, {time_exits} time exits")
else:
    print("  (No trades taken)")

print("""
BASELINE (ALL {} SIGNALS, NO MAGI FILTER)""".format(len(results_rows)))
# Win for a short = price fell (outcome_4h < 0)
print(f"  Win rate (outcome_4h < 0):  {baseline_win_rate:.1f}%")
print(f"  Avg 4h price return:        {pct_fmt(avg(baseline_rets))}")
# Avg net for shorts: -avg_4h_return - fees
avg_baseline_net = avg(baseline_net_rets) if baseline_net_rets else None
print(f"  Avg net return (deducting fees): {pct_fmt(avg_baseline_net)}")

print("""
MAGI VALUE-ADD""")
avg_approved = avg(approved_rets) if approved_rets else None
avg_filtered = avg(filtered_rets) if filtered_rets else None

print(f"  MAGI approved signals avg outcome_4h (short direction): {pct_fmt(avg_approved)}")
print(f"  MAGI filtered signals avg outcome_4h (short direction): {pct_fmt(avg_filtered)}")
if avg_approved is not None and avg_filtered is not None:
    diff = avg_approved - avg_filtered
    verdict = "does" if diff > 0 else "does NOT"
    print(f"  Difference:                 {diff:+.3f} pp — MAGI {verdict} add value as a filter")

print("""
EXPECTANCY (per trade, as % of notional)""")
exp_without = expectancy(baseline_net_rets) if baseline_net_rets else None
exp_with    = expectancy(all_net) if all_net else None
print(f"  Without MAGI:               {pct_fmt(exp_without)}")
print(f"  With MAGI filter:           {pct_fmt(exp_with)}")

print("""
CONVICTION ANALYSIS (MAGI-approved trades only)""")
def conv_line(label, grp):
    if grp['n'] == 0:
        print(f"  {label:<20} 0 trades")
        return
    print(f"  {label:<20} {grp['n']} trades, "
          f"avg net {pct_fmt(grp['avg_net'])}, "
          f"win rate {grp['win_rate']:.1f}%")

conv_line("High conviction:",   high_grp)
conv_line("Medium conviction:", med_grp)
conv_line("Low conviction:",    low_grp)

print("""
TOP 3 WORST TRADES (for risk awareness):""")
if worst_3:
    print(f"  {'Timestamp':<22} {'Gross%':>8} {'Net%':>8} {'Exit':>10}")
    print("  " + "-" * 54)
    for r in worst_3:
        print(f"  {r['signal_timestamp']:<22} "
              f"{r['gross_pct']:>+8.3f}% "
              f"{r['net_pct']:>+8.3f}% "
              f"{r['exit_reason'] or 'N/A':>10}")
else:
    print("  (No trades)")

print("""
TOP 3 BEST TRADES:""")
if best_3:
    print(f"  {'Timestamp':<22} {'Gross%':>8} {'Net%':>8} {'Exit':>10}")
    print("  " + "-" * 54)
    for r in best_3:
        print(f"  {r['signal_timestamp']:<22} "
              f"{r['gross_pct']:>+8.3f}% "
              f"{r['net_pct']:>+8.3f}% "
              f"{r['exit_reason'] or 'N/A':>10}")
else:
    print("  (No trades)")

print()
print("=" * 70)
print("  Results saved to magi_backtest_decisions table in observer.db")
print("=" * 70)

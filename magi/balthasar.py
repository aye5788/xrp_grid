# =============================================================
# MAGI SYSTEM — BALTHASAR-2
# Risk Manager — GPT-4o with Structured Outputs
# Reads account health + market context, returns a vote.
# Only agent with veto power.
# =============================================================

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from openai import OpenAI
from pydantic import BaseModel
from dotenv import load_dotenv
from coinbase.rest import RESTClient

from magi.prompts import BALTHASAR_SYSTEM

load_dotenv(os.path.expanduser("~/eth_observer/.env"))
logger = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/eth_observer/observer.db")
ET      = ZoneInfo("America/New_York")


# ── Pydantic schema for structured output ─────────────────────
class BalthasarVote(BaseModel):
    vote:        Literal["long", "short", "flat"]
    conviction:  Literal["high", "medium", "low"]
    reasoning:   str
    concerns:    list[str]
    veto:        bool
    veto_reason: Optional[str] = None


# ── Account data ───────────────────────────────────────────────
def get_account_health() -> dict:
    """
    Pull real-time account health from Coinbase API.
    Uses get_futures_balance_summary() — works with view-only key.
    SDK returns an object with attributes, not a plain dict.
    Access fields via getattr, not dict key indexing.
    """
    try:
        with open(os.path.expanduser("~/eth_observer/cdp_key.json")) as f:
            key = json.load(f)

        client = RESTClient(
            api_key=key["name"],
            api_secret=key["privateKey"]
        )
        result  = client.get_futures_balance_summary()
        summary = result.balance_summary

        def val(field):
            """
            Safely extract a numeric value from an SDK summary field.
            Most fields are plain dicts: {'value': '134.68', 'currency': 'USD'}
            Some fields are bare strings: '1000'
            """
            try:
                attr = getattr(summary, field, None)
                if attr is None:
                    return None
                if isinstance(attr, dict):
                    return float(attr.get("value", 0) or 0)
                return float(attr)
            except (TypeError, ValueError):
                return None

        # Extract margin window type from intraday measure dict
        try:
            intraday = getattr(summary, "intraday_margin_window_measure", None)
            if isinstance(intraday, dict):
                margin_window = intraday.get("margin_window_type", "UNKNOWN")
            else:
                margin_window = "UNKNOWN"
        except Exception:
            margin_window = "UNKNOWN"

        return {
            "futures_buying_power":      val("futures_buying_power"),
            "available_margin":          val("available_margin"),
            "liquidation_buffer_pct":    val("liquidation_buffer_percentage"),
            "liquidation_buffer_amount": val("liquidation_buffer_amount"),
            "unrealized_pnl":            val("unrealized_pnl"),
            "initial_margin_used":       val("initial_margin"),
            "total_usd_balance":         val("total_usd_balance"),
            "funding_pnl":               val("funding_pnl"),
            "margin_window_type":        margin_window,
            "api_status":                "ok"
        }

    except Exception as e:
        logger.error("Balthasar account health fetch failed: %s", e)
        return {"api_status": "error", "error": str(e)}


# ── Database data ──────────────────────────────────────────────
def get_24h_rows() -> list:
    """Pull last 24 hourly rows from the observer database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT timestamp, hour_of_day, day_of_week,
                   eth_close, eth_ret_pct, btc_ret_pct,
                   eth_btc_ratio_ret, vwap_24h, vwap_dev_pct,
                   vol_24h_std, vol_regime, avg_spread_pct,
                   funding_rate, signal_long, signal_short
            FROM hourly
            ORDER BY timestamp DESC
            LIMIT 24
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_recent_signal_history() -> list:
    """Pull last 5 completed signal events for drawdown assessment."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT timestamp, direction, eth_price,
                   outcome_1h, outcome_4h, win_1h, win_4h
            FROM signal_events
            WHERE outcome_1h IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 5
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Pre-computation layer ──────────────────────────────────────
def compute_time_analysis() -> dict:
    """
    Compute time window analysis relative to ET thresholds.
    Results are passed to GPT as clearly labeled context.
    GPT makes the final judgment — this is information, not a gate.
    """
    now_et  = datetime.now(ET)
    hour    = now_et.hour
    minute  = now_et.minute
    dow     = now_et.weekday()  # 0=Mon, 6=Sun
    is_weekday = dow < 5
    time_str = now_et.strftime("%I:%M %p ET")

    current_minutes = hour * 60 + minute

    def mins_to(h, m):
        return (h * 60 + m) - current_minutes

    to_300 = mins_to(15, 0)
    to_330 = mins_to(15, 30)
    to_400 = mins_to(16, 0)
    to_fri_close = mins_to(17, 0) if dow == 4 else None

    # Determine status label — GPT reads this and decides
    # Only flag hard veto on weekdays when margin cliff is real
    if dow == 4 and 17*60 <= current_minutes < 18*60:
        status = "HARD VETO CONDITION — CDE CLOSED (Friday 5-6 PM ET)"
    elif is_weekday and -30 <= to_400 <= 0 and to_330 <= 0:
        status = "HARD VETO CONDITION — within 3:30-4:00 PM ET overnight cliff window"
    elif is_weekday and to_330 <= 30 and to_330 >= 0:
        status = "WARNING — approaching 3:30 PM ET threshold"
    elif is_weekday and to_300 <= 60 and to_300 >= 0:
        status = "CAUTION — approaching 3:00 PM ET threshold"
    elif not is_weekday:
        status = "CLEAR — weekend, no margin window constraints"
    else:
        status = "CLEAR"

    return {
        "current_time_et":   time_str,
        "day_of_week":       ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][dow],
        "is_weekday":        is_weekday,
        "mins_to_300pm":     to_300,
        "mins_to_330pm":     to_330,
        "mins_to_400pm":     to_400,
        "mins_to_fri_close": to_fri_close,
        "status":            status,
    }


def compute_account_analysis(acct: dict) -> dict:
    """
    Pre-compute account health with clear status labels.
    GPT reads these labels and makes the judgment call.
    This is information preparation, not a decision gate.
    """
    if acct.get("api_status") == "error":
        return {
            "status":    "API ERROR — account data unavailable",
            "error":     acct.get("error", "unknown"),
        }

    bp  = acct.get("futures_buying_power", 0) or 0
    buf = acct.get("liquidation_buffer_pct", 0) or 0
    pnl = acct.get("unrealized_pnl", 0) or 0
    mgn = acct.get("margin_window_type", "UNKNOWN")

    intraday_margin  = 23.00
    overnight_margin = 57.00
    max_stop_loss    = 3.48

    # Status labels for GPT — clear flags, GPT decides action
    if bp < 40:
        status = "HARD VETO CONDITION — buying power $%.2f below $40 minimum" % bp
    elif buf < 200:
        status = "HARD VETO CONDITION — liquidation buffer %.0f%% below 200%%" % buf
    elif mgn == "FCM_MARGIN_WINDOW_TYPE_OVERNIGHT" and bp < 80:
        status = "HARD VETO CONDITION — overnight window active, buying power $%.2f < $80" % bp
    elif bp < 60:
        status = "CRITICAL — buying power below $60"
    elif bp < 100:
        status = "THIN — buying power below $100"
    elif buf < 400:
        status = "MONITOR — liquidation buffer below 400%%"
    else:
        status = "HEALTHY"

    contracts_supportable = int(bp / (intraday_margin * 1.5))

    return {
        "futures_buying_power":          f"${bp:.2f}",
        "available_margin":              f"${acct.get('available_margin', 0):.2f}",
        "liquidation_buffer_pct":        f"{buf:.0f}%",
        "unrealized_pnl":                f"${pnl:.2f}",
        "initial_margin_used":           f"${acct.get('initial_margin_used', 0):.2f}",
        "margin_window":                 mgn,
        "intraday_margin_per_contract":  f"${intraday_margin:.2f}",
        "overnight_margin_per_contract": f"${overnight_margin:.2f}",
        "max_stop_loss_per_contract":    f"${max_stop_loss:.2f}",
        "contracts_safely_supportable":  contracts_supportable,
        "status":                        status,
    }


def compute_friction_analysis(avg_spread_pct: float,
                               eth_price: float) -> dict:
    """
    Pre-compute total round-trip friction.
    Arithmetic done in Python — result labeled clearly for GPT.
    """
    if avg_spread_pct is None:
        return {"status": "UNKNOWN — spread data missing"}

    notional       = 0.1 * eth_price
    maker_fee_pct  = 0.085
    nfa_fee        = 0.15
    nfa_pct        = (nfa_fee / notional) * 100
    total_friction = (maker_fee_pct * 2) + nfa_pct + avg_spread_pct
    max_acceptable = 0.40
    headroom       = max_acceptable - total_friction

    if total_friction > max_acceptable:
        status = "HARD VETO CONDITION — friction %.3f%% exceeds maximum %.2f%%" % (
            total_friction, max_acceptable)
    elif total_friction > 0.32:
        status = "ELEVATED (%.3f%%)" % total_friction
    elif total_friction > 0.27:
        status = "MODERATE (%.3f%%)" % total_friction
    else:
        status = "ACCEPTABLE (%.3f%%)" % total_friction

    return {
        "avg_spread_pct":        f"{avg_spread_pct:.4f}%",
        "maker_fee_both_sides":  f"{maker_fee_pct * 2:.3f}%",
        "nfa_clearing_pct":      f"{nfa_pct:.3f}% (${nfa_fee:.2f} on ${notional:.0f} notional)",
        "total_friction":        f"{total_friction:.3f}%",
        "max_acceptable":        f"{max_acceptable:.2f}%",
        "headroom":              f"{headroom:.3f}%",
        "status":                status,
    }


def compute_drawdown_analysis(signal_history: list,
                               buying_power: float) -> dict:
    """
    Pre-compute drawdown assessment from recent signal history.
    Estimates dollar losses from outcome percentages.
    """
    if not signal_history:
        return {
            "recent_signals":     0,
            "wins":               0,
            "losses":             0,
            "consecutive_losses": 0,
            "estimated_losses":   "$0.00",
            "noted_concern_at":   "$7.00 with buying power < $100",
            "strong_concern_at":  "$14.00 or 4+ consecutive losses",
            "hard_veto_at":       "buying power < $40 (see account analysis)",
            "status":             "NO HISTORY — insufficient data for drawdown assessment",
        }

    estimated_losses = 0.0
    loss_streak      = 0

    for sig in signal_history:
        outcome = sig.get("outcome_1h")
        eth_p   = sig.get("eth_price", 2300)
        win     = sig.get("win_1h")

        if outcome is not None and win == 0:
            loss_dollar       = abs(outcome / 100) * 0.1 * eth_p
            estimated_losses += loss_dollar
            loss_streak      += 1
        else:
            loss_streak = 0

    if estimated_losses > 14 or loss_streak >= 4:
        status = "STRONG CONCERN — losses $%.2f, streak %d" % (
            estimated_losses, loss_streak)
    elif estimated_losses > 7 and buying_power < 100:
        status = "NOTED CONCERN — losses $%.2f with thin buying power" % estimated_losses
    else:
        status = "ACCEPTABLE — estimated losses $%.2f" % estimated_losses

    wins  = sum(1 for s in signal_history if s.get("win_1h") == 1)
    total = len(signal_history)

    return {
        "recent_signals":     total,
        "wins":               wins,
        "losses":             total - wins,
        "consecutive_losses": loss_streak,
        "estimated_losses":   f"${estimated_losses:.2f}",
        "noted_concern_at":   "$7.00 with buying power < $100",
        "strong_concern_at":  "$14.00 or 4+ consecutive losses",
        "hard_veto_at":       "buying power < $40 (see account analysis)",
        "status":             status,
    }


# ── ARCHIVED PYTHON SAFETY GATE ───────────────────────────────
# This block was the original pre-LLM hard veto interceptor.
# It is disabled by default — GPT-4o makes all decisions.
# Re-enable by uncommenting if GPT consistently fails to catch
# hard mechanical conditions (exchange closed, liquidation risk).
#
# USAGE: uncomment the call to _python_safety_gate() in assess()
# and the function itself below.
#
# def _python_safety_gate(time_anal, acct_anal, friction):
#     """
#     Last-resort Python safety gate.
#     Only fires if GPT is not being used or has failed entirely.
#     Checks the three most critical hard veto conditions:
#     1. Exchange closed
#     2. Account below minimum threshold
#     3. Friction exceeds maximum
#     Returns (should_veto: bool, reason: str)
#     """
#     for anal in [time_anal, acct_anal, friction]:
#         status = anal.get("status", "")
#         if "HARD VETO CONDITION" in str(status):
#             return True, status
#     return False, None
# ── END ARCHIVED GATE ─────────────────────────────────────────


class Balthasar:
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model  = "gpt-4o"
        self._last_input_tokens = 0
        self._last_output_tokens = 0

    def build_context(self, rows, acct, time_anal,
                      acct_anal, friction, drawdown) -> str:
        """
        Build Balthasar's full context string with pre-computed
        analysis blocks clearly labeled.
        GPT reads all of this and makes every decision.
        """
        def fmt(val, decimals=4):
            if val is None: return "NULL"
            try: return round(float(val), decimals)
            except: return str(val)

        latest = rows[0] if rows else {}
        now    = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        time_block = f"""
PRE-COMPUTED: TIME WINDOW ANALYSIS
- Current time:          {time_anal['current_time_et']} ({time_anal['day_of_week']})
- Is weekday:            {time_anal['is_weekday']}
- Minutes to 3:00 PM ET: {time_anal['mins_to_300pm']}
- Minutes to 3:30 PM ET: {time_anal['mins_to_330pm']}
- Minutes to 4:00 PM ET: {time_anal['mins_to_400pm']}
- Friday close risk:     {time_anal['mins_to_fri_close'] if time_anal['mins_to_fri_close'] is not None else 'N/A'}
- STATUS: {time_anal['status']}
"""

        acct_block = f"""
PRE-COMPUTED: ACCOUNT HEALTH ANALYSIS
- Futures buying power:      {acct_anal.get('futures_buying_power', 'N/A')}
- Available margin:          {acct_anal.get('available_margin', 'N/A')}
- Liquidation buffer:        {acct_anal.get('liquidation_buffer_pct', 'N/A')}
- Unrealized P&L:            {acct_anal.get('unrealized_pnl', 'N/A')}
- Initial margin in use:     {acct_anal.get('initial_margin_used', 'N/A')}
- Margin window:             {acct_anal.get('margin_window', 'N/A')}
- Contracts safely supportable: {acct_anal.get('contracts_safely_supportable', 'N/A')}
- Intraday margin/contract:  {acct_anal.get('intraday_margin_per_contract', 'N/A')}
- Overnight margin/contract: {acct_anal.get('overnight_margin_per_contract', 'N/A')}
- Max stop loss/contract:    {acct_anal.get('max_stop_loss_per_contract', 'N/A')}
- STATUS: {acct_anal.get('status', 'UNKNOWN')}
"""

        friction_block = f"""
PRE-COMPUTED: FRICTION ANALYSIS
- Current avg spread:        {friction.get('avg_spread_pct', 'NULL')}
- Maker fee (both sides):    {friction.get('maker_fee_both_sides', 'NULL')}
- NFA/clearing fee:          {friction.get('nfa_clearing_pct', 'NULL')}
- Total round-trip friction: {friction.get('total_friction', 'NULL')}
- Maximum acceptable:        {friction.get('max_acceptable', '0.40%')}
- Headroom remaining:        {friction.get('headroom', 'NULL')}
- STATUS: {friction.get('status', 'UNKNOWN')}
"""

        drawdown_block = f"""
PRE-COMPUTED: DRAWDOWN ANALYSIS
- Recent completed signals:  {drawdown['recent_signals']}
- Wins / Losses:             {drawdown.get('wins', 0)} / {drawdown.get('losses', 0)}
- Consecutive losses:        {drawdown.get('consecutive_losses', 0)}
- Estimated recent losses:   {drawdown['estimated_losses']}
- Noted concern threshold:   {drawdown['noted_concern_at']}
- Strong concern threshold:  {drawdown['strong_concern_at']}
- STATUS: {drawdown['status']}
"""

        market_block = f"""
CURRENT MARKET STATE — {now}
- ETH price:         ${fmt(latest.get('eth_close'), 2)}
- ETH ret (1h):      {fmt(latest.get('eth_ret_pct'))}%
- BTC ret (1h):      {fmt(latest.get('btc_ret_pct'))}%
- ETH/BTC ratio ret: {fmt(latest.get('eth_btc_ratio_ret'))}%
- VWAP dev:          {fmt(latest.get('vwap_dev_pct'))}%
- Vol regime:        {latest.get('vol_regime') or 'NULL'}
- Funding rate:      {fmt(latest.get('funding_rate'), 8)}
- Hour UTC:          {latest.get('hour_of_day') or 'NULL'}
- Day of week:       {latest.get('day_of_week') or 'NULL'} (0=Mon)
"""

        header = (
            f"\n{'Timestamp':<20} {'ETH':>8} {'ETH%':>7} "
            f"{'BTC%':>7} {'VWAP dev%':>10} {'Vol':>8} {'Funding':>12}\n"
            + "─" * 76 + "\n"
        )
        row_lines = ""
        for r in rows:
            row_lines += (
                f"{str(r.get('timestamp','')):<20} "
                f"${fmt(r.get('eth_close'),2):>7} "
                f"{fmt(r.get('eth_ret_pct'),3):>7}% "
                f"{fmt(r.get('btc_ret_pct'),3):>7}% "
                f"{fmt(r.get('vwap_dev_pct'),3):>9}% "
                f"{str(r.get('vol_regime') or 'N/A'):>8} "
                f"{fmt(r.get('funding_rate'),8):>12}\n"
            )
        history = f"\nLAST 24 HOURLY ROWS (most recent first):{header}{row_lines}"

        return (
            time_block + acct_block + friction_block +
            drawdown_block + market_block + history +
            "\nAssess these conditions and return your vote."
        ).strip()

    def assess(self, rows: list = None) -> dict:
        """
        Main entry point.
        Pulls account data, pre-computes analysis blocks,
        then passes everything to GPT-4o for judgment.
        GPT makes all decisions including hard veto conditions.
        Python only catches total GPT API failure.
        """
        try:
            if rows is None:
                rows = get_24h_rows()

            # Pull real-time account health
            acct = get_account_health()

            # Get latest market data for pre-computation
            latest    = rows[0] if rows else {}
            spread    = latest.get("avg_spread_pct", 0.05) or 0.05
            eth_price = latest.get("eth_close", 2300) or 2300
            bp        = acct.get("futures_buying_power", 134) or 134

            # Pre-compute all analysis blocks — information for GPT
            time_anal = compute_time_analysis()
            acct_anal = compute_account_analysis(acct)
            friction  = compute_friction_analysis(spread, eth_price)
            sig_hist  = get_recent_signal_history()
            drawdown  = compute_drawdown_analysis(sig_hist, bp)

            # ── ARCHIVED PYTHON SAFETY GATE (disabled) ──────────
            # Uncomment to re-enable pre-LLM hard veto interception:
            # should_veto, veto_reason = _python_safety_gate(
            #     time_anal, acct_anal, friction
            # )
            # if should_veto:
            #     logger.warning("Python safety gate fired: %s", veto_reason)
            #     return {
            #         "agent": "balthasar", "status": "ok",
            #         "vote": "flat", "conviction": "high",
            #         "reasoning": f"Safety gate: {veto_reason}",
            #         "concerns": [veto_reason],
            #         "veto": True, "veto_reason": veto_reason,
            #     }
            # ── END ARCHIVED GATE ────────────────────────────────

            # Build context and call GPT-4o with structured output
            context = self.build_context(
                rows, acct, time_anal, acct_anal, friction, drawdown
            )

            completion = self.client.beta.chat.completions.parse(
                model=self.model,
                max_tokens=600,
                temperature=0.1,
                messages=[
                    {"role": "system", "content": BALTHASAR_SYSTEM},
                    {"role": "user",   "content": context},
                ],
                response_format=BalthasarVote,
            )

            self._last_input_tokens = completion.usage.prompt_tokens
            self._last_output_tokens = completion.usage.completion_tokens

            vote_obj = completion.choices[0].message.parsed

            # Enforce veto_reason consistency
            if vote_obj.veto and not vote_obj.veto_reason:
                vote_obj.veto_reason = "Balthasar vetoed — see reasoning"
            if not vote_obj.veto:
                vote_obj.veto_reason = None

            vote = vote_obj.model_dump()

            logger.info(
                "Balthasar vote: %s | conviction: %s | veto: %s | %s",
                vote["vote"].upper(),
                vote["conviction"],
                vote["veto"],
                vote["reasoning"][:80],
            )

            return {
                "agent": "balthasar", "status": "ok", **vote,
                "_input_tokens": self._last_input_tokens,
                "_output_tokens": self._last_output_tokens,
                "_account_health_status": acct_anal.get("status", "unknown"),
            }

        except Exception as e:
            # Only Python-level fallback — GPT API failure only
            logger.error("Balthasar error: %s", e)
            return self._error_vote(str(e))

    def _error_vote(self, reason: str) -> dict:
        """
        Safe fallback — only fires on total GPT API failure.
        Not a decision gate — purely an error handler.
        """
        return {
            "agent":           "balthasar",
            "status":          "error",
            "vote":            "flat",
            "conviction":      "low",
            "reasoning":       f"Balthasar API error — defaulting to flat. Reason: {reason}",
            "concerns":        ["GPT API failure — vote unreliable, do not trade"],
            "veto":            False,
            "veto_reason":     None,
            "_input_tokens":   0,
            "_output_tokens":  0,
        }

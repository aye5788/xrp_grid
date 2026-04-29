# =============================================================
# MAGI SYSTEM — MELCHIOR-1
# Quantitative Analyst — Claude Sonnet 4.6
# Reads 24h of price and microstructure signals, returns a vote.
# =============================================================

import json
import logging
import os
import sqlite3
from datetime import datetime

import anthropic
from dotenv import load_dotenv

from magi.prompts import MELCHIOR_SYSTEM

load_dotenv(os.path.expanduser("~/eth_observer/.env"))
logger = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/eth_observer/observer.db")


def get_24h_rows() -> list:
    """
    Pull the last 24 hourly rows from the database.
    Returns list of dicts, most recent first.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT
                timestamp, hour_of_day, day_of_week,
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


def compute_trends(rows: list) -> dict:
    """
    Compute simple trend summaries from the last 24h of rows.
    These are passed to Melchior as pre-digested context.
    All computed in Python — no LLM needed for this math.
    """
    if not rows:
        return {}

    def safe_float(val):
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    # VWAP deviation trend
    vwap_devs = [safe_float(r.get('vwap_dev_pct')) for r in rows
                 if safe_float(r.get('vwap_dev_pct')) is not None]
    if len(vwap_devs) >= 3:
        recent_avg = sum(vwap_devs[:3]) / 3
        older_avg  = sum(vwap_devs[-3:]) / 3
        vwap_trend = "improving" if abs(recent_avg) < abs(older_avg) else "worsening"
    else:
        vwap_trend = "insufficient_data"

    # BTC direction last 6 hours
    btc_rets = [safe_float(r.get('btc_ret_pct')) for r in rows[:6]
                if safe_float(r.get('btc_ret_pct')) is not None]
    if btc_rets:
        btc_sum = sum(btc_rets)
        btc_direction = "up" if btc_sum > 0.3 else "down" if btc_sum < -0.3 else "flat"
        btc_6h_total  = round(btc_sum, 3)
    else:
        btc_direction = "unknown"
        btc_6h_total  = None

    # Vol regime
    regimes = [r.get('vol_regime') for r in rows[:6] if r.get('vol_regime')]
    if regimes:
        vol_stable = len(set(regimes)) == 1
        vol_regime_summary = regimes[0] + (" (stable)" if vol_stable else " (transitioning)")
    else:
        vol_regime_summary = "unknown"

    # Funding direction
    fundings = [safe_float(r.get('funding_rate')) for r in rows[:6]
                if safe_float(r.get('funding_rate')) is not None]
    if len(fundings) >= 2:
        funding_delta = fundings[0] - fundings[-1]
        funding_dir = ("rising" if funding_delta > 0.000001
                       else "falling" if funding_delta < -0.000001
                       else "stable")
    else:
        funding_dir = "unknown"

    # Price range
    prices = [safe_float(r.get('eth_close')) for r in rows
              if safe_float(r.get('eth_close')) is not None]
    price_high = max(prices) if prices else None
    price_low  = min(prices) if prices else None
    price_now  = prices[0] if prices else None

    if price_high and price_low and price_high != price_low:
        range_position = round(
            (price_now - price_low) / (price_high - price_low) * 100, 1
        )
    else:
        range_position = None

    long_signals  = sum(1 for r in rows[:6] if r.get('signal_long'))
    short_signals = sum(1 for r in rows[:6] if r.get('signal_short'))

    # ETH 12h and 24h cumulative return
    eth_rets = [safe_float(r.get('eth_ret_pct')) for r in rows
                if safe_float(r.get('eth_ret_pct')) is not None]
    eth_12h_ret = round(sum(eth_rets[:12]), 3) if len(eth_rets) >= 12 else None
    eth_24h_ret = round(sum(eth_rets[:24]), 3) if len(eth_rets) >= 24 else None

    return {
        "vwap_dev_trend":           vwap_trend,
        "btc_6h_direction":         btc_direction,
        "btc_6h_total_pct":         btc_6h_total,
        "vol_regime_summary":       vol_regime_summary,
        "funding_direction":        funding_dir,
        "price_24h_high":           price_high,
        "price_24h_low":            price_low,
        "price_range_position_pct": range_position,
        "long_signals_last_6h":     long_signals,
        "short_signals_last_6h":    short_signals,
        "eth_12h_ret":              eth_12h_ret,
        "eth_24h_ret":              eth_24h_ret,
    }


class Melchior:
    def __init__(self):
        self.client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )
        self.model = "claude-sonnet-4-6"
        self._last_input_tokens = 0
        self._last_output_tokens = 0

    def build_context(self, rows: list, trends: dict) -> str:
        """
        Format 24h of rows plus pre-computed trends into a
        readable context string for Melchior.
        Most recent row first. Null values shown explicitly.
        """
        def fmt(val, decimals=4):
            if val is None:
                return "NULL"
            try:
                return round(float(val), decimals)
            except (TypeError, ValueError):
                return str(val)

        now    = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
        latest = rows[0] if rows else {}

        current = f"""
CURRENT STATE — {now}
- ETH price:         ${fmt(latest.get('eth_close'), 2)}
- ETH ret (1h):      {fmt(latest.get('eth_ret_pct'))}%
- BTC ret (1h):      {fmt(latest.get('btc_ret_pct'))}%
- ETH/BTC ratio ret: {fmt(latest.get('eth_btc_ratio_ret'))}%
- VWAP (24h):        ${fmt(latest.get('vwap_24h'), 2)}
- VWAP dev:          {fmt(latest.get('vwap_dev_pct'))}%
- Vol regime:        {latest.get('vol_regime') or 'NULL'}
- Vol std (24h):     {fmt(latest.get('vol_24h_std'))}%
- Avg spread:        {fmt(latest.get('avg_spread_pct'))}%
- Funding rate:      {fmt(latest.get('funding_rate'), 8)}
- Hour (UTC):        {latest.get('hour_of_day') or 'NULL'}
- Day of week:       {latest.get('day_of_week') or 'NULL'} (0=Mon, 6=Sun)
"""

        trend_section = f"""
24H TREND SUMMARY (pre-computed):
- VWAP deviation trend:     {trends.get('vwap_dev_trend', 'unknown')}
  (improving = price moving back toward VWAP)
- BTC direction (last 6h):  {trends.get('btc_6h_direction', 'unknown')}
  (6h cumulative: {trends.get('btc_6h_total_pct', 'NULL')}%)
- ETH return (12h):         {trends.get('eth_12h_ret', 'NULL')}%
- ETH return (24h):         {trends.get('eth_24h_ret', 'NULL')}%
- Vol regime:               {trends.get('vol_regime_summary', 'unknown')}
- Funding direction:        {trends.get('funding_direction', 'unknown')}
- 24h price range:          ${fmt(trends.get('price_24h_low'), 2)} — ${fmt(trends.get('price_24h_high'), 2)}
- Position in 24h range:    {trends.get('price_range_position_pct', 'NULL')}%
  (0% = at 24h low, 100% = at 24h high)
- Signal flags (last 6h):   {trends.get('long_signals_last_6h', 0)} long, {trends.get('short_signals_last_6h', 0)} short
"""

        header = (f"\n{'Timestamp':<20} {'ETH':>8} {'ETH%':>7} "
                  f"{'BTC%':>7} {'VWAP dev%':>10} {'Vol':>8} "
                  f"{'Funding':>12}\n" + "─" * 76 + "\n")

        row_lines = ""
        for r in rows:
            row_lines += (
                f"{str(r.get('timestamp', '')):<20} "
                f"${fmt(r.get('eth_close'), 2):>7} "
                f"{fmt(r.get('eth_ret_pct'), 3):>7}% "
                f"{fmt(r.get('btc_ret_pct'), 3):>7}% "
                f"{fmt(r.get('vwap_dev_pct'), 3):>9}% "
                f"{str(r.get('vol_regime') or 'N/A'):>8} "
                f"{fmt(r.get('funding_rate'), 8):>12}\n"
            )

        history = f"\nLAST 24 HOURLY ROWS (most recent first):{header}{row_lines}"

        return (current + trend_section + history +
                "\nAssess these signals and return your vote.").strip()

    def assess(self, rows: list = None, signals: dict = None) -> dict:
        """
        Main entry point.

        Accepts either:
        - rows: list of hourly dicts from the database (preferred)
        - signals: single dict (used by synthetic test harness)

        Always returns a valid vote dict even if the API call fails.
        """
        try:
            if rows is not None:
                trends  = compute_trends(rows)
                context = self.build_context(rows, trends)
            elif signals is not None:
                trends  = {}
                context = self._build_single_row_context(signals)
            else:
                return self._error_vote("No data provided to assess()")

            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                temperature=0.1,
                system=MELCHIOR_SYSTEM,
                messages=[{"role": "user", "content": context}]
            )

            self._last_input_tokens = response.usage.input_tokens
            self._last_output_tokens = response.usage.output_tokens

            raw = response.content[0].text.strip()
            logger.debug("Melchior raw response: %s", raw)

            # Strip markdown code fences if Claude adds them
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            vote = json.loads(raw)

            required = ["vote", "conviction", "reasoning", "concerns", "veto"]
            for field in required:
                if field not in vote:
                    raise ValueError(f"Missing field in vote: {field}")

            if vote["vote"] not in ["long", "short", "flat"]:
                raise ValueError(f"Invalid vote value: {vote['vote']}")
            if vote["conviction"] not in ["high", "medium", "low"]:
                raise ValueError(f"Invalid conviction: {vote['conviction']}")
            vote["veto"] = False

            logger.info(
                "Melchior vote: %s | conviction: %s | %s",
                vote["vote"].upper(),
                vote["conviction"],
                vote["reasoning"][:80]
            )

            return {
                "agent": "melchior", "status": "ok", **vote,
                "_input_tokens": self._last_input_tokens,
                "_output_tokens": self._last_output_tokens,
            }

        except json.JSONDecodeError as e:
            logger.error("Melchior JSON parse error: %s", e)
            return self._error_vote(f"JSON parse error: {e}")

        except Exception as e:
            logger.error("Melchior error: %s", e)
            return self._error_vote(str(e))

    def _build_single_row_context(self, signals: dict) -> str:
        """
        Backward-compatible single-row context builder.
        Used by the synthetic test harness only.
        """
        def fmt(val, decimals=4):
            if val is None:
                return "NULL"
            try:
                return round(float(val), decimals)
            except (TypeError, ValueError):
                return str(val)

        return f"""
CURRENT SIGNALS — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

PRICE & MICROSTRUCTURE:
- eth_close:         {fmt(signals.get('eth_close'), 2)}
- eth_ret_pct:       {fmt(signals.get('eth_ret_pct'))}%
- btc_ret_pct:       {fmt(signals.get('btc_ret_pct'))}%
- eth_btc_ratio_ret: {fmt(signals.get('eth_btc_ratio_ret'))}%
- vwap_24h:          {fmt(signals.get('vwap_24h'), 2)}
- vwap_dev_pct:      {fmt(signals.get('vwap_dev_pct'))}%
- vol_24h_std:       {fmt(signals.get('vol_24h_std'))}%
- vol_regime:        {signals.get('vol_regime') or 'NULL'}
- avg_spread_pct:    {fmt(signals.get('avg_spread_pct'))}%
- funding_rate:      {fmt(signals.get('funding_rate'), 8)}

CONTEXT:
- hour_of_day:       {signals.get('hour_of_day') or 'NULL'} UTC
- day_of_week:       {signals.get('day_of_week') or 'NULL'} (0=Mon, 6=Sun)
- trigger_reason:    {signals.get('trigger_reason') or 'scheduled'}

Note: Single-row context — no 24h history available.
Assess these signals and return your vote.
""".strip()

    def _error_vote(self, reason: str) -> dict:
        return {
            "agent":           "melchior",
            "status":          "error",
            "vote":            "flat",
            "conviction":      "low",
            "reasoning":       f"Melchior error — defaulting to flat. Reason: {reason}",
            "concerns":        ["agent error — vote unreliable"],
            "veto":            False,
            "_input_tokens":   0,
            "_output_tokens":  0,
        }

# =============================================================
# MAGI SYSTEM — CASPER-3
# Macro Context Analyst — Gemini 2.5 Pro
# Uses the new google-genai SDK (not deprecated google.generativeai)
# Reads macro environment + crypto market structure, returns vote.
# Never vetoes — equal vote in consensus gate.
# =============================================================

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

from google import genai
from google.genai import types
import requests
from dotenv import load_dotenv

from magi.prompts import CASPER_SYSTEM

load_dotenv(os.path.expanduser("~/eth_observer/.env"))
logger = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/eth_observer/observer.db")


# ── Data fetchers ──────────────────────────────────────────────

def get_coingecko_data() -> dict:
    """
    Fetch global crypto market data from CoinGecko free API.
    No API key required.
    """
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=15
        )
        d = r.json()["data"]
        return {
            "btc_dominance_pct":         round(d["market_cap_percentage"]["btc"], 2),
            "eth_dominance_pct":         round(d["market_cap_percentage"]["eth"], 2),
            "total_market_cap_usd":      d["total_market_cap"]["usd"],
            "total_volume_24h_usd":      d["total_volume"]["usd"],
            "market_cap_change_24h_pct": round(
                d["market_cap_change_percentage_24h_usd"], 2
            ),
            "status": "ok"
        }
    except Exception as e:
        logger.warning("CoinGecko fetch failed: %s", e)
        return {"status": "error", "error": str(e)}


def get_fear_greed() -> dict:
    """
    Fetch Fear & Greed Index from Alternative.me.
    No API key required. Returns last 3 days to compute trend.
    """
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=3",
            timeout=15
        )
        data    = r.json()["data"]
        current = data[0]
        values  = [int(d["value"]) for d in data]

        if len(values) >= 2:
            diff = values[0] - values[-1]
            if diff > 5:
                trend = "IMPROVING"
            elif diff < -5:
                trend = "DETERIORATING"
            else:
                trend = "STABLE"
        else:
            trend = "UNKNOWN"

        return {
            "fear_greed_index":          int(current["value"]),
            "fear_greed_classification": current["value_classification"],
            "fear_greed_3d_trend":       trend,
            "fear_greed_3d_values":      values,
            "status": "ok"
        }
    except Exception as e:
        logger.warning("Fear & Greed fetch failed: %s", e)
        return {"status": "error", "error": str(e)}


def get_dxy_data() -> dict:
    """
    Fetch DXY (US Dollar Index) from yfinance.
    Returns current level, direction, and recent % change.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker("DX-Y.NYB")
        hist   = ticker.history(period="5d")

        if hist.empty or len(hist) < 2:
            return {"status": "error", "error": "insufficient data"}

        latest = float(hist["Close"].iloc[-1])
        prior  = float(hist["Close"].iloc[-2])
        change = round((latest / prior - 1) * 100, 3)

        return {
            "dxy_close":      round(latest, 2),
            "dxy_prior":      round(prior, 2),
            "dxy_change_pct": change,
            "dxy_direction":  "UP" if change > 0 else "DOWN",
            "status": "ok"
        }
    except Exception as e:
        logger.warning("DXY fetch failed: %s", e)
        return {"status": "error", "error": str(e)}


def get_yield_data() -> dict:
    """
    Fetch 10-year Treasury yield from FRED API.
    Requires FRED_API_KEY in .env file.
    """
    try:
        fred_key = os.getenv("FRED_API_KEY")
        if not fred_key:
            return {"status": "error", "error": "FRED_API_KEY not set"}

        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id":     "DGS10",
                "api_key":       fred_key,
                "file_type":     "json",
                "sort_order":    "desc",
                "limit":         5,
                "observation_end": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            },
            timeout=15
        )
        obs   = r.json()["observations"]
        valid = [o for o in obs if o["value"] != "."]

        if len(valid) < 2:
            return {"status": "error", "error": "insufficient valid observations"}

        current = float(valid[0]["value"])
        prior   = float(valid[1]["value"])
        change  = round(current - prior, 3)

        return {
            "yield_10y":        round(current, 3),
            "yield_10y_prior":  round(prior, 3),
            "yield_change_bps": round(change * 100, 1),
            "yield_direction":  "RISING" if change > 0 else "FALLING",
            "yield_date":       valid[0]["date"],
            "status": "ok"
        }
    except Exception as e:
        logger.warning("FRED yield fetch failed: %s", e)
        return {"status": "error", "error": str(e)}


def get_observer_context() -> dict:
    """
    Pull macro-relevant context from the observer database.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT timestamp, hour_of_day, day_of_week,
                   eth_close, btc_close, btc_ret_pct,
                   vol_regime, premium_pct, premium_change
            FROM hourly
            ORDER BY timestamp DESC
            LIMIT 6
        """).fetchall()
        conn.close()

        if not rows:
            return {"status": "error", "error": "no hourly data"}

        latest = dict(rows[0])

        btc_rets = [r["btc_ret_pct"] for r in rows
                    if r["btc_ret_pct"] is not None]
        if btc_rets:
            btc_6h_sum = sum(btc_rets)
            btc_6h_dir = ("up" if btc_6h_sum > 0.3
                          else "down" if btc_6h_sum < -0.3
                          else "flat")
        else:
            btc_6h_dir = "unknown"

        premiums = [r["premium_pct"] for r in rows
                    if r["premium_pct"] is not None]
        if len(premiums) >= 2:
            prem_dir = "RISING" if premiums[0] > premiums[-1] else "FALLING"
        else:
            prem_dir = "UNKNOWN"

        days = ["Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday"]

        return {
            "premium_pct":       latest.get("premium_pct"),
            "premium_direction": prem_dir,
            "vol_regime":        latest.get("vol_regime"),
            "btc_6h_direction":  btc_6h_dir,
            "hour_of_day_utc":   latest.get("hour_of_day"),
            "day_of_week":       days[latest.get("day_of_week", 0)],
            "eth_price":         latest.get("eth_close"),
            "status": "ok"
        }
    except Exception as e:
        logger.warning("Observer context fetch failed: %s", e)
        return {"status": "error", "error": str(e)}


# ── Context builder ────────────────────────────────────────────

def build_context(
    coingecko: dict,
    fear_greed: dict,
    dxy: dict,
    yields: dict,
    observer: dict,
) -> str:
    """
    Format all macro data sources into a clean context string.
    Each input includes interpretation guide for Gemini.
    """
    def fmt(val, decimals=2):
        if val is None:
            return "NULL"
        try:
            return round(float(val), decimals)
        except Exception:
            return str(val)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    macro_block = f"""
MACRO CONDITIONS — {now}

Dollar (DXY):
- Current level:  {fmt(dxy.get('dxy_close'))}
- Recent change:  {fmt(dxy.get('dxy_change_pct'), 3)}%
- Direction:      {dxy.get('dxy_direction', 'NULL')}
- Data status:    {dxy.get('status', 'unknown')}
  → Dollar UP = risk-off (bearish crypto) | Dollar DOWN = risk-on (bullish crypto)

10-Year Treasury Yield:
- Current yield:  {fmt(yields.get('yield_10y'), 3)}%
- Prior yield:    {fmt(yields.get('yield_10y_prior'), 3)}%
- Change (bps):   {fmt(yields.get('yield_change_bps'), 1)}
- Direction:      {yields.get('yield_direction', 'NULL')}
- As of date:     {yields.get('yield_date', 'NULL')}
- Data status:    {yields.get('status', 'unknown')}
  → Yields RISING = risk-off | Yields FALLING = risk-on
"""

    crypto_block = f"""
CRYPTO MARKET STRUCTURE:

Dominance & Size:
- BTC dominance:  {fmt(coingecko.get('btc_dominance_pct'))}%
  → Above 60% = BTC-led, ETH underperforming | Below 50% = altcoin season
  → Rising = risk aversion | Falling = risk appetite returning
- ETH dominance:  {fmt(coingecko.get('eth_dominance_pct'))}%
- Total market cap: ${coingecko.get('total_market_cap_usd', 'NULL'):,.0f}
- 24h market cap change: {fmt(coingecko.get('market_cap_change_24h_pct'))}%
- Data status:    {coingecko.get('status', 'unknown')}

Fear & Greed Index:
- Current score:  {fear_greed.get('fear_greed_index', 'NULL')} / 100
- Classification: {fear_greed.get('fear_greed_classification', 'NULL')}
- 3-day trend:    {fear_greed.get('fear_greed_3d_trend', 'NULL')}
- 3-day values:   {fear_greed.get('fear_greed_3d_values', 'NULL')}
  → 0-25: Extreme Fear | 25-45: Fear | 45-55: Neutral
  → 55-75: Greed | 75-100: Extreme Greed
  → Trend direction matters as much as current level
- Data status:    {fear_greed.get('status', 'unknown')}
"""

    coinbase_block = f"""
COINBASE CONTEXT (from live observer):
- Coinbase premium: {fmt(observer.get('premium_pct'), 4)}%
  → Positive/rising = US institutional buying on our venue
  → Negative/falling = US selling pressure or institutional absence
- Premium direction: {observer.get('premium_direction', 'NULL')}
- ETH vol regime:   {observer.get('vol_regime', 'NULL')}
- BTC 6h direction: {observer.get('btc_6h_direction', 'NULL')}
- Current hour UTC: {observer.get('hour_of_day_utc', 'NULL')}
- Day of week:      {observer.get('day_of_week', 'NULL')}
- ETH price:        ${fmt(observer.get('eth_price'), 2)}
- Data status:      {observer.get('status', 'unknown')}
"""

    return (
        macro_block + crypto_block + coinbase_block +
        "\nSynthesize these signals and return your vote."
    ).strip()


# ── Main agent class ───────────────────────────────────────────

class Casper:
    def __init__(self):
        self.client     = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        self.model_name = "gemini-2.5-flash"

    def _build_safety_settings(self):
        return [
            types.SafetySetting(
                category="HARM_CATEGORY_HARASSMENT",
                threshold="BLOCK_NONE",
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_HATE_SPEECH",
                threshold="BLOCK_NONE",
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                threshold="BLOCK_NONE",
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT",
                threshold="BLOCK_NONE",
            ),
        ]

    def _call_gemini(self, context: str) -> str:
        """
        Make a single Gemini API call and return raw text.
        Extracted so retry logic can call it cleanly.
        """
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=context,
            config=types.GenerateContentConfig(
                system_instruction=CASPER_SYSTEM,
                temperature=0.1,
                max_output_tokens=1024,
                safety_settings=self._build_safety_settings(),
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        if not response.text:
            raise ValueError("Gemini returned empty response text")
        return response.text.strip()

    def _parse_raw(self, raw: str) -> dict:
        """
        Strip markdown fences and parse JSON from Gemini response.
        """
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        return json.loads(raw)

    def assess(self) -> dict:
        """
        Main entry point. Fetches all macro data, builds context,
        calls Gemini 2.5 Pro with thinking mode, returns a vote dict.
        Always returns a valid dict even if the API call fails.
        Retries once on JSON parse error before returning error vote.
        """
        try:
            # Fetch all data sources
            coingecko  = get_coingecko_data()
            fear_greed = get_fear_greed()
            dxy        = get_dxy_data()
            yields     = get_yield_data()
            observer   = get_observer_context()

            # Build context string
            context = build_context(
                coingecko, fear_greed, dxy, yields, observer
            )

            # Call Gemini
            raw = self._call_gemini(context)
            logger.debug("Casper raw response: %s", raw)

            try:
                vote = self._parse_raw(raw)
            except json.JSONDecodeError as e:
                logger.warning(
                    "Casper JSON parse error on first attempt: %s — retrying", e
                )
                raw  = self._call_gemini(context)
                logger.debug("Casper retry raw response: %s", raw)
                vote = self._parse_raw(raw)

            # Validate required fields
            required = ["vote", "conviction", "reasoning",
                        "concerns", "veto", "macro_lean"]
            for field in required:
                if field not in vote:
                    raise ValueError(f"Missing field: {field}")

            if vote["vote"] not in ["long", "short", "flat"]:
                raise ValueError(f"Invalid vote: {vote['vote']}")
            if vote["conviction"] not in ["high", "medium", "low"]:
                raise ValueError(f"Invalid conviction: {vote['conviction']}")
            if vote["macro_lean"] not in ["risk-on", "risk-off", "mixed"]:
                raise ValueError(f"Invalid macro_lean: {vote['macro_lean']}")

            vote["veto"] = False  # Casper never vetoes

            logger.info(
                "Casper vote: %s | conviction: %s | macro_lean: %s | %s",
                vote["vote"].upper(),
                vote["conviction"],
                vote["macro_lean"],
                vote["reasoning"][:80],
            )

            return {
                "agent": "casper", "status": "ok", **vote,
                "_btc_6h_direction": observer.get("btc_6h_direction", "unknown"),
            }

        except Exception as e:
            logger.error("Casper error: %s", e)
            return self._error_vote(str(e))

    def _error_vote(self, reason: str) -> dict:
        return {
            "agent":      "casper",
            "status":     "error",
            "vote":       "flat",
            "conviction": "low",
            "reasoning":  f"Casper error — defaulting to flat. Reason: {reason}",
            "concerns":   ["agent error — macro context unavailable"],
            "veto":       False,
            "macro_lean": "mixed",
        }

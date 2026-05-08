import json
import logging
import re
from google import genai
from google.genai import types
from config import GOOGLE_API_KEY

log = logging.getLogger('magi.casper')

client = genai.Client(api_key=GOOGLE_API_KEY)

SYSTEM_PROMPT_PATH = "/root/xrp_grid/magi/prompts/casper_prompt.txt"

def load_prompt():
    with open(SYSTEM_PROMPT_PATH, 'r') as f:
        return f.read()

def build_context(indicators: dict) -> str:
    current_price = None
    try:
        from database import get_candles
        rows = get_candles('1h', limit=1)
        if rows:
            current_price = rows[0].get('close')
    except Exception as e:
        log.warning(f"Casper: failed to fetch current_price: {e}")

    return f"""CURRENT MARKET REGIME DATA — XRP/USD

Price Context:
- current_price: {current_price if current_price is not None else 'NULL'}

Trend Indicators (Daily):
- ema_50: {indicators.get('ema_50', 'NULL')}
- ema_200: {indicators.get('ema_200', 'NULL')}
- adx: {indicators.get('adx', 'NULL')}
- adx_pos: {indicators.get('adx_pos', 'NULL')}
- adx_neg: {indicators.get('adx_neg', 'NULL')}
- bb_width: {indicators.get('bb_width', 'NULL')}
- bb_upper: {indicators.get('bb_upper', 'NULL')}
- bb_lower: {indicators.get('bb_lower', 'NULL')}

Momentum (6h):
- roc_6h: {indicators.get('roc_6h', 'NULL')}%

Mean Reversion Signals:
- autocorr_1h: {indicators.get('autocorr_1h', 'NULL')}
- autocorr_4h: {indicators.get('autocorr_4h', 'NULL')}

BTC Market Context (Daily):
- btc_ema_50: {indicators.get('btc_ema_50', 'NULL')}
- btc_ema_200: {indicators.get('btc_ema_200', 'NULL')}

Respond with a JSON object only. No preamble, no markdown fences."""

def extract_json(text: str) -> dict:
    """Extract JSON from response text, handling markdown fences."""
    if not text:
        raise ValueError("Empty response text")
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    return json.loads(text)

def get_decision(indicators: dict) -> dict:
    try:
        system_prompt = load_prompt()
        context = build_context(indicators)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=context,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2,
                max_output_tokens=1024,
                thinking_config=types.ThinkingConfig(thinking_budget=0)
            )
        )
        if not response.text:
            raise ValueError(f"Empty response from Gemini — finish_reason: {response.candidates[0].finish_reason if response.candidates else 'unknown'}")
        result = extract_json(response.text)
        try:
            from database import insert_token_usage
            from magi.costs import estimate_cost
            um = response.usage_metadata
            pt = um.prompt_token_count
            ct = um.candidates_token_count
            tt = um.total_token_count
            cost = estimate_cost("gemini-2.5-flash", pt, ct)
            insert_token_usage(
                agent="casper",
                model="gemini-2.5-flash",
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
                cost_usd=cost,
                source="direct"
            )
        except Exception as e:
            log.warning(f"Casper token logging failed: {e}")
        log.info(f"Casper: regime={result.get('regime')} conviction={result.get('conviction')}")
        return result
    except Exception as e:
        log.error(f"Casper error: {e}")
        return {
            "agent": "casper",
            "regime": "UNCERTAIN",
            "conviction": "low",
            "trend_direction": None,
            "reasoning": f"Error: {str(e)}",
            "concerns": "Agent call failed — defaulting to UNCERTAIN"
        }

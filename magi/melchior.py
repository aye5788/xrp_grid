import json
import logging
from openai import OpenAI
from config import OPENAI_API_KEY

log = logging.getLogger('magi.melchior')

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT_PATH = "/root/xrp_grid/magi/prompts/melchior_prompt.txt"

def load_prompt():
    with open(SYSTEM_PROMPT_PATH, 'r') as f:
        return f.read()

def build_context(indicators: dict, grid_state: dict) -> str:
    """Build the context package Melchior receives."""
    return f"""CURRENT MARKET MICROSTRUCTURE — XRP/USD

Grid Parameters:
- grid_centre: {grid_state.get('centre_price', 'NULL')}
- grid_spacing_pct: {grid_state.get('spacing_pct', 'NULL')}
- pause_longs: {grid_state.get('pause_longs', 0)}
- pause_shorts: {grid_state.get('pause_shorts', 0)}

Microstructure Signals:
- vwap_dev_pct: {indicators.get('vwap_dev_pct', 'NULL')}%
- vol_regime: {indicators.get('vol_regime', 'NULL')}
- autocorr_1h: {indicators.get('autocorr_1h', 'NULL')}
- autocorr_4h: {indicators.get('autocorr_4h', 'NULL')}
- atr: {indicators.get('atr', 'NULL')}
- atr_percentile: {indicators.get('atr_percentile', 'NULL')}
- inventory_skew: {indicators.get('inventory_skew', 'NULL')}

Respond with a JSON object only. No preamble."""

def get_decision(indicators: dict, grid_state: dict) -> dict:
    """Call Melchior and return structured decision."""
    try:
        system_prompt = load_prompt()
        context = build_context(indicators, grid_state)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ],
            temperature=0.2,
            max_tokens=500,
            response_format={"type": "json_object"}
        )
        raw = response.choices[0].message.content
        result = json.loads(raw)
        log.info(f"Melchior: action={result.get('action')} conviction={result.get('conviction')}")
        return result
    except Exception as e:
        log.error(f"Melchior error: {e}")
        return {
            "agent": "melchior",
            "action": "MAINTAIN",
            "conviction": "low",
            "recentre_target": None,
            "spacing_adjustment_pct": None,
            "reasoning": f"Error: {str(e)}",
            "concerns": "Agent call failed — defaulting to MAINTAIN"
        }

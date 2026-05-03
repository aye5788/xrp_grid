import json
import logging
import anthropic
from config import ANTHROPIC_API_KEY

log = logging.getLogger('magi.balthasar')

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT_PATH = "/root/xrp_grid/magi/prompts/balthasar_prompt.txt"

def load_prompt():
    with open(SYSTEM_PROMPT_PATH, 'r') as f:
        return f.read()

def build_context(indicators: dict, inventory: dict, grid_state: dict) -> str:
    return f"""CURRENT RISK STATE — XRP/USD GRID BOT

Inventory:
- xrp_held: {inventory.get('xrp_held', 'NULL')}
- usd_held: {inventory.get('usd_held', 'NULL')}
- net_position_usd: {inventory.get('net_position_usd', 'NULL')}
- inventory_skew: {inventory.get('inventory_skew', 'NULL')}

Grid State:
- centre_price: {grid_state.get('centre_price', 'NULL')}
- spacing_pct: {grid_state.get('spacing_pct', 'NULL')}
- pause_longs: {grid_state.get('pause_longs', 0)}
- pause_shorts: {grid_state.get('pause_shorts', 0)}
- halt: {grid_state.get('halt', 0)}

Market:
- vol_regime: {indicators.get('vol_regime', 'NULL')}
- vwap_dev_pct: {indicators.get('vwap_dev_pct', 'NULL')}%
- atr_percentile: {indicators.get('atr_percentile', 'NULL')}

You must respond with a valid JSON object only. No preamble, no explanation outside the JSON."""

def get_decision(indicators: dict, inventory: dict, grid_state: dict) -> dict:
    try:
        system_prompt = load_prompt()
        context = build_context(indicators, inventory, grid_state)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": context}]
        )
        raw = response.content[0].text.strip()
        if not raw:
            raise ValueError("Empty response from Balthasar")
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        log.info(f"Balthasar: action={result.get('action')} conviction={result.get('conviction')}")
        return result
    except Exception as e:
        log.error(f"Balthasar error: {e}")
        return {
            "agent": "balthasar",
            "action": "CLEAR",
            "conviction": "low",
            "reasoning": f"Error: {str(e)}",
            "concerns": "Agent call failed — defaulting to CLEAR"
        }

import json
import logging
import anthropic
from config import ANTHROPIC_API_KEY, MAX_INVENTORY_USD

log = logging.getLogger('magi.balthasar')

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT_PATH = "/root/xrp_grid/magi/prompts/balthasar_prompt.txt"

def load_prompt():
    with open(SYSTEM_PROMPT_PATH, 'r') as f:
        return f.read()

def build_context(indicators: dict, inventory: dict, grid_state: dict, current_price=None) -> str:
    xrp = inventory.get('xrp_held') or 0
    usd = inventory.get('usd_held') or 0
    price_ok = current_price is not None and current_price > 0

    inventory_skew_raw = inventory.get('inventory_skew')

    if price_ok:
        xrp_value_usd       = xrp * current_price
        total_universe_usd  = xrp_value_usd + usd
        target_xrp_value    = total_universe_usd / 2
        allocation_skew     = (xrp_value_usd - target_xrp_value) / total_universe_usd if total_universe_usd > 0 else 0
        xrp_pct_of_universe = xrp_value_usd / total_universe_usd * 100 if total_universe_usd > 0 else 0
        inv_skew_fmt        = f"{inventory_skew_raw:+.3f}" if inventory_skew_raw is not None else 'NULL'

        portfolio_block = f"""PORTFOLIO STATE:
- total_universe_usd: ${total_universe_usd:.2f}  (sum of XRP value + USD held)
- xrp_value_usd: ${xrp_value_usd:.2f}  ({xrp_pct_of_universe:.1f}% of universe)
- usd_held: ${usd:.2f}
- allocation_skew: {allocation_skew:+.3f}  (range ±1; 0 = balanced 50/50, +1 = all XRP, -1 = all USD)
- inventory_skew (DB): {inv_skew_fmt}"""
    else:
        portfolio_block = "PORTFOLIO STATE: NULL (price unavailable)"

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

{portfolio_block}

Market:
- vol_regime: {indicators.get('vol_regime', 'NULL')}
- vwap_dev_pct: {indicators.get('vwap_dev_pct', 'NULL')}%
- atr_percentile: {indicators.get('atr_percentile', 'NULL')}

You must respond with a valid JSON object only. No preamble, no explanation outside the JSON."""

def get_decision(indicators: dict, inventory: dict, grid_state: dict, current_price=None) -> dict:
    try:
        system_prompt = load_prompt()
        context = build_context(indicators, inventory, grid_state, current_price)
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
        try:
            from database import insert_token_usage
            from magi.costs import estimate_cost
            pt = response.usage.input_tokens
            ct = response.usage.output_tokens
            tt = pt + ct
            cost = estimate_cost("claude-sonnet-4-6", pt, ct)
            insert_token_usage(
                agent="balthasar",
                model="claude-sonnet-4-6",
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
                cost_usd=cost,
                source="direct"
            )
        except Exception as e:
            log.warning(f"Balthasar token logging failed: {e}")
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

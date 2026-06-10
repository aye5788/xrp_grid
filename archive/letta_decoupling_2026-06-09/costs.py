# Token pricing per million tokens — update if pricing changes.
# Keys are matched after stripping any provider prefix (e.g. the live
# agent_registry stores 'anthropic/claude-haiku-4-5'; estimate_cost
# strips 'anthropic/' before lookup).
MODEL_PRICING = {
    'gpt-4o': {'input': 2.50, 'output': 10.00},
    'gpt-4o-mini': {'input': 0.15, 'output': 0.60},
    'claude-sonnet-4-6': {'input': 3.00, 'output': 15.00},
    'claude-haiku-4-5': {'input': 1.00, 'output': 5.00},
    'claude-opus-4-7': {'input': 15.00, 'output': 75.00},
    'gemini-2.5-flash': {'input': 0.30, 'output': 2.50},
    'gemini-2.5-pro': {'input': 1.25, 'output': 10.00},
    # Preview-tier pricing not yet published; using 2.5-flash as a
    # conservative placeholder. Verify and update once Google posts
    # gemini-3-flash-preview rates.
    'gemini-3-flash-preview': {'input': 0.30, 'output': 2.50},
}

# Fixed monthly subscriptions — edit as needed
FIXED_SUBSCRIPTIONS = {
    'DigitalOcean droplet': 6.00,
    'Domain': 0.00,
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate cost in USD for a single call."""
    # Strip provider prefix if present (GEEP/gpt-4o → gpt-4o)
    model_key = model.split('/')[-1] if '/' in model else model
    pricing = MODEL_PRICING.get(model_key)
    if not pricing:
        return 0.0
    input_cost = (prompt_tokens / 1_000_000) * pricing['input']
    output_cost = (completion_tokens / 1_000_000) * pricing['output']
    return round(input_cost + output_cost, 6)


def get_fixed_monthly_total() -> float:
    return round(sum(FIXED_SUBSCRIPTIONS.values()), 2)

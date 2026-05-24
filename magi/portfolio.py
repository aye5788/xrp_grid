"""
magi/portfolio.py — single source of truth for portfolio-derived values.

Used by:
  - grid/engine.py (inventory sync at fill time + periodic update_inventory)
  - magi/orchestrator.py:build_world_state() (per-cycle snapshot exposed
    to agents under world_state.portfolio)
  - magi/orchestrator.py:enforce_hard_rules() (reads world_state.portfolio
    instead of recomputing — buffer-floor checks use the same numbers
    the agents see)

Centralisation rationale: before this module, xrp_value_usd was computed
in three places (engine PAPER_RESET, engine update_inventory,
enforce_hard_rules); total_universe_usd and allocation_skew were computed
in two engine sites. Drift between sites was a latent risk and the agents
had no way to see portfolio.* at all (Balthasar's persona referenced a
namespace that did not exist in world_state). This helper fixes both.

Convention: target_xrp_value = total_universe_usd / 2. allocation_skew
is signed in [-1, +1] where 0 = balanced 50/50, +1 = all XRP, -1 = all USD.
"""


def compute_portfolio_metrics(xrp_held, usd_held, price) -> dict:
    """Compute portfolio-derived values from raw inventory + spot price.

    Inputs may be None or zero — return a fully-populated dict with zeros
    rather than partial keys so consumers can rely on the schema.

    Returns a dict with:
      - xrp_value_usd:        XRP holdings valued at current price
      - total_universe_usd:   xrp_value_usd + usd_held
      - xrp_pct_of_universe:  xrp_value_usd / total_universe_usd  (0.0-1.0)
      - allocation_skew:      signed [-1, +1]; 0 = balanced 50/50
    """
    try:
        xrp_held_f = float(xrp_held or 0.0)
    except (TypeError, ValueError):
        xrp_held_f = 0.0
    try:
        usd_held_f = float(usd_held or 0.0)
    except (TypeError, ValueError):
        usd_held_f = 0.0
    try:
        price_f = float(price or 0.0)
    except (TypeError, ValueError):
        price_f = 0.0

    xrp_value_usd = xrp_held_f * price_f
    total_universe_usd = xrp_value_usd + usd_held_f

    if total_universe_usd > 0:
        target_xrp_value = total_universe_usd / 2.0
        allocation_skew = (xrp_value_usd - target_xrp_value) / total_universe_usd
        xrp_pct_of_universe = xrp_value_usd / total_universe_usd
    else:
        allocation_skew = 0.0
        xrp_pct_of_universe = 0.0

    return {
        "xrp_value_usd":       xrp_value_usd,
        "total_universe_usd":  total_universe_usd,
        "xrp_pct_of_universe": xrp_pct_of_universe,
        "allocation_skew":     allocation_skew,
    }

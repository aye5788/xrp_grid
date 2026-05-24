"""
spacing_evaluator.py — analytical, closed-form variant scoring.

Replaces the fill-based shadow-sim spacing search. The shadow sim couldn't
differentiate variants in low-vol regimes because none of them accumulated
fills. Here every candidate (levels, spacing_pct) tuple is scored from the
historical hourly-range distribution alone — no LLM, no simulation,
deterministic.

Per-LEVEL economics (not per-round-trip-of-the-innermost-pair, as the
earlier draft did):

  Each level pair i ∈ [1, N/2] sits at ± i * spacing from the centre. For
  the buy at centre*(1 - i*s) AND the sell at centre*(1 + i*s) to both
  fill within one hour, the hourly range must span both extremes — i.e.

      (high - low) / low  >=  2 * i * spacing

  Outer pairs need bigger excursions; this is what makes level count
  actually matter. In a low-vol market the outermost pair of a 10-level
  grid rarely fills, and the variant's score falls accordingly.

  pair_profit_pct = spacing - 2 * fee   (per round-trip, after Kraken fees)
  pair_rt_per_day = (count of qualifying hours / total hours) * 24
  pair_pnl_pct    = pair_rt_per_day * pair_profit_pct

  Each pair holds 2 * (1/N) = 2/N of grid capital (one buy slot + one sell
  slot). Daily PnL of pair i, as a % of TOTAL grid capital:

      pair_grid_pnl_pct = (2 / N) * pair_pnl_pct

  Grid total:
      total_pnl_pct = sum_i(pair_grid_pnl_pct)
                    = (2 / N) * (spacing - 2*fee) * sum_i(pair_rt_per_day)

  Adding outer levels that don't fill drags `sum_i(pair_rt_per_day)` up
  much less than 1/N drags the multiplier down — so more levels only
  helps when vol can reach those outer pairs. Exactly the "no-babying"
  property the operator asked for.

Acceptability requirement: a variant is `acceptable` iff `spacing > 2*fee`
(clears the per-round-trip fee floor) AND its total expected daily PnL is
positive (`total_pnl_pct > 0`, i.e. at least the inner pair(s) fill and the
grid nets out positive). The earlier rule also required EVERY pair to fill at
least once in the window; after the 2026-05-23 fee correction
(`2*TAKER_FEE = 0.008`) that became unreachable in low-vol regimes — the outer
rungs of any fee-clearing grid never saw the hourly range they needed, so all
36 variants scored unacceptable and the grid stood down indefinitely. The
unfilled outer rungs are resting inventory reservations, not a cost, so a
net-positive grid is acceptable even when its outer pairs are quiet. Per-pair
liveness is still exposed via `per_level_rt_per_day` for transparency.
`acceptable=false` variants are appended for traceability but always rank
behind every acceptable one. Melchior is instructed in her persona to never
select an unacceptable variant.

Public surface (stable contract):
  - DEFAULT_VARIANTS: 36 candidates (6 level-counts × 6 spacings).
  - score_variants(...) returns a list[dict], JSON-serializable. Each
    entry has: levels, spacing_pct, profit_per_round_trip_pct,
    per_level_pnl_pct (list), per_level_rt_per_day (list),
    estimated_round_trips_per_day (sum across pairs),
    expected_daily_pnl_pct (grid total, normalised by total capital),
    acceptable (bool), rank (int or None).
  - Sort: acceptable variants first, ranked by expected_daily_pnl_pct
    DESC; unacceptable variants appended (still ordered for traceability).

Edge cases:
  - Empty candles → []
  - <24h history → all variants returned with rank=None and
    estimated_round_trips_per_day / expected_daily_pnl_pct / per_level_*
    set to None (acceptable=False under the strict reading; not enough
    data to certify per-level positivity).
  - Variants whose spacing is outside [MIN_GRID_SPACING_PCT,
    MAX_GRID_SPACING_PCT] are filtered out before scoring.
"""

from typing import Optional


# Variant search space. 6 level-counts × 6 spacings = 36 variants.
# Level range [5, 10] matches the operator's "min ~5, max ~10" guidance —
# enough room for Melchior to explore, narrow enough that the engine clamp
# at [4, 12] never has to fight her choice. Spacings span the round-trip
# fee-clearance boundary (2 * TAKER_FEE = 0.008 after the 2026-05-23 fee
# correction; the two sub-0.008 spacings stay in the set for diagnostics but
# can never be acceptable) up to MAX.
DEFAULT_VARIANTS = [
    (levels, spacing)
    for levels in (5, 6, 7, 8, 9, 10)
    for spacing in (0.005, 0.0075, 0.01, 0.015, 0.02, 0.025)
]


def score_variants(
    current_price: float,
    candles_1h: list,
    fee_rate_per_side: float,
    candidate_variants: list,
) -> list:
    """
    Rank candidate (levels, spacing_pct) variants by total grid PnL%.

    Each candle dict must expose 'high' and 'low' (numeric). 'close' is
    accepted but not used. Order doesn't matter — only the distribution
    of hourly range fractions does.
    """
    # Pull MIN/MAX bounds lazily so this module is import-safe in tests
    # where config isn't on sys.path.
    try:
        from config import MIN_GRID_SPACING_PCT, MAX_GRID_SPACING_PCT
    except Exception:
        MIN_GRID_SPACING_PCT, MAX_GRID_SPACING_PCT = 0.003, 0.025

    if not candles_1h:
        return []

    # Compute (high-low)/low for every candle once.
    hourly_ranges = []
    for c in candles_1h:
        try:
            hi = float(c['high'])
            lo = float(c['low'])
        except (KeyError, TypeError, ValueError):
            continue
        if lo <= 0 or hi < lo:
            continue
        hourly_ranges.append((hi - lo) / lo)

    total_hours = len(hourly_ranges)
    have_24h = total_hours >= 24

    rt_cost = 2.0 * float(fee_rate_per_side)

    # Pre-filter to in-bounds variants.
    filtered: list = []
    for entry in candidate_variants:
        try:
            lc, sp = entry
            lc = int(lc)
            sp = float(sp)
        except (TypeError, ValueError):
            continue
        if sp < MIN_GRID_SPACING_PCT or sp > MAX_GRID_SPACING_PCT:
            continue
        if lc < 2:
            continue  # need at least 1 pair
        filtered.append((lc, sp))

    scored: list = []
    for lc, sp in filtered:
        profit_pct = sp - rt_cost           # per round-trip, vs that pair's capital
        n_pairs = lc // 2

        if not have_24h:
            scored.append({
                'levels':                         int(lc),
                'spacing_pct':                    round(float(sp), 6),
                'profit_per_round_trip_pct':      round(profit_pct, 6),
                'per_level_rt_per_day':           None,
                'per_level_pnl_pct':              None,
                'estimated_round_trips_per_day':  None,
                'expected_daily_pnl_pct':         None,
                'acceptable':                     False,  # insufficient data
                'rank':                           None,
            })
            continue

        per_pair_rt: list = []
        per_pair_pnl_pct: list = []
        for i in range(1, n_pairs + 1):
            pair_threshold = 2.0 * i * sp
            pair_qualifying = sum(1 for r in hourly_ranges if r >= pair_threshold)
            pair_rt = (pair_qualifying / total_hours) * 24.0
            # Per-pair PnL as a % of TOTAL grid capital. Each pair holds
            # 2/N of grid capital; the round-trip earns `profit_pct` of
            # the pair's deployed capital. So pair contribution to
            # grid-total PnL% is (2/N) * pair_rt * profit_pct.
            pair_pnl_grid_pct = (2.0 / lc) * pair_rt * profit_pct
            per_pair_rt.append(round(pair_rt, 4))
            per_pair_pnl_pct.append(round(pair_pnl_grid_pct, 6))

        # Sum across pairs gives total daily PnL of the grid, in % of total
        # grid capital.
        total_pnl_pct = round(sum(per_pair_pnl_pct), 6)
        total_rt_per_day = round(sum(per_pair_rt), 4)

        # Acceptability: clear the per-round-trip fee floor (profit_pct > 0,
        # i.e. spacing > 2*fee) AND have positive expected daily PnL across the
        # grid. The earlier criterion additionally required EVERY pair to have
        # filled at least once in the window (all_pairs_active). After the
        # 2026-05-23 fee correction (2*TAKER_FEE = 0.008) that became
        # unreachable in low-vol regimes: the outer rungs of any fee-clearing
        # grid never see the hourly range they need, so all 36 variants scored
        # unacceptable and the grid stood down indefinitely (GRID_PAUSE via the
        # orchestrator's NO_ACCEPTABLE_VARIANT rule). A grid whose inner pairs
        # fill and whose net expected PnL is positive is a working, fee-positive
        # grid — the unfilled outer rungs are just resting inventory
        # reservations, not a cost. So require net-positive expected PnL rather
        # than all-pairs-active. Per-pair liveness stays visible to consumers
        # via the per_level_rt_per_day list.
        acceptable = (profit_pct > 0) and (total_pnl_pct > 0)

        scored.append({
            'levels':                         int(lc),
            'spacing_pct':                    round(float(sp), 6),
            'profit_per_round_trip_pct':      round(profit_pct, 6),
            'per_level_rt_per_day':           per_pair_rt,
            'per_level_pnl_pct':              per_pair_pnl_pct,
            'estimated_round_trips_per_day':  total_rt_per_day,
            'expected_daily_pnl_pct':         total_pnl_pct,
            'acceptable':                     bool(acceptable),
            'rank':                           None,  # filled below
        })

    if not have_24h:
        return scored

    # Sort: acceptable first (bucket 0), unacceptable last (bucket 1);
    # within each bucket, by expected_daily_pnl_pct DESC. Tie-break on
    # (spacing ASC, levels ASC) for determinism.
    def sort_key(v):
        bucket = 0 if v['acceptable'] else 1
        pnl = v['expected_daily_pnl_pct']
        # None pnls (insufficient data) shouldn't actually appear here
        # because have_24h is True, but be defensive.
        pnl_sort = -(pnl if pnl is not None else 0.0)
        return (bucket, pnl_sort, v['spacing_pct'], v['levels'])

    scored.sort(key=sort_key)
    for i, v in enumerate(scored, start=1):
        v['rank'] = i

    return scored

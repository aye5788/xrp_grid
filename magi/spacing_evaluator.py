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
  pair_rt_per_day = (completed swings of amplitude >= 2*i*spacing over the
                     close path, / 2, normalised to per-day). Swings span
                     however many hours they take — the old per-hour-range
                     model scored fee-viable wide spacings at zero fills
                     and would have deadlocked the grid (caught 2026-06-11).
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

Acceptability requirement (GoodCrypto-frame redesign, 2026-06-11): a variant
is `acceptable` iff `spacing >= 6*fee_per_side` — the round-trip fee
(2 maker fills = 2*fee) may consume no more than 1/3 of the gross spacing.
That is the ONLY gate. The old rule (`spacing > 2*fee` AND
`total_pnl_pct > 0`) failed two ways: (a) bare 2*fee clearance admitted the
0.75% grid, which a 9.5y hourly backtest (2016-12 → 2026-06) showed loses in
9 of 10 years — per-fill fee-positivity is not equity-positivity, because
trend cycling consumes the thin remainder; (b) gating on the swing-forecast
PnL let a fill-model blind spot veto economically sound grids — that is
judgment, and judgment belongs to Melchior/the council. The forecast columns
(estimated_round_trips_per_day, expected_daily_pnl_pct, per-pair liveness)
stay in the output as FACTS for the council to weigh, not vetoes. (A still
earlier rule requiring EVERY pair to fill in-window stays removed: unfilled
outer rungs are inventory reservations, not a cost.) `acceptable=false`
variants are appended for traceability but always rank behind every
acceptable one. Melchior is instructed in her persona to never select an
unacceptable variant.

Public surface (stable contract):
  - DEFAULT_VARIANTS: 36 candidates (6 level-counts × 6 spacings); entries
    below MIN_GRID_SPACING_PCT (the 0.0075 column) are filtered before
    scoring, so 30 variants survive at the current 1.5% floor.
  - score_variants(...) returns a list[dict], JSON-serializable. Each
    entry has: levels, spacing_pct, profit_per_round_trip_pct,
    per_level_pnl_pct (list), per_level_rt_per_day (list),
    estimated_round_trips_per_day (sum across pairs),
    expected_daily_pnl_pct (grid total, normalised by total capital),
    acceptable (bool), rank (int or None).
  - Sort: acceptable variants first; within each bucket by
    profit_per_round_trip_pct DESC (widest fee-viable gap first), then
    levels ASC (least capital committed), then spacing. The swing-forecast
    PnL deliberately does NOT drive rank — see Acceptability above.

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
# at [4, 12] never has to fight her choice. Spacings (2026-06-11) span the
# 6*MAKER_FEE acceptability floor (1.5%) up to MAX (2.5%), the band the 9.5y
# backtest showed viable. The 0.0075 column documents the old default but is
# filtered out by the MIN_GRID_SPACING_PCT bound before scoring — it lost in
# 9 of 10 backtest years and can never be selected.
DEFAULT_VARIANTS = [
    (levels, spacing)
    for levels in (5, 6, 7, 8, 9, 10)
    for spacing in (0.0075, 0.015, 0.0175, 0.02, 0.0225, 0.025)
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
        MIN_GRID_SPACING_PCT, MAX_GRID_SPACING_PCT = 0.015, 0.025

    if not candles_1h:
        return []

    # Compute (high-low)/low for every candle once (data-sufficiency
    # guard), and extract the close path for swing counting.
    hourly_ranges = []
    closes = []
    for c in candles_1h:
        try:
            hi = float(c['high'])
            lo = float(c['low'])
        except (KeyError, TypeError, ValueError):
            continue
        if lo <= 0 or hi < lo:
            continue
        hourly_ranges.append((hi - lo) / lo)
        try:
            cl = float(c['close'])
            if cl > 0:
                closes.append(cl)
        except (KeyError, TypeError, ValueError):
            pass

    total_hours = len(hourly_ranges)
    have_24h = total_hours >= 24 and len(closes) >= 24

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
            # Fill model (rewritten 2026-06-11): count completed price
            # SWINGS of amplitude >= pair_threshold over the close path,
            # across however many hours each takes. The old model counted
            # single-hour ranges >= threshold, which structurally scored
            # wide (fee-viable) spacings at zero fills — hourly ranges
            # almost never span 2*1.5%, but multi-hour walks do, and the
            # 9.5y backtest confirms 1.5-2.5% grids fill 5-9x/day. Two
            # alternating legs (down-then-up) = one round trip, which is
            # the harvest event the PnL model prices. One-way walks count
            # at most one leg regardless of depth — deliberately, since
            # accumulation fills in a trend are not harvest.
            legs = 0
            swing_hi = swing_lo = closes[0]
            dirn = 0
            for cl in closes[1:]:
                if cl > swing_hi:
                    swing_hi = cl
                if cl < swing_lo:
                    swing_lo = cl
                if dirn >= 0 and cl <= swing_hi * (1.0 - pair_threshold):
                    legs += 1
                    dirn = -1
                    swing_hi = swing_lo = cl
                elif dirn <= 0 and cl >= swing_lo * (1.0 + pair_threshold):
                    legs += 1
                    dirn = 1
                    swing_hi = swing_lo = cl
            pair_rt = ((legs / 2.0) / total_hours) * 24.0
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

        # Acceptability = the fee floor ONLY (GoodCrypto-frame redesign,
        # 2026-06-11, operator direction): spacing must be at least
        # 6 * fee_per_side, i.e. the 2-maker-fill round-trip cost (2*fee)
        # may consume no more than 1/3 of the gross spacing. The old floor
        # (spacing > 2*fee, any margin) admitted the 0.75% grid, which a
        # 9.5y hourly backtest (2016-12 → 2026-06) showed loses in 9 of 10
        # years: per-fill fee-positivity is NOT equity-positivity, because
        # trend cycling consumes the thin remainder. At >= 6*fee (1.5% at
        # maker 0.25%) the same backtest is viable, with a broad plateau
        # through 2.0-3.0%. There is NO PnL-forecast gate: the calculator
        # computes per-level economics; whether the market suits a grid
        # right now is JUDGMENT and belongs to Melchior/the council. The
        # swing-based fill estimate above stays in the output as a FACT for
        # the council to weigh — it no longer vetoes a variant. (The earlier
        # all_pairs_active criterion stays removed: outer rungs are
        # inventory reservations, not a cost; liveness stays visible via
        # per_level_rt_per_day.)
        fee_share_floor = 6.0 * float(fee_rate_per_side)
        acceptable = (sp >= fee_share_floor)

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

    # Sort (GoodCrypto-frame redesign, 2026-06-11): acceptable first
    # (bucket 0), unacceptable last (bucket 1); within each bucket by
    # profit_per_round_trip_pct DESC (widest fee-viable gap first — the 9.5y
    # backtest shows a broad viable plateau at 2.0-3.0% and losses below),
    # then levels ASC (fewest levels = least capital committed; the backtest
    # validated the few-level shape). The swing-based expected_daily_pnl_pct
    # deliberately does NOT drive rank anymore: it is information for
    # Melchior's judgment, not a decider — ranking by a forecast is what
    # baked the old fill-model's blind spot into every grid choice.
    def sort_key(v):
        bucket = 0 if v['acceptable'] else 1
        return (bucket, -v['profit_per_round_trip_pct'],
                v['levels'], v['spacing_pct'])

    scored.sort(key=sort_key)
    for i, v in enumerate(scored, start=1):
        v['rank'] = i

    return scored

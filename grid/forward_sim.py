"""Forward-realized grid-economics simulator + regime labeler (shared core).

This is the single source of the recycling-grid forward simulation. It was lifted
out of the Casper eval labeler (``optimize/casper/forward_label.py``, which now
re-exports from here) so the live per-role accuracy scorer
(``database.get_agent_accuracy``) can reuse the SAME reality-anchored sim without
importing the ``optimize/`` experiment tree — whose package ``__init__`` runs
``from . import agent`` and pulls the ADK eval stack (``vertexai``), which is not
installed in the core venv and would make the import fail outright.

Reality-anchored, NOT indicator-derived: a historical hourly bar is labeled by
what a recycling grid would actually have DONE over the next ``WINDOW_H`` hours,
using the bot's real fill + replacement rules and real config. No fitted
thresholds.

Faithful to the live code:
  - fill rule        -> grid/engine.py simulate_fills (buy fills on low<=level,
                        sell on high>=level; maker fee per fill; inventory-gated)
  - replacement rule -> scheduler.py (post opposite side at market*(1∓spacing);
                        SKIP when |market-fill_ref|/market > 2*spacing == stranding)

Measure: GRID ALPHA vs hold = grid_equity_end - hold_equity_end, marked at the
forward price, normalized by deployed grid notional:
  - downtrend -> grid over-buys the fall -> alpha < 0  (the item-0* bleed)
  - range     -> grid harvests fees      -> alpha > 0
  - uptrend   -> grid over-sells winners -> alpha < 0  (underperforms hold)
The threshold is the EXOGENOUS maker round-trip fee floor (2*MAKER_FEE), the
break-even a grid must clear — not a fitted number.

simulate() also returns grid_equity_start/grid_equity_end so callers can read the
grid's RAW pnl (grid_equity_end - grid_equity_start) — Balthasar's veto
counterfactual ("would the unpaused grid have bled?") — alongside alpha-vs-hold,
which Casper's label and Melchior's NO_PROFITABLE_GRID test use.

config-sourced: MAKER_FEE and ORDER_XRP come from config.py (single source of
truth). The labeler PARAMETERS — WINDOW_H / SPACING_PCT / N_LEVELS — are the
labeler's deliberate fixed choices (a representative live spacing; the live grid
spacing varies and is swept elsewhere) and stay module constants here. Pure:
config + stdlib only, takes ``bars`` as input (load_1h takes an open connection),
so this module never imports database.py — no import cycle.
"""

from config import MAKER_FEE, ORDER_SIZE_XRP

# ── Labeler parameters (deliberate fixed choices; see module docstring) ──
SPACING_PCT = 0.010          # representative live spacing (clamp 0.3%–2.5%)
N_LEVELS    = 5              # per side (live grid runs 5–10)
WINDOW_H    = 72            # forward horizon: multi-day, matched to the DAILY
                            # resolution of Casper's ema/adx indicators. A grid
                            # survives transient sub-daily wiggles; it bleeds on
                            # SUSTAINED moves. 24h caught intraday noise as regimes.
ORDER_XRP   = ORDER_SIZE_XRP            # config.ORDER_SIZE_XRP (Kraken XRP minimum)
FEE_FLOOR   = 2 * MAKER_FEE * 100       # 0.50% maker round-trip break-even, in %


def load_1h(conn):
    """Load chronological 1h OHLC bars [(timestamp, high, low, close), ...] from
    an OPEN sqlite connection. The caller owns the connection — this module takes
    no get_conn import, so it stays free of any database.py dependency / cycle."""
    rows = conn.execute(
        "SELECT timestamp, high, low, close FROM candles "
        "WHERE timeframe='1h' ORDER BY timestamp ASC"
    ).fetchall()
    return [(t, float(h), float(l), float(c))
            for (t, h, l, c) in rows if None not in (h, l, c)]


def simulate(bars, i, spacing_pct=SPACING_PCT, n_levels=N_LEVELS):
    """Run a recycling grid forward WINDOW_H hours from bar i. Return diagnostics.

    bars: list of (timestamp, high, low, close). i: index of the decision bar
    (entry price = its close). Returns a dict with:
      alpha_pct           grid-vs-hold alpha, normalized by deployed notional (%)
      drift_pct           forward price drift (%) — metadata / direction only
      n_fills             grid fills over the window
      p0, p_end           entry / forward-exit price
      grid_equity_start   grid equity marked at p0 (usd0 + xrp0*p0)
      grid_equity_end     grid equity marked at p_end (usd + xrp*p_end)
      grid_pnl            grid_equity_end - grid_equity_start (Balthasar veto test)
    """
    p0 = bars[i][3]                       # entry price = close at decision bar
    drift_skip = 2 * spacing_pct          # scheduler.py replacement drift guard
    # Build the initial ladder around p0.
    orders = []                           # each: {side, price, size, ref}
    for k in range(1, n_levels + 1):
        orders.append({"side": "buy",  "price": round(p0 * (1 - k * spacing_pct), 5),
                       "size": ORDER_XRP, "ref": p0})
        orders.append({"side": "sell", "price": round(p0 * (1 + k * spacing_pct), 5),
                       "size": ORDER_XRP, "ref": p0})
    # Seed generously so capital constraints rarely bind (label = regime, not seed).
    deployed = 2 * n_levels * ORDER_XRP * p0          # notional the grid works
    usd = 100 * n_levels * ORDER_XRP * p0
    xrp = 100 * n_levels * ORDER_XRP
    usd0, xrp0 = usd, xrp

    n_fills = 0
    for j in range(i + 1, min(i + 1 + WINDOW_H, len(bars))):
        _, hi, lo, close = bars[j]
        newly = []
        for o in orders:
            if o.get("done"):
                continue
            if o["side"] == "buy" and lo <= o["price"]:
                cost = o["size"] * o["price"]
                if usd < cost:
                    continue
                usd -= cost + cost * MAKER_FEE        # deduct maker fee (real cost)
                xrp += o["size"]
                o["done"] = True
                newly.append(o)
                n_fills += 1
            elif o["side"] == "sell" and hi >= o["price"]:
                if xrp < o["size"]:
                    continue
                proceeds = o["size"] * o["price"]
                usd += proceeds - proceeds * MAKER_FEE
                xrp -= o["size"]
                o["done"] = True
                newly.append(o)
                n_fills += 1
        # Recycle filled levels (scheduler.py rule), anchored to this bar's close.
        for o in newly:
            if abs(close - o["price"]) / close > drift_skip:
                continue                              # stranded — RECENTRE territory
            if o["side"] == "sell":
                orders.append({"side": "buy", "price": round(close * (1 - spacing_pct), 5),
                               "size": o["size"], "ref": close})
            else:
                orders.append({"side": "sell", "price": round(close * (1 + spacing_pct), 5),
                               "size": o["size"], "ref": close})

    p_end = bars[min(i + WINDOW_H, len(bars) - 1)][3]
    grid_equity_start = usd0 + xrp0 * p0
    grid_equity_end = usd + xrp * p_end
    hold_equity = usd0 + xrp0 * p_end
    alpha_pct = (grid_equity_end - hold_equity) / deployed * 100
    drift_pct = (p_end - p0) / p0 * 100
    return {"alpha_pct": alpha_pct, "drift_pct": drift_pct,
            "n_fills": n_fills, "p0": p0, "p_end": p_end,
            "grid_equity_start": grid_equity_start,
            "grid_equity_end": grid_equity_end,
            "grid_pnl": grid_equity_end - grid_equity_start}


def label(d, fee_floor=FEE_FLOOR):
    """Label by the GRID's realized economics over the holding window — the exact
    question the adaptive bot exists to get right: would the grid harvest or bleed?
    Net price drift is deliberately NOT a criterion (a big swing the grid harvests
    is a favourable high-vol RANGE; only a move that BLEEDS the grid is hostile).
    Direction is metadata only — the vocabulary is RANGING / TRENDING / UNCERTAIN."""
    a = d["alpha_pct"]
    direction = "bearish" if d["drift_pct"] < 0 else "bullish"
    if a < -fee_floor:      # grid lost to hold over the window -> hostile regime
        return "TRENDING", direction
    if a > fee_floor:       # grid beat hold by harvesting -> favourable regime
        return "RANGING", "flat"
    return "UNCERTAIN", "flat"

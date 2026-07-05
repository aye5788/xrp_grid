"""IREUL Phase 1 — label every hourly bar with forward grid economics + the
fixed trial-#1 feature vector. See 04_EXPERIMENTAL_IDEAS.md (Session 2026-07-04)
for the design and pre-committed criteria; README.md here for operations.

Reads tape/history.db READ-ONLY (rollup_bars interval 60 + 1440, signals_1h).
Writes optimize/ireul/ireul.db (regenerable artifact).

Labels are reality-anchored via the shared grid/forward_sim.simulate — what a
recycling grid at the LIVE config (2.5% spacing, 5 levels, 72h window, maker
fees, real fill/replacement rules) would have done from each bar. Binary
outcomes at the exogenous maker round-trip floor (0.50%):
  hostile    = alpha_pct < -0.50   (grid bleeds vs hold)
  favourable = alpha_pct > +0.50   (grid harvests vs hold)

Features (trial #1 — FIXED, no feature search; every one is price-derived and
computable live from observer.db/signals_1h):
  roc_6h, roc_24h, roc_72h        backward price rate-of-change, %
  vol_sigma_pct                   24h realized vol (signals_1h)
  regime_er                       24h efficiency ratio (signals_1h)
  drawdown_24h                    24h drawdown from high (signals_1h)
  drawdown_7d                     168h drawdown from running peak, %
  dist_ema50d, dist_ema200d       % distance of close from the 50/200-day EMA
                                  (EMAs on completed daily bars only — an hour
                                  inside day D sees the EMA through day D-1,
                                  matching how the live observer computes them
                                  and avoiding intraday lookahead)
"""
import os
import sqlite3
import sys
import time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from grid.forward_sim import simulate, WINDOW_H  # noqa: E402

HISTORY_DB = os.path.join(REPO, "tape", "history.db")
OUT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ireul.db")

SPACING_PCT = 0.025      # live grid config (clamp ceiling; current paper grid)
N_LEVELS = 5             # live grid config
FEE_FLOOR_PCT = 0.50     # 2 * MAKER_FEE, exogenous — mirrors forward_sim.FEE_FLOOR

HOUR_MS = 3600_000
DAY_MS = 24 * HOUR_MS


def ema(values, span):
    """Standard EMA (alpha = 2/(span+1)), seeded at the first value."""
    alpha = 2.0 / (span + 1)
    out = np.empty(len(values))
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def rolling_max(values, window):
    """O(n) sliding-window max (monotonic deque) — droplet-friendly, no big
    stride-trick allocation."""
    from collections import deque
    out = np.empty(len(values))
    dq = deque()
    for i, v in enumerate(values):
        while dq and values[dq[-1]] <= v:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - window:
            dq.popleft()
        out[i] = values[dq[0]]
    return out


def main():
    t0 = time.time()
    conn = sqlite3.connect(f"file:{HISTORY_DB}?mode=ro", uri=True)

    rows = conn.execute(
        "SELECT ts_begin, high, low, close FROM rollup_bars "
        "WHERE interval_min=60 AND high IS NOT NULL AND low IS NOT NULL "
        "AND close IS NOT NULL ORDER BY ts_begin ASC"
    ).fetchall()
    ts = np.array([r[0] for r in rows], dtype=np.int64)
    high = np.array([r[1] for r in rows])
    low = np.array([r[2] for r in rows])
    close = np.array([r[3] for r in rows])
    n = len(rows)
    print(f"hourly bars: {n} ({ts[0]} .. {ts[-1]})")

    # Contiguity check — the warehouse is maintained gap-free; verify anyway.
    gaps = int(np.sum(np.diff(ts) != HOUR_MS))
    print(f"non-1h steps: {gaps}")
    if gaps:
        print("WARNING: gaps present; roc/window features assume contiguity. "
              "Proceeding (labels use bar indices, unaffected).")

    # --- features from hourly closes ---
    def roc(h):
        out = np.full(n, np.nan)
        out[h:] = (close[h:] / close[:-h] - 1.0) * 100.0
        return out

    roc_6h, roc_24h, roc_72h = roc(6), roc(24), roc(72)

    peak_7d = rolling_max(close, 168)
    drawdown_7d = (close / peak_7d - 1.0) * 100.0
    drawdown_7d[:168] = np.nan  # incomplete window

    # --- daily EMAs, mapped to hours without intraday lookahead ---
    drows = conn.execute(
        "SELECT ts_begin, close FROM rollup_bars WHERE interval_min=1440 "
        "AND close IS NOT NULL ORDER BY ts_begin ASC"
    ).fetchall()
    dts = np.array([r[0] for r in drows], dtype=np.int64)
    dclose = np.array([r[1] for r in drows])
    ema50 = ema(dclose, 50)
    ema200 = ema(dclose, 200)
    # An hour at time t sees the EMA of the last day COMPLETED by t:
    # day index d qualifies iff dts[d] + 1 day <= t.
    idx = np.searchsorted(dts + DAY_MS, ts, side="right") - 1
    valid_day = idx >= 199  # 200-day EMA warmup (observer-style)
    idx_c = np.clip(idx, 0, len(dts) - 1)
    dist_ema50d = np.where(valid_day, (close / ema50[idx_c] - 1.0) * 100.0, np.nan)
    dist_ema200d = np.where(valid_day, (close / ema200[idx_c] - 1.0) * 100.0, np.nan)

    # --- signals_1h join (vol, efficiency ratio, 24h drawdown) ---
    smap = {}
    for ts_b, vol, er, dd in conn.execute(
        "SELECT ts_begin, vol_sigma_pct, regime_er, drawdown_pct FROM signals_1h"
    ):
        smap[ts_b] = (vol, er, dd)
    vol_sigma = np.array([smap.get(t, (None,) * 3)[0] for t in ts], dtype=object)
    regime_er = np.array([smap.get(t, (None,) * 3)[1] for t in ts], dtype=object)
    dd_24h = np.array([smap.get(t, (None,) * 3)[2] for t in ts], dtype=object)

    def fnum(a):
        return np.array([np.nan if v is None else float(v) for v in a])

    vol_sigma, regime_er, dd_24h = fnum(vol_sigma), fnum(regime_er), fnum(dd_24h)
    conn.close()

    # --- forward labels ---
    bars = list(zip(ts.tolist(), high.tolist(), low.tolist(), close.tolist()))
    last_i = n - WINDOW_H - 1
    alpha = np.full(n, np.nan)
    drift = np.full(n, np.nan)
    fills = np.full(n, -1, dtype=np.int64)
    t1 = time.time()
    for i in range(0, last_i + 1):
        d = simulate(bars, i, spacing_pct=SPACING_PCT, n_levels=N_LEVELS)
        alpha[i] = d["alpha_pct"]
        drift[i] = d["drift_pct"]
        fills[i] = d["n_fills"]
        if i and i % 10000 == 0:
            el = time.time() - t1
            print(f"  labeled {i}/{last_i}  ({el:.0f}s, {i/el:.0f}/s)")
    print(f"labeling done in {time.time()-t1:.0f}s")

    hostile = np.where(np.isnan(alpha), -1, (alpha < -FEE_FLOOR_PCT).astype(int))
    favourable = np.where(np.isnan(alpha), -1, (alpha > FEE_FLOOR_PCT).astype(int))

    out = sqlite3.connect(OUT_DB)
    out.execute("DROP TABLE IF EXISTS hours")
    out.execute("""CREATE TABLE hours (
        ts_ms INTEGER PRIMARY KEY, close REAL,
        roc_6h REAL, roc_24h REAL, roc_72h REAL,
        vol_sigma_pct REAL, regime_er REAL, drawdown_24h REAL, drawdown_7d REAL,
        dist_ema50d REAL, dist_ema200d REAL,
        alpha_pct REAL, drift_pct REAL, n_fills INTEGER,
        hostile INTEGER, favourable INTEGER)""")

    def nn(x):
        return None if (x is None or (isinstance(x, float) and np.isnan(x))) else float(x)

    out.executemany(
        "INSERT INTO hours VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(int(ts[i]), float(close[i]),
          nn(roc_6h[i]), nn(roc_24h[i]), nn(roc_72h[i]),
          nn(vol_sigma[i]), nn(regime_er[i]), nn(dd_24h[i]), nn(drawdown_7d[i]),
          nn(dist_ema50d[i]), nn(dist_ema200d[i]),
          nn(alpha[i]), nn(drift[i]), int(fills[i]),
          int(hostile[i]), int(favourable[i])) for i in range(n)])
    out.execute("DROP TABLE IF EXISTS meta")
    out.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    for k, v in {"spacing_pct": SPACING_PCT, "n_levels": N_LEVELS,
                 "window_h": WINDOW_H, "fee_floor_pct": FEE_FLOOR_PCT,
                 "source": HISTORY_DB, "n_hours": n}.items():
        out.execute("INSERT INTO meta VALUES (?,?)", (k, str(v)))
    out.commit()
    out.close()

    lab = alpha[~np.isnan(alpha)]
    print(f"labeled {len(lab)} hours: hostile {np.mean(lab < -FEE_FLOOR_PCT):.3f}, "
          f"favourable {np.mean(lab > FEE_FLOOR_PCT):.3f}, "
          f"uncertain {np.mean(np.abs(lab) <= FEE_FLOOR_PCT):.3f}")
    print(f"total {time.time()-t0:.0f}s -> {OUT_DB}")


if __name__ == "__main__":
    main()

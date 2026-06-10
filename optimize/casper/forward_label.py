"""Forward-realized regime labeler for Casper eval ground truth — CLI wrapper.

The simulation + labeling logic now lives in the core module ``grid/forward_sim.py``
(so the live per-role accuracy scorer can reuse it without importing this
optimize/ experiment tree). This file is a thin CLI that imports that logic and
prints the label distribution / per-label means / illustrative windows over the
full observer.db 1h history.

Reality-anchored, NOT indicator-derived: each historical hourly bar is labeled by
what a recycling grid would actually have DONE over the next WINDOW_H hours, using
the bot's real fill + replacement rules and real config. Threshold is the
EXOGENOUS maker round-trip fee floor (2*MAKER_FEE = 0.50%). See grid/forward_sim.py
for the full doctrine.

Run (main venv, stdlib only):
    /root/xrp_grid/venv/bin/python optimize/casper/forward_label.py
"""

import sqlite3
import sys
from pathlib import Path

# Make the repo root importable so `from grid.forward_sim import ...` works when
# this file is run directly as a script (sys.path[0] is this dir, not the root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from grid.forward_sim import (  # noqa: E402
    FEE_FLOOR,
    N_LEVELS,
    SPACING_PCT,
    WINDOW_H,
    label,
    load_1h,
    simulate,
)

DB = _REPO_ROOT / "observer.db"


def main():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    bars = load_1h(conn)
    conn.close()
    n = len(bars)
    print(f"loaded {n} hourly bars  {bars[0][0]} -> {bars[-1][0]}")
    print(f"config: spacing={SPACING_PCT*100:.2f}% levels={N_LEVELS} "
          f"window={WINDOW_H}h fee_floor={FEE_FLOOR:.2f}%\n")

    results = []
    for i in range(0, n - WINDOW_H - 1):
        d = simulate(bars, i)
        lab, direction = label(d)
        results.append((i, lab, direction, d))

    # Distribution
    from collections import Counter
    counts = Counter(lab for _, lab, _, _ in results)
    dirs = Counter((lab, dr) for _, lab, dr, _ in results)
    total = len(results)
    print(f"=== label distribution over {total} windows ===")
    for lab in ("RANGING", "TRENDING", "UNCERTAIN"):
        c = counts.get(lab, 0)
        print(f"  {lab:10s} {c:6d}  {c/total*100:5.1f}%")
    print(f"  (TRENDING split: bearish={dirs.get(('TRENDING','bearish'),0)}  "
          f"bullish={dirs.get(('TRENDING','bullish'),0)})\n")

    # Per-label means
    print("=== per-label mean drift / alpha / fills ===")
    for lab in ("RANGING", "TRENDING", "UNCERTAIN"):
        grp = [d for _, l, _, d in results if l == lab]
        if not grp:
            continue
        md = sum(x["drift_pct"] for x in grp) / len(grp)
        ma = sum(x["alpha_pct"] for x in grp) / len(grp)
        mf = sum(x["n_fills"] for x in grp) / len(grp)
        print(f"  {lab:10s} drift={md:+6.2f}%  alpha={ma:+6.3f}%  fills={mf:4.1f}")

    # Illustrative examples
    def show(title, rec):
        i, lab, dr, d = rec
        print(f"  [{title}] {bars[i][0]}  {lab}/{dr}  "
              f"p {d['p0']:.4f}->{d['p_end']:.4f}  drift={d['drift_pct']:+.2f}%  "
              f"alpha={d['alpha_pct']:+.3f}%  fills={d['n_fills']}")
    print("\n=== illustrative windows ===")
    by_alpha = sorted(results, key=lambda r: r[3]["alpha_pct"])
    show("worst alpha (bleed) ", by_alpha[0])
    show("best alpha (harvest)", by_alpha[-1])
    mid = min(results, key=lambda r: abs(r[3]["alpha_pct"]))
    show("near-zero (uncertain)", mid)


if __name__ == "__main__":
    main()

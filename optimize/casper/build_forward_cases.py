"""Build DRAFT reality-anchored Casper eval cases from forward-realized labels.

Pipeline:
  1. Label every historical hourly bar by forward grid-alpha (forward_label.py).
  2. For sampled bars, reconstruct Casper's world_state indicator inputs the SAME
     way production does (observer.compute_indicators on the last 300 1h bars +
     1d/6h resampled from 1h, strictly up to the decision bar — no lookahead).
  3. Emit draft cases in the evals/casper/dataset.jsonl shape, ground_truth = the
     forward-realized label, with full diagnostics in metadata for audit.

Deliberately oversamples the item-0* danger regime (TRENDING/bearish at low daily
ADX + adx_neg>adx_pos + negative roc_6h — the under-called downtrend the current
8-case set entirely lacks) plus the in-band boundary and clean RANGING/bullish.

Writes optimize/casper/forward_cases.draft.jsonl — does NOT touch the live suite.
Operator reviews the printed table before anything is promoted.

Run (main venv):
    /root/xrp_grid/venv/bin/python optimize/casper/build_forward_cases.py
"""

import json
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
sys.path.insert(0, str(_HERE))   # forward_label
sys.path.insert(0, str(_REPO))   # observer / database / config

# Reuse the live indicator pipeline, but no-op adam.init so importing observer
# does not spin up Sentry for an offline build.
import magi.adam as _adam
_adam.init = lambda *a, **k: None
from observer import compute_indicators, _resample_6h_from_1h  # noqa: E402

from forward_label import simulate, label, SPACING_PCT, N_LEVELS, WINDOW_H, FEE_FLOOR  # noqa: E402

DB = _REPO / "observer.db"
OUT = _HERE / "forward_cases.draft.jsonl"
HIST_1H = 7200          # ~300 days of 1h history fed to reconstruction (ema_200 needs 200 daily)
RNG = random.Random(42)


def _resample_1d_from_1h(candles_1h):
    """1h -> 1d OHLC, mirroring observer._resample_6h_from_1h (UTC-day buckets)."""
    buckets = {}
    for c in candles_1h or []:
        try:
            dt = datetime.fromisoformat(str(c.get("timestamp")).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            epoch = int(dt.timestamp())
        except Exception:
            continue
        buckets.setdefault(epoch // (24 * 3600), []).append((epoch, c))
    out = []
    for key in sorted(buckets):
        bars = [c for _, c in sorted(buckets[key], key=lambda x: x[0])]
        out.append({
            "timestamp": datetime.fromtimestamp(key * 24 * 3600, tz=timezone.utc).isoformat(),
            "open": float(bars[0]["open"]),
            "high": max(float(b["high"]) for b in bars),
            "low": min(float(b["low"]) for b in bars),
            "close": float(bars[-1]["close"]),
            "volume": sum(float(b.get("volume") or 0) for b in bars),
        })
    if out and len(buckets[sorted(buckets)[-1]]) < 24:
        out = out[:-1]    # drop incomplete trailing day
    return out


def load_bars():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT timestamp, open, high, low, close, volume FROM candles "
        "WHERE timeframe='1h' ORDER BY timestamp ASC"
    ).fetchall()
    conn.close()
    return [{"timestamp": t, "open": o, "high": h, "low": l, "close": c, "volume": v}
            for (t, o, h, l, c, v) in rows
            if None not in (o, h, l, c)]


def reconstruct_indicators(bars, i):
    """Casper's world_state indicators as of bar i (no lookahead). None on failure."""
    lo = max(0, i - HIST_1H)
    window = bars[lo:i + 1]
    c1h = window[-300:]
    c1d = _resample_1d_from_1h(window)[-300:]
    c6h = _resample_6h_from_1h(window[-600:])[-100:]
    if len(c1h) < 50:
        return None
    return compute_indicators(c1h, c6h, c1d, [])


def ema_dist(price, ema200):
    return None if not ema200 else round((price - ema200) / ema200 * 100, 2)


def structure_ok(ind, price, lab, direction):
    """Keep only cases where decision-time indicator STRUCTURE agrees with the
    forward-realized label — so we test regime reads reality confirmed, not
    un-callable reversals. The deliberate exception is the low-ADX bearish-base
    'danger' band selected separately: there the present is ambiguous and reality
    resolves it, which is the item-0* gold case."""
    e50, e200 = ind.get("ema_50"), ind.get("ema_200")
    adx, ap, an = ind.get("adx"), ind.get("adx_pos"), ind.get("adx_neg")
    if None in (e50, e200, adx):
        return False
    if lab == "TRENDING" and direction == "bearish":
        return e50 < e200 and price < e200 and (an or 0) > (ap or 0)
    if lab == "TRENDING" and direction == "bullish":
        return e50 > e200 and price > e200 and (ap or 0) > (an or 0)
    if lab == "RANGING":
        return adx < 25      # not an obvious trend by ADX -> RANGING is unambiguous
    return True


PROMPT = (
    "Classify the market regime for this cycle using your decision tree, then "
    "respond with your RegimeVote."
)


def make_case(case_id, bars, i, lab, direction, diag, ind):
    price = round(bars[i]["close"], 5)
    ws = {
        "timestamp": bars[i]["timestamp"],
        "price": price,
        "indicators": {k: ind.get(k) for k in (
            "current_price", "ema_50", "ema_200", "adx", "adx_pos", "adx_neg",
            "roc_6h", "bb_width", "bb_upper", "bb_lower", "btc_ema_50",
            "btc_ema_200", "atr_percentile", "autocorr_1h", "autocorr_4h")},
        "grid_state": {}, "inventory": {},
        "open_orders": {"buy_count": 5, "sell_count": 5},
        "hours_since_last_fill": 1.0, "hours_since_last_rebuild": 2.0,
        "trajectory": [], "market_knowledge": None, "hard_rules": {},
    }
    ws["indicators"]["current_price"] = price
    return {
        "id": case_id,
        "input": PROMPT,
        "ground_truth": lab,
        "agent_args": {"world_state": ws},
        "tags": ["casper", "forward_realized", f"{lab.lower()}_{direction}"],
        "metadata": {
            "source": "forward_realized_grid_alpha",
            "label_basis": (f"grid alpha vs hold over {WINDOW_H}h "
                            f"(spacing={SPACING_PCT*100:.2f}%, {N_LEVELS} levels); "
                            f"fee_floor={FEE_FLOOR:.2f}%"),
            "direction": direction,
            "drift_pct": round(diag["drift_pct"], 2),
            "alpha_pct": round(diag["alpha_pct"], 3),
            "n_fills": diag["n_fills"],
            "p0": round(diag["p0"], 5), "p_end": round(diag["p_end"], 5),
            "recon_adx": ind.get("adx"), "recon_adx_pos": ind.get("adx_pos"),
            "recon_adx_neg": ind.get("adx_neg"), "recon_roc_6h": ind.get("roc_6h"),
            "ema_distance_pct": ema_dist(price, ind.get("ema_200")),
        },
    }


def main():
    bars = load_bars()
    print(f"loaded {len(bars)} hourly bars  {bars[0]['timestamp']} -> {bars[-1]['timestamp']}")

    # Tuple view aligned 1:1 with `bars` (same index) for the labeler, which
    # indexes (timestamp, high, low, close) positionally.
    bars_t = [(b["timestamp"], b["high"], b["low"], b["close"]) for b in bars]

    # 1. Label everything.
    labeled = []
    for i in range(0, len(bars) - WINDOW_H - 1):
        d = simulate(bars_t, i)
        lab, direction = label(d)
        labeled.append((i, lab, direction, d))

    rng_pool = [r for r in labeled if r[1] == "RANGING"]
    tb_pool  = [r for r in labeled if r[1] == "TRENDING" and r[2] == "bearish"]
    tu_pool  = [r for r in labeled if r[1] == "TRENDING" and r[2] == "bullish"]
    bnd_pool = [r for r in labeled if r[1] in ("RANGING", "TRENDING")
                and FEE_FLOOR < abs(r[3]["alpha_pct"]) < FEE_FLOOR + 0.4]
    print(f"pools: RANGING={len(rng_pool)} TREND-bear={len(tb_pool)} "
          f"TREND-bull={len(tu_pool)} boundary={len(bnd_pool)}\n")

    # 2. Restrict to bars old enough that ema_200 is reconstructable.
    MIN_I = 210 * 24
    for p in (rng_pool, tb_pool, tu_pool, bnd_pool):
        RNG.shuffle(p)

    cases, used = [], set()
    cid = 0

    # Spread selections across distinct market episodes — without this, random
    # sampling clusters (e.g. 3 danger cases from one April-2022 downtrend week).
    chosen_epochs = []
    MIN_GAP = 10 * 86400   # >=10 days between any two selected cases

    def _ep(i):
        s = str(bars[i]["timestamp"]).replace("Z", "").split("+")[0]
        return datetime.fromisoformat(s).timestamp()

    def spaced(i):
        e = _ep(i)
        return all(abs(e - c) >= MIN_GAP for c in chosen_epochs)

    def take(rec, want_tag):
        nonlocal cid
        i = rec[0]
        if i in used or i < MIN_I or not spaced(i):
            return False
        ind = reconstruct_indicators(bars, i)
        if not ind or ind.get("ema_200") is None or ind.get("adx") is None:
            return False
        if not structure_ok(ind, bars[i]["close"], rec[1], rec[2]):
            return False
        cid += 1
        used.add(i)
        chosen_epochs.append(_ep(i))
        cases.append((make_case(cid, bars, i, rec[1], rec[2], rec[3], ind), want_tag))
        return True

    # 2a. The headline add: item-0* danger regime — bearish trend, LOW daily ADX,
    #     adx_neg>adx_pos, negative roc_6h. Scan the bearish-trend pool until we
    #     have 5 (or exhaust a generous scan budget).
    got = 0
    for rec in tb_pool:
        if got >= 5:
            break
        i = rec[0]
        if i in used or i < MIN_I or not spaced(i):
            continue
        ind = reconstruct_indicators(bars, i)
        if not ind or ind.get("ema_200") is None or ind.get("adx") is None:
            continue
        e50, e200, price = ind.get("ema_50"), ind.get("ema_200"), bars[i]["close"]
        bearish_struct = (e50 is not None and e200 is not None and e50 < e200 and price < e200)
        adx, ap, an, roc = ind["adx"], ind.get("adx_pos"), ind.get("adx_neg"), ind.get("roc_6h")
        if (adx < 20 and ap is not None and an is not None and an > ap
                and (roc or 0) < 0 and bearish_struct):
            cid += 1
            used.add(i)
            chosen_epochs.append(_ep(i))
            cases.append((make_case(cid, bars, i, rec[1], rec[2], rec[3], ind),
                          "danger: bearish base, low ADX (item-0*)"))
            got += 1

    # 2b. Clean strong bearish trend (high ADX).
    got_hi = 0
    for rec in tb_pool:
        if got_hi >= 2:
            break
        i = rec[0]
        if i in used or i < MIN_I or not spaced(i):
            continue
        ind = reconstruct_indicators(bars, i)
        if not ind or ind.get("adx") is None or ind.get("ema_200") is None:
            continue
        if ind["adx"] > 25 and structure_ok(ind, bars[i]["close"], rec[1], rec[2]):
            cid += 1
            used.add(i)
            chosen_epochs.append(_ep(i))
            cases.append((make_case(cid, bars, i, rec[1], rec[2], rec[3], ind),
                          "clean strong bearish trend (high ADX)"))
            got_hi += 1

    # 2c / 2d / 2e: bullish trend, ranging, boundary.
    for pool, n, tag in ((tu_pool, 2, "bullish trend"),
                         (rng_pool, 3, "clean ranging (harvest)"),
                         (bnd_pool, 2, "in-band boundary")):
        c = 0
        for rec in pool:
            if c >= n:
                break
            if take(rec, tag):
                c += 1

    # 3. Write + report.
    with OUT.open("w", encoding="utf-8") as f:
        for case, _ in cases:
            f.write(json.dumps(case) + "\n")

    print(f"=== {len(cases)} draft cases -> {OUT} ===")
    hdr = (f"{'#':>2} {'date':19} {'GT':9} {'dir':7} {'drift%':>7} {'alpha%':>7} "
           f"{'fills':>5} {'adx':>5} {'+DI':>5} {'-DI':>5} {'roc6h':>6} {'emaDist%':>8}  note")
    print(hdr)
    print("-" * len(hdr))
    for case, tag in cases:
        m = case["metadata"]
        print(f"{case['id']:>2} {case['agent_args']['world_state']['timestamp'][:19]:19} "
              f"{case['ground_truth']:9} {m['direction']:7} {m['drift_pct']:>7.2f} "
              f"{m['alpha_pct']:>7.3f} {m['n_fills']:>5} "
              f"{(m['recon_adx'] or 0):>5.1f} {(m['recon_adx_pos'] or 0):>5.1f} "
              f"{(m['recon_adx_neg'] or 0):>5.1f} {(m['recon_roc_6h'] or 0):>6.2f} "
              f"{(m['ema_distance_pct'] or 0):>8.2f}  {tag}")


if __name__ == "__main__":
    main()

"""IREUL Phase 2 — deterministic k-NN matcher evaluation with the pre-committed
guard set. Criteria and design: 04_EXPERIMENTAL_IDEAS.md (Session 2026-07-04).

The question: at each query hour, retrieve the k most-similar PAST hours (by
z-scored feature distance) and use their known 72h outcomes as evidence. Does
the matched-set hostile rate predict the query's own forward outcome better
than the naive conditioning the council already gets (hostile rate given
sign(roc_72h) — "downtrend => bleed")?

Guards (all pre-committed):
  - HOLDOUT: rows >= 2025-07-04T00:00Z are never loaded. Opened once, ever,
    by operator decision — not by this script.
  - Walk-forward: candidates strictly earlier than query minus a 7-day PURGE.
  - Episode dedup: selected neighbors mutually >= 72h apart.
  - Non-overlapping queries: one per 72h step (labels of adjacent hours share
    ~71/72 of their forward window — not independent samples).
  - z-scoring stats from the candidate set only (prefix sums — no future data).
  - Block bootstrap CI (blocks of 10 consecutive queries ~ 30 days).
  - Circular-shift permutation null (>=200 shifts, min 30 days): destroys the
    feature->label link, preserves label autocorrelation; neighbors are
    feature-only so they stay fixed — this nulls the WHOLE pipeline.
  - Trials ledger: every run appends config+results to trials.jsonl.

PASS requires ALL FOUR (evaluated on dev 2017-07 .. 2025-07):
  1. AUC(matcher) - AUC(baseline) > 0 with 95% block-bootstrap CI excluding 0
  2. mean forward alpha of top-quartile-predicted-hostile queries at least
     0.50pp (the maker round-trip floor) worse than the dev mean
  3. observed AUC lift > 95th percentile of the permutation null
  4. top-quartile hostile-rate lift positive in >= 75% of dev years
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "ireul.db")
LEDGER = os.path.join(HERE, "trials.jsonl")

FEATURES = ["roc_6h", "roc_24h", "roc_72h", "vol_sigma_pct", "regime_er",
            "drawdown_24h", "drawdown_7d", "dist_ema50d", "dist_ema200d"]
K = 25
PURGE_MS = 7 * 24 * 3600_000
SEP_MS = 72 * 3600_000
QUERY_STEP = 72                 # hours between queries (non-overlapping windows)
HOLDOUT_START_MS = int(datetime(2025, 7, 4, tzinfo=timezone.utc).timestamp() * 1000)
N_BOOT = 1000
BOOT_BLOCK = 10                 # queries per block (~30 days)
N_PERM = 200
PERM_MIN_SHIFT_H = 720          # 30 days
RNG_SEED = 20260704             # fixed: runs are reproducible, ledger-comparable


def auc(scores, labels):
    """Rank-based AUC (Mann-Whitney with average ranks for ties)."""
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=int)
    n1 = int(labels.sum())
    n0 = len(labels) - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores))
    sorted_scores = scores[order]
    i = 0
    r = 1.0
    while i < len(scores):
        j = i
        while j + 1 < len(scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i:j + 1]] = (r + (r + (j - i))) / 2.0
        r += (j - i) + 1
        i = j + 1
    return (ranks[labels == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def main():
    t0 = time.time()
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cols = ", ".join(FEATURES)
    rows = conn.execute(
        f"SELECT ts_ms, {cols}, alpha_pct, hostile FROM hours "
        f"WHERE ts_ms < ? AND hostile >= 0 AND "
        + " AND ".join(f"{f} IS NOT NULL" for f in FEATURES)
        + " ORDER BY ts_ms ASC", (HOLDOUT_START_MS,)).fetchall()
    conn.close()

    ts = np.array([r[0] for r in rows], dtype=np.int64)
    X = np.array([r[1:1 + len(FEATURES)] for r in rows], dtype=float)
    alpha = np.array([r[-2] for r in rows], dtype=float)
    hostile = np.array([r[-1] for r in rows], dtype=int)
    n = len(rows)
    print(f"dev rows: {n}  ({datetime.utcfromtimestamp(ts[0]/1000):%Y-%m-%d} .. "
          f"{datetime.utcfromtimestamp(ts[-1]/1000):%Y-%m-%d})  "
          f"base hostile rate {hostile.mean():.3f}")

    # Prefix sums for O(1) candidate stats at any cutoff.
    csum = np.cumsum(X, axis=0)
    csq = np.cumsum(X * X, axis=0)
    sign_pos = (X[:, FEATURES.index("roc_72h")] >= 0).astype(int)
    c_pos = np.cumsum(sign_pos)
    c_pos_h = np.cumsum(sign_pos * hostile)
    c_h = np.cumsum(hostile)

    # Queries: every QUERY_STEP-th eligible row, skipping early rows with a
    # too-small candidate pool (5000h ~ 7 months; also guarantees enough
    # mutually-72h-separated candidates to fill K=25).
    MIN_CANDS = 5000
    q_idx = []
    for qi in range(0, n, QUERY_STEP):
        cut = int(np.searchsorted(ts, ts[qi] - PURGE_MS, side="right"))
        if cut >= MIN_CANDS:
            q_idx.append(qi)
    q_idx = np.array(q_idx)
    nq = len(q_idx)
    print(f"queries: {nq} (step {QUERY_STEP}h, purge 7d, min pool {MIN_CANDS})")

    # Per-query retrieval.
    p_hat = np.empty(nq)          # matched-set hostile rate
    b_hat = np.empty(nq)          # baseline: candidate hostile rate given roc sign
    cuts = np.empty(nq, dtype=int)
    neigh = np.empty((nq, K), dtype=int)
    for out_i, qi in enumerate(q_idx):
        cut = int(np.searchsorted(ts, ts[qi] - PURGE_MS, side="right"))
        cuts[out_i] = cut
        mu = csum[cut - 1] / cut
        var = np.maximum(csq[cut - 1] / cut - mu * mu, 1e-12)
        sd = np.sqrt(var)
        d2 = (((X[:cut] - X[qi]) / sd) ** 2).sum(axis=1)
        order = np.argsort(d2, kind="stable")
        picked = []
        for ci in order:
            t_c = ts[ci]
            if all(abs(int(t_c) - int(ts[p])) >= SEP_MS for p in picked):
                picked.append(ci)
                if len(picked) == K:
                    break
        if len(picked) < K:
            raise RuntimeError(
                f"query at index {qi} filled only {len(picked)}/{K} separated "
                f"neighbors from a pool of {cut} — raise MIN_CANDS")
        neigh[out_i] = picked
        p_hat[out_i] = hostile[picked].mean()
        if sign_pos[qi]:
            n_s, n_sh = c_pos[cut - 1], c_pos_h[cut - 1]
        else:
            n_s = cut - c_pos[cut - 1]
            n_sh = c_h[cut - 1] - c_pos_h[cut - 1]
        b_hat[out_i] = (n_sh / n_s) if n_s else (c_h[cut - 1] / cut)

    y = hostile[q_idx]
    a_q = alpha[q_idx]
    auc_m = auc(p_hat, y)
    auc_b = auc(b_hat, y)
    auc_diff = auc_m - auc_b
    print(f"AUC matcher {auc_m:.4f}  baseline {auc_b:.4f}  diff {auc_diff:+.4f}")

    # (1) block bootstrap CI on the AUC difference
    rng = np.random.default_rng(RNG_SEED)
    n_blocks = int(np.ceil(nq / BOOT_BLOCK))
    starts = np.arange(0, nq, BOOT_BLOCK)
    diffs = np.empty(N_BOOT)
    for b in range(N_BOOT):
        sel = np.concatenate([
            np.arange(s, min(s + BOOT_BLOCK, nq))
            for s in rng.choice(starts, size=n_blocks, replace=True)])[:nq]
        diffs[b] = auc(p_hat[sel], y[sel]) - auc(b_hat[sel], y[sel])
    ci_lo, ci_hi = np.nanpercentile(diffs, [2.5, 97.5])
    pass1 = bool(ci_lo > 0)
    print(f"[1] bootstrap 95% CI of diff: [{ci_lo:+.4f}, {ci_hi:+.4f}]  "
          f"{'PASS' if pass1 else 'FAIL'}")

    # (2) economics: top-quartile predicted-hostile vs dev mean alpha
    thr = np.quantile(p_hat, 0.75)
    topq = p_hat >= thr
    econ_diff = a_q[topq].mean() - a_q.mean()
    pass2 = bool(econ_diff <= -0.50)
    print(f"[2] mean alpha top-quartile {a_q[topq].mean():+.3f} vs dev {a_q.mean():+.3f} "
          f"(diff {econ_diff:+.3f}pp, need <= -0.50)  {'PASS' if pass2 else 'FAIL'}")

    # (3) circular-shift permutation null (neighbors + cutoffs fixed; labels shift)
    null = np.empty(N_PERM)
    max_shift = n - PERM_MIN_SHIFT_H
    for p in range(N_PERM):
        s = int(rng.integers(PERM_MIN_SHIFT_H, max_shift))
        h2 = np.roll(hostile, s)
        cp2 = np.cumsum(sign_pos * h2)
        ch2 = np.cumsum(h2)
        p2 = h2[neigh].mean(axis=1)
        b2 = np.empty(nq)
        for out_i, qi in enumerate(q_idx):
            cut = cuts[out_i]
            if sign_pos[qi]:
                n_s, n_sh = c_pos[cut - 1], cp2[cut - 1]
            else:
                n_s = cut - c_pos[cut - 1]
                n_sh = ch2[cut - 1] - cp2[cut - 1]
            b2[out_i] = (n_sh / n_s) if n_s else (ch2[cut - 1] / cut)
        y2 = h2[q_idx]
        null[p] = auc(p2, y2) - auc(b2, y2)
    p95 = np.nanpercentile(null, 95)
    pass3 = bool(auc_diff > p95)
    print(f"[3] permutation null 95th pct {p95:+.4f} (observed {auc_diff:+.4f})  "
          f"{'PASS' if pass3 else 'FAIL'}")

    # (4) per-year robustness of top-quartile hostile-rate lift
    years = np.array([datetime.utcfromtimestamp(t / 1000).year for t in ts[q_idx]])
    yr_lift = {}
    for yr in sorted(set(years.tolist())):
        m = years == yr
        if m.sum() < 20:
            continue
        thr_y = np.quantile(p_hat[m], 0.75)
        lift = y[m & (p_hat >= thr_y)].mean() - y[m].mean()
        yr_lift[int(yr)] = round(float(lift), 4)
    n_pos = sum(1 for v in yr_lift.values() if v > 0)
    pass4 = bool(n_pos >= 0.75 * len(yr_lift))
    print(f"[4] per-year top-quartile lift: {yr_lift}  positive {n_pos}/{len(yr_lift)}  "
          f"{'PASS' if pass4 else 'FAIL'}")

    overall = pass1 and pass2 and pass3 and pass4
    print(f"\n=== PHASE 2 {'PASS' if overall else 'FAIL'} "
          f"({sum([pass1, pass2, pass3, pass4])}/4 criteria) in {time.time()-t0:.0f}s ===")

    record = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trial": "knn_eval",
        "config": {"features": FEATURES, "k": K, "purge_days": 7,
                   "neighbor_sep_h": 72, "query_step_h": QUERY_STEP,
                   "holdout_start_utc": "2025-07-04T00:00Z",
                   "n_boot": N_BOOT, "boot_block": BOOT_BLOCK,
                   "n_perm": N_PERM, "perm_min_shift_h": PERM_MIN_SHIFT_H,
                   "rng_seed": RNG_SEED, "min_cands": MIN_CANDS},
        "data": {"dev_rows": int(n), "n_queries": int(nq),
                 "base_hostile_rate": round(float(hostile.mean()), 4)},
        "results": {"auc_matcher": round(float(auc_m), 4),
                    "auc_baseline": round(float(auc_b), 4),
                    "auc_diff": round(float(auc_diff), 4),
                    "boot_ci95": [round(float(ci_lo), 4), round(float(ci_hi), 4)],
                    "econ_diff_pp": round(float(econ_diff), 4),
                    "perm_null_p95": round(float(p95), 4),
                    "per_year_lift": yr_lift,
                    "criteria": {"1_skill_ci": pass1, "2_economics": pass2,
                                 "3_permutation": pass3, "4_per_year": pass4},
                    "phase2_pass": overall},
    }
    with open(LEDGER, "a") as f:
        f.write(json.dumps(record) + "\n")
    out_path = os.path.join(HERE, f"results_{record['run_at_utc'][:10]}.json")
    with open(out_path, "w") as f:
        json.dump(record, f, indent=1)
    print(f"ledger appended -> {LEDGER}\nresults -> {out_path}")


if __name__ == "__main__":
    main()

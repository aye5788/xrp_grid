# IREUL — situation-indexed recall sandbox (OFFLINE, not adopted)

Named for the Eleventh Angel — the adaptive intelligence that invaded the MAGI's
own computers — inverted: this one is domesticated and works FOR the council.

**Design + pre-committed pass/kill criteria live in `04_EXPERIMENTAL_IDEAS.md`
(Session 2026-07-04). Read that first — this README is operational only.**

The question under test (Phase 2): does similarity retrieval over 9.5 years of
labeled hourly market history predict whether the NEXT 72h is grid-hostile
better than the naive "downtrend → bleed" conditioning the council already gets
from its indicators? If no → the project dies here (a learned matcher finding
signal where the deterministic one found none is datamining).

## Files

- `build_labels.py` — Phase 1. Reads `tape/history.db` (read-only), labels every
  hourly bar with `grid/forward_sim.simulate` at the live grid config
  (2.5% spacing / 5 levels / 72h window / maker fees), computes the fixed
  trial-#1 feature vector, writes `ireul.db`.
- `knn_eval.py` — Phase 2. Deterministic k-NN evaluation with the full guard
  set (purge, episode dedup, walk-forward candidates, block-bootstrap CI,
  circular-shift permutation null, per-year robustness). Appends every run to
  `trials.jsonl` and writes `results_<n>.json`.
- `trials.jsonl` — the multiple-testing ledger. EVERY evaluation run appends a
  row (full config + results), no exceptions, so the final claim can be deflated
  by the number of shots taken.
- `ireul.db` — generated artifact (gitignored): labeled feature table.

## Holdout — DO NOT TOUCH

Rows from **2025-07-04T00:00Z onward are FROZEN**: never used as queries, never
evaluated, opened ONCE ever by explicit operator decision. `knn_eval.py` refuses
to look at them. All development happens on 2017-07 → 2025-07.

## Running (droplet-safe: read-only on history.db, nice'd, numpy-light)

```
cd /root/xrp_grid
nice -n 19 .venv/bin/python3 optimize/ireul/build_labels.py   # ~minutes, once
nice -n 19 .venv/bin/python3 optimize/ireul/knn_eval.py       # seconds-minutes
```

Phase 3 (learned matcher), if Phase 2 passes, runs on the operator's desktop —
never the droplet.

# R&D — Local LLM Fine-Tuning Experiment

## Purpose

This is a research track that lives separately from MAGI production. It tests
whether training-based shaping of small local LLMs can produce agents that
genuinely internalize their task — rather than relying on persona prompts plus
hard-rule overrides to compensate for under-shaped behavior.

This is **not** an effort to replace the current Letta Cloud MAGI agents. It
is an effort to test a methodology. If fine-tuning produces qualitatively
different behavior than the current prompt-engineering approach (better
calibration, fewer overrides needed, more consistent reasoning), the
methodology may eventually inform how MAGI agents are built — whether via
local models, fine-tuned cloud models (e.g. Gemini tuning), or some hybrid.

The first agent under study is Casper because perception/classification is
the most tractable starting point. If the methodology works for Casper, it
extends to Melchior and Balthasar later.

## Motivation

Two observations drove this work:

1. **Letta's framework promised characterized, stateful agents but in practice
   the "tuning" has been entirely prompt-engineering + memory blocks.** No
   actual model weights have been shaped. Personas drift across sessions.
   Memory summaries compress history but do not retrain behavior.

2. **The hard-rule sprawl in `orchestrator.py` is symptomatic.** Every
   override rule we have added is implicit evidence that the agent is not
   doing what it should. If Casper were genuinely shaped into a regime
   classifier with appropriate caution, `[REGIME_STANDDOWN]` and similar
   overrides would be safety nets rather than primary governance. They are
   not — they are doing the agent's job for it.

The research question is: **can training-based shaping replace prompt-based
shaping as the primary mechanism for agent behavior**, leaving hard rules in
their proper role as safety nets only.

## Scope

- Workspace: `~/casper-local/` on the operator's local machine (Windows + RTX 3060 12 GB VRAM)
- Production droplet (`/root/xrp_grid`) is **not** touched by this work
- Data is independent: pulled from Bitstamp public OHLC API, not from MAGI's SQLite
- No Letta credits consumed by this research
- This is paper R&D until results justify otherwise

## Current state (as of 2026-05-25)

### Data layer — COMPLETE

- `data/raw/xrpusd_1h.parquet` — 82,732 hourly XRP/USD candles from Bitstamp,
  2016-12-16 → 2026-05-25, zero gaps, validated
- Source: Bitstamp public REST API (`api.bitstamp.net/api/v2/ohlc/xrpusd/`),
  paginated via `start`/`end`, no auth, no rate-limit issues
- Forward-look discipline enforced and verified throughout

### Labels — COMPLETE (multiple versions, kept for traceability)

- `xrpusd_1h_labels.parquet` (v1) — three-class regime via vol+drift ratio
  with thresholds 0.4 / 0.9, balanced ~33/33/33 on non-thin rows. This is
  the regime label that maps to Casper's actual RANGING/TRENDING/UNCERTAIN
  output schema.
- `xrpusd_1h_labels_v2.parquet` — six-class regime with breakout as a state.
  Deprecated due to structural problems (CHOPPY won argmax 81% of the time,
  BREAKOUT was mathematically unreachable). Kept as a diagnostic record.
- `xrpusd_1h_labels_v3.parquet` — five-class regime with empirically-derived
  amplitude bands (RANGE_BOUND / CHOPPY / WIDE_CHOPPY / TREND_UP / TREND_DOWN)
  plus is_breakout flag and grid_fitness score. Well-balanced. Forward-look
  verified.
- `xrpusd_1h_labels_amplitude.parquet` — three-class amplitude-only labels
  (LOW_AMP / MID_AMP / HIGH_AMP) derived from forward 24h range percentiles.
  Built during a scope detour that has since been corrected.

The structural vote label (EXECUTE / DEFER_STRUCTURAL / STAND_DOWN), which
Casper also emits in production, has not yet been generated. This is the
next labeling task.

### Features — COMPLETE (with one bug found and fixed)

- `data/features/xrpusd_1h_features.parquet` — 25 technical indicators,
  all scale-invariant or vol-normalized
- Bug found: `parkinson_vol_24_pct` was being divided by close, which is
  incorrect because Parkinson vol is already dimensionless. Fixed; column
  renamed to `parkinson_vol_24`. Spot value at 2017-03-17 corrected from
  2.40 to 0.015 (165× correction). Distribution now properly bounded
  [0.0005, 0.23].
- Forward-look verification passes on three random rows at 1e-9 precision

### XGBoost baseline — COMPLETE (on amplitude task, recently rebaselined post-fix)

- `models/xgb_amplitude.json` — three-class amplitude classifier trained on
  fixed features
- Test set: 12,208 rows, 2025-01-01 → 2026-05-24, non-thin only
- Test accuracy: 0.5192 (vs majority baseline 0.2279, random baseline 0.3289)
- Macro F1: 0.4886
- Per-class F1: LOW_AMP 0.627, MID_AMP 0.437, HIGH_AMP 0.401
- Calibration: monotonic, top-confidence bucket (>=0.8) hits 83% accuracy
- Canonical metrics archived in `models/xgb_amplitude_metrics.json`

XGBoost on the original three-class regime task (RANGING/TRENDING/UNCERTAIN —
Casper's actual output) **has not been re-run on fixed features**. The
previous attempt scored 0.30 accuracy, below the majority baseline, which led
to the amplitude scope detour. This needs to be re-done on fixed features
before the fine-tuning experiment can be properly evaluated.

### Local LLM stage — NOT STARTED

Hardware verified: RTX 3060, 12 GB VRAM, CUDA 12.1, PyTorch 2.5 with
bitsandbytes installed. No fine-tuning has occurred yet.

## Key findings so far

1. **24-hour forward direction is fundamentally hard to predict from 24-hour
   backward features.** XGBoost cannot extract directional signal for
   TREND_UP / TREND_DOWN states (F1 below 0.10 on both). This is a property
   of the data, not a methodology failure.

2. **24-hour forward amplitude IS predictable** at meaningful levels (52%
   accuracy on three-class, F1 0.49). The vol-clustering signal in ATR-like
   features carries real information. `atr_14_pct` alone has permutation
   importance of +0.16, with second-place features below +0.02 — this is
   nearly a one-feature problem.

3. **The vol+drift ratio metric used for labels is vol-invariant by
   construction**, which makes regime labels stationary across years even
   though XRP's underlying volatility regime is non-stationary (3.6× spread
   in annualized vol across years).

4. **The 2017 thin-market period is genuinely different.** Flagged via
   `thin_market` boolean rather than dropped, preserving the data for future
   experiments that may want to include or exclude it.

## What this experiment is testing (restated)

The experiment is **not**:
- A replacement for Casper
- A comparison against current Letta-Casper's accuracy
- An optimization for prediction accuracy alone

The experiment **is**:
- A test of whether QLoRA fine-tuning on a small local LLM (Qwen 2.5 3B) can
  produce a model that internalizes Casper's regime classification task
- A test of whether the resulting model is **calibrated** (confident on clear
  cases, uncertain on ambiguous ones)
- A test of whether it shows **consistent** behavior (same input → same output)
- A methodology test that, if successful, justifies investing in the same
  approach for Melchior and Balthasar — or for fine-tuned cloud models like
  Gemini once Google supports task-specific tuning

The evaluation metrics that matter:
- Accuracy and macro F1 (standard)
- Calibration (predicted probability vs actual hit rate)
- Consistency (run same input twice, compare outputs)
- Failure-mode sensibility (does the model fail in interpretable ways?)
- Comparison to XGBoost ceiling on the same task

If the fine-tuned model is well-calibrated at, say, 45% accuracy with sensible
failure modes, that is a better result than 55% accuracy with poor calibration.
Calibration is what lets the production system **remove hard-rule overrides**
in favor of confidence-thresholded gating. That is the actual goal.

## Next steps (in order)

1. **Generate structural-vote labels.** Algorithmic definition of EXECUTE /
   DEFER_STRUCTURAL / STAND_DOWN based on forward grid-fitness. Save as a new
   column on the regime label file or as a separate parquet.

2. **Re-run XGBoost on Casper's actual three-class regime task** using fixed
   features. Establishes the tabular ceiling on the real task, not the
   amplitude proxy.

3. **Run XGBoost on structural-vote task.** Same setup. Captures whether the
   structural vote is even learnable from these features.

4. **Build the fine-tune pipeline.** QLoRA on Qwen 2.5 3B, compressed prompt
   format, batch=4-8 with grad accumulation, early stopping on val loss.
   Target training time under 1 hour per epoch.

5. **Build the evaluation framework.** A model-adapter abstraction so the
   same eval can run on the local fine-tuned model, a few-shot version, an
   XGBoost model, or (later) a cloud API. Reports accuracy + calibration +
   consistency + failure-mode analysis.

6. **Run the fine-tune.** First real test.

7. **Interpret.** Did training-based shaping produce calibrated, consistent
   behavior? If yes, methodology is validated and the approach extends to
   Melchior and Balthasar. If no, the limits of small-model fine-tuning
   become a constraint to plan around.

## What we will not do (decisions made and held)

- We will not evaluate against current Letta-Casper via API calls (Letta
  credits not justified for this research)
- We will not deploy the local model to MAGI production from this experiment
- We will not use third-party Kraken wrappers, vector DBs, Mem0, or any
  framework not in the MAGI production stack — the local R&D project follows
  the same dependency discipline as production
- We will not retrain on the original buggy features for any reason

## Repository

The R&D code lives at `~/casper-local/` on the operator's local machine.
Structure:
casper-local/
├── .venv/                    Python 3.10 virtual environment
├── data/
│   ├── raw/                  OHLCV parquet
│   ├── labels/               regime / amplitude label parquets
│   ├── features/             25-feature parquet
│   └── llm/                  JSONL training files (to be regenerated)
├── models/                   xgb_amplitude.json + metrics, future LLM artifacts
├── notebooks/                stationarity check + future exploration
├── src/                      fetch, features, labels, training scripts
└── logs/                     training and eval logs

The droplet (`/root/xrp_grid`) is not touched by this work. The MAGI
production system continues to run independently with no awareness of this
research.

## Success criteria: analyst vs Jim Cramer

The methodology question isn't "can a trained model match XGBoost accuracy."
It's whether the trained model produces outputs operationally trustworthy
enough that downstream policy can act on its confidence rather than overriding
it with hard rules. The bar is closer to "would a serious technical analyst be
respected" than "did the prediction match the label."

A serious analyst is someone whose predictions you can act on differently
depending on how confident they are. A Jim Cramer is someone whose confidence
is decorative.

Six qualities distinguish them:

1. **Puts numbers on predictions before outcomes.** Evaluable confidence, not
   vibes.
2. **Numbers approximately match reality.** Calibration — when they say 70%,
   they're right 70% of the time.
3. **Distinguishes confident-knowing from guessing.** Can commit to
   uncertainty on genuinely uncertain cases.
4. **Honest about base rates and method limits.** Acknowledges what TA can and
   can't do at given horizons.
5. **Failures are interpretable.** Wrong calls cluster near class boundaries or
   in identifiable regimes, not at random.
6. **Beats trivial baselines.** Majority, random, "predict yesterday."

The operational test: at confidence threshold X, model commits to Y% of test
rows and gets Z% correct. If high-confidence accuracy meaningfully exceeds
low-confidence accuracy, the model's confidence carries actionable information.
If not, the confidence is Cramer-level decoration regardless of headline
accuracy.

## Reinforcement framework for training shaping

The MAGI architecture currently inverts the agent-rule relationship: agents
emit suggestions, hard rules decide. We want the inverse — agent outputs are
primary, rules are guardrails for edge cases that fire rarely. For that
inversion to be defensible, the agent must earn it by being right when
confident, uncertain when ambiguous, consistent across identical inputs, and
interpretable in its failure modes.

Reinforcers identified:

| Reinforcer | Trainable | Notes |
|---|---|---|
| Correctness at stated confidence | Yes | Direct calibration signal |
| Useful confidence spread across population | Emergent | Measured in eval |
| Confidence-stratified accuracy | Emergent | High-conf accuracy must exceed low-conf accuracy |
| Boundary failures over random failures | Yes | Ordinal loss weighting |
| Consistency under repetition | Emergent | Tested via two-pass identical-input check |
| Honest uncertainty on ambiguous cases | Yes | DRA target — reinforce uncertainty on near-boundary cases |

Trainable reinforcers go into the loss function. Emergent reinforcers are
measured in eval to detect whether the training-side contingencies produced the
desired population-level behavior.

### Both positive and negative reinforcement, not punishment

Punishment teaches avoidance of the punished behavior, not understanding of why
it was wrong. A model punished for "overconfident wrong answers" has multiple
avoidance paths:

- Be uniformly low-confidence on everything (collapses useful spread)
- Pattern-match specific feature signatures that don't get punished
  (memorization)
- Output the training distribution regardless of input (blanket strategy)

Only one path produces the intended behavior (genuine calibrated judgment). The
others look like avoidance of the punishment signal without internalizing the
lesson.

Differential reinforcement of alternative behaviors (DRA) is non-negotiable:
explicitly reinforce both warranted high confidence and appropriate uncertainty
as positive behaviors. Reinforcing only one collapses the other (pure "be less
confident" pressure produces uniformly underconfident models; pure "be more
confident" pressure produces gambling behavior).

The literature term for the right loss structure is "proper scoring rule" — a
function whose expected value is maximized only by reporting your true
probability estimate. Log-loss and label-smoothed cross-entropy have this
property when implemented correctly. The training landscape is structurally
aversive (high loss when miscalibrated) and the path out is calibration. Not
punishment of overconfidence; negative reinforcement against miscalibration
with positive reinforcement for accurate self-report.

## Manufactured contingencies and the sculpting metaphor

We dictate the training reality. Contingencies the model encounters don't have
to mirror naturalistic conditions. If we want the model to learn from
being-bested (predictions were wrong AND an alternative was right), we can
construct training examples with synthetic "alternative model" predictions. The
reinforcement signal is structural, not metaphysical — the model experiences
the contingency, not the source.

This collapses the implementation complexity of inter-model training. No second
model required at training time. No additional API calls. Just training data
structured to present the contingencies we want shaped.

Sculpting metaphor for staging:

- **Phase 1 (wedge):** basic SFT with label smoothing. Establishes rough shape
  of the task. Heavy-handed, imprecise, fast. Don't try to do refined work
  here.
- **Phase 2 (chisel):** preference-based fine-tuning targeted at specific
  behaviors that survived Phase 1 imperfectly — calibration adjustment,
  boundary case sensitivity. Cal-DPO with difficulty-weighted preferences is
  the published methodology.
- **Phase 3 (rasp/refinement):** inter-model disagreement contingencies,
  multi-round refinement, attribution-of-failure signals. These refine specific
  competencies that earlier phases established approximately.

Each phase appropriate to where the work is. Don't use refined tools on
shapeless marble; don't use heavy tools on near-finished work.

Research literature grounding:

- Standard fine-tuning predictably breaks calibration (CogCalib 2025, multiple
  confirmations).
- Prior-knowledge overlap with fine-tuning data causes overconfidence; novel
  data improves calibration.
- Cal-DPO (NeurIPS 2024) is the validated method for calibration-aware
  preference training.
- Difficulty-based preference selection (2025) confirms boundary cases provide
  strongest learning signal.
- BaseCal (Jan 2026): base models more calibrated than instruct counterparts;
  usable as calibration anchor.

## Operational decision threshold for the research track

Methodology success isn't a single number — it's whether the model produces
operationally trustworthy calibrated outputs. The five-question check:

1. Is the model right often enough to be useful at all? (accuracy vs baselines)
2. When the model says it's confident, is it actually right? (high-confidence
   accuracy)
3. When the model says it's unsure, is it actually unsure? (low-confidence
   accuracy lower than high-confidence)
4. Does it produce a useful range of confidence levels? (confidence
   distribution shape)
5. Are the failures interpretable? (confusion matrix structure)

- **All five yes** → "serious analyst" model, methodology validated, worth
  continuing.
- **1 and 2 yes, others no** → "competent guesser without judgment,"
  methodology partially worked, needs Phase 2.
- **1 no** → methodology didn't work at this scale, R&D track stops.

This framing replaces ECE/accuracy targets as the primary verdict. The
operational test is what matters; numerical metrics are diagnostic of why the
operational test did or didn't pass.

## Status

Active. Currently at: features clean, XGBoost amplitude baseline established,
need to revisit regime task on fixed features and generate structural-vote
labels before starting the fine-tune.

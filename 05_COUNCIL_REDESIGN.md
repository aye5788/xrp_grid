# 05 — Blind-Review Council (READ FIRST for the decision layer)

**Written 2026-06-24 to orient a future Claude Code session.** As of this date the
MAGI **decision layer** was rebuilt from the old *arbiter relay* into a
**blind-review, equal-seats council**. This doc is authoritative for the council;
it **supersedes** the council/decision-layer descriptions in `CLAUDE.md` §2–§3 and
in `00`/`01`/`02`. Everything *below* the council — the engine (`grid/engine.py`),
the hard-rule layer (`orchestrator.enforce_hard_rules`), the gate, world_state, and
the data layout — is **unchanged**. If a council statement here disagrees with the
older docs, this doc wins.

---

## TL;DR — where we are right now (updated 2026-06-25)
- **Code:** branch `council-redesign`, latest commit `60cf20b` (the redesign
  `fc62b93`, the 2026-06-25 Langfuse/dashboard fixes in §7, and the CS1+CS2
  era-aware dashboard alignment in §7c). The branch **was pushed 2026-06-25**
  through `7090f50` (docs); the CS1/CS2 commits `4274f3e`+`60cf20b` and this doc
  update are **local until the next manual push**. **NOT merged to `main`** —
  `main` still holds the dead arbiter.
- **This box:** `observer.db` restored (brain through **2026-06-14**), `.env` →
  symlink to `/root/magi.env`, `.venv` built. The **trading engine (`magi.service`)
  is deliberately SHUT DOWN** (paper mode). The **dashboard IS running** —
  `magi-dashboard.service` under waitress on `:5000`, public at
  `https://api.ethobs.uk` (see §7).
- **Council is HEALTHY:** the Casper propose 400 is **FIXED** (§4). A full standalone
  smoke test produces a valid 3-seat decision (3/3 propose return 200, clear
  consensus, no council_error).
- **Langfuse instrumentation rebuilt** for the blind-review council; **per-seat
  symmetric grading + decision-quality scores + session grouping** shipped (§7).
- **Dashboard aligned to the redesign (era-aware, §7c):** seat-accuracy panel now
  shares the Langfuse grader, agent-health degraded-detection fixed, model labels
  sourced from `seats.MODELS`, NO_CONSENSUS / equal-seat / consensus vocabulary.
  **Finding:** `observer.db` has **zero blind-review rows** (engine never persisted
  one), so the redesign display paths are validated synthetically and first show real
  data at the next engine bring-up.

---

## 1. The new architecture

**Three EQUAL seats. No arbiter, no privileged seat, no synthesizer.** Governing
principles (settled — do not reopen):
- **P1 — equality:** the three seats are equals; none sees more or decides more.
- **P2 — minimal rules:** the council is central; do not add static governance that
  monitors/controls it, and do not bend the council to feed a monitor.
- **P3 — non-consensus is the council's output, never a deterministic rule:** there
  is **no most-reversible tiebreak** and **no external action-picker** anywhere.

**Seats (models are cost-matched; no premium synthesizer tier):**
| Seat | Vendor / model | Transport |
|---|---|---|
| Casper | Google `gemini-2.5-flash` | native Gemini via **ADK `output_schema`** |
| Melchior | DeepSeek `deepseek-v4-pro` | Anthropic-compat endpoint, `thinking` **disabled** |
| Balthasar | Anthropic `claude-haiku-4-5` | Anthropic (was `claude-sonnet-4-6` in the arbiter era — **changed**) |

**Mechanism (in `magi/council_v2.py:run_council`):**
1. **Phase 1 — propose:** 3 seats, in parallel, isolated (no peer context). Identical
   scaffold; persona is a reasoning *lens* only. Each returns a `CandidateDecision`.
2. **Phase 2 — review:** 3 seats, in parallel, cross-review the candidates with
   **authorship stripped + template normalized + shuffled to A/B/C** (per-cycle seed).
   Each returns a `Ranking` (best→worst).
3. **Phase 3 — aggregate (deterministic, pure):** Condorcet check → Borda fallback.
   A clear winner **is** the decision. A cycle/tie → `winner=None`.
4. **Reconciliation:** if no stable winner, ONE more round showing the anonymized
   split; seats revise; re-aggregate.
5. **NO_CONSENSUS:** if still no winner → first-class decision ("no mandate, nothing
   changes"). Not an error, not a fabricated rule.

**Action space (the only values a seat may propose, + the decision-only NO_CONSENSUS):**
`MAINTAIN | RECONFIGURE(geometry) | PAUSE_LONGS | PAUSE_SHORTS | STAND_ASIDE | HALT`
and, at the decision level only, `NO_CONSENSUS`. **Regime is an INPUT** carried in
`world_state` that all seats see — it is **not** a field any seat outputs (the old
Casper regime grader is retired).

**Seat-failure handling (honest, minimal):** retry once; if a clear tally remains
from the responders, proceed; otherwise NO_CONSENSUS; log plainly. **No fabricated
votes, no SAFE_DEFAULTS sentinels.** `council_error` is set **only** on a genuine
convene crash, never for a designed NO_CONSENSUS.

---

## 2. Code map + integration seam

**Changed/new files (all on `council-redesign`):**
| File | What |
|---|---|
| `magi/council_v2.py` | rewritten — the 5-step flow above |
| `magi/agents/aggregate.py` | **new** — Condorcet→Borda (no tiebreak); action→cons map |
| `magi/agents/anonymize.py` | **new** — authorship strip + seeded A/B/C shuffle |
| `magi/agents/seats.py` | **new** — symmetric `propose`/`review`; `MODELS`/`VENDORS` |
| `magi/agents/schemas.py` | `CandidateDecision` + `Ranking`; regime fields dropped |
| `database.py` | additive `council_json` column + `get_council_ledger` |
| `magi/orchestrator.py` | 8-line additive: persist `council_json` to debate_records |
| `observer.py` | retired the Casper regime grader |

**The seam (unchanged from the arbiter era — the redesign plugs into it cleanly):**
- `run_council(world_state, cycle_id, trigger=None) -> (round_0, round_1, cons)` —
  **same signature/return** as before. Called at `orchestrator.py:run_cycle`.
- The council emits **`grid_verdict`** (not `grid_action`). `enforce_hard_rules`
  translates it via `_VERDICT_TO_GRID_ACTION`:
  `THESIS_HOLDS→MAINTAIN`, `RECONFIGURE→RECENTRE`, `NO_PROFITABLE_GRID→GRID_PAUSE`.
  The redesign's `aggregate._ACTION_TO_CONS` emits **only those three** verdict
  values, so the unchanged table consumes them with no gaps.
- `cons` carries the 3 **gating** axes (`grid_verdict`, `stance`, `risk_action`),
  the record-only axes as `None` (`regime`, `regime_action`, `geometry_veto`,
  `override_justification` → NULL columns), and `council_json`.
- Engine geometry channel: winner geometry is written to
  `round_0['melchior']['geometry']`, which `_final_consensus` forwards as
  `melchior_geometry`. **Engine and hard-rule layer are not modified.**

**Data:** `debate_records.council_json` holds
`{decision, vote_multiset (authorship-free, e.g. "2x MAINTAIN, 1x RECONFIGURE"),
consensus: clear|reconciled|none, reconciled: bool}`. `get_council_ledger` reads it,
filtered by `config_version`; it is the council's own replay-safe memory, injected
**identically** to all three seats. Historical (arbiter-era) rows have **NULL**
`council_json` and are excluded. `config_version` **bumps at cutover** (haiku +
`veto_mode="none_blind_review"`), so pre/post history partitions cleanly.

---

## 3. Current state (detail)

- **GitHub:** `council-redesign` @ `fc62b93` on `aye5788/xrp_grid`. `main` is still the
  arbiter — open a PR / merge when the council is healthy. Do **not** assume `main`
  reflects this design.
- **Brain restored from the GCS archive** (this is where the prior session stashed it):
  `gs://xrp-grid-tape-backups-ayn88/project-final-archive/magi-final-archive-2026-06-17.tar.gz`.
  Contains a full cold-restart guide (`magi_final_glue_2026-06-17/RESUME.md`), the
  consistent `observer.db`, tape DBs, systemd/nginx units, `.env`, and the `magi_docs`
  checkout. The restored `observer.db` (253 cycles, last 2026-06-14, `council_stance=DEPLOY`)
  was migrated by the redesign's `init_db` (added `council_json`, old rows NULL,
  integrity ok).
- **Env:** code does `load_dotenv()` expecting `/root/xrp_grid/.env`; that's a symlink
  to `/root/magi.env` (which holds all keys: ANTHROPIC/GOOGLE/DEEPSEEK + Langfuse/NTFY).
- **Smoke test (frozen ws from `cyc_1781395248`):** decision `STAND_ASIDE`
  (`grid_verdict=THESIS_HOLDS, stance=STAND_ASIDE, risk_action=PAUSE_LONGS`),
  consensus clear, no council_error, **no DB write** (the standalone runner does not
  insert a cycle row). After the §4 fix: **all 3 seats return 200 on propose** — the
  vote_multiset has 3 candidates (`2x STAND_ASIDE, 1x MAINTAIN`), confirming the
  equal-seats council is whole (was a 2-seat propose council while Casper 400'd).

---

## 4. RESOLVED BUG (was: "fix this first") — Casper propose 400, FIXED 2026-06-25

**FIX (commit `f0bc8f9`):** the nested `Geometry` model inside `CandidateDecision`
still carried `model_config = ConfigDict(extra="forbid")` — an arbiter-era leftover.
ADK's `output_schema` mirrors the pydantic schema and bypasses
`schema_for_tool`'s central `additionalProperties` strip, so Geometry emitted
`additionalProperties: false` and native Gemini 400'd on it. Flipping `Geometry` to
`extra="ignore"` (matching what `CandidateDecision`/`Ranking` already do for the same
reason) removes the key at the source. Verified: 3/3 propose calls 200, 3-candidate
vote_multiset. `requirements.txt` also gained the two deps the `.venv` rebuild was
missing (`google-adk`, `icontract`). The original diagnosis is kept below for context.

---

**(historical) Casper (Gemini) cannot PROPOSE.** Its propose call 400s:
`Invalid JSON payload ... Unknown name "additional_properties" at
response_schema.properties[1].value` — `properties[1]` is the nested **`Geometry`**
object in `CandidateDecision`.

**Root cause:** the Anthropic seats go through `magi/agents/schema_tools.py:schema_for_tool`,
which **strips `additionalProperties`** (native-Gemini rejects that key). Casper's
Gemini path in `seats.py:_call_gemini` passes the Pydantic model straight to **ADK
`output_schema`**, which **bypasses that strip**, so ADK serializes the nested geometry
with `additional_properties` and Gemini 400s. The flat `Ranking` schema has no nested
object, so Casper's *review* works — only *propose* is broken.

**Why it's new:** in the arbiter era Casper emitted a flat regime vote (no nested
object); only Melchior carried geometry, and Melchior is on the Anthropic path. Now
all three share `CandidateDecision` (nested geometry) and Casper-on-Gemini never got
the strip. **Net effect: a 2-seat propose council — breaks P1 (equal seats).**

**Fix:** apply the `additionalProperties` strip to the schema handed to ADK on the
Gemini path (mirror what `schema_for_tool` does for Anthropic) — e.g. sanitize the
schema dict recursively before ADK builds its `response_schema`, or route Casper
through a forced-function/tool schema like the other seats. **Verify** by re-running
the §5 smoke test and confirming **3/3 propose calls return 200** and a 3-candidate
`vote_multiset`.

---

## 5. How to run / revive

**Standalone council (cheap, no services, the test path):**
```
cd /root/xrp_grid
PYTHONPATH=/root/xrp_grid .venv/bin/python -m magi.council_v2 --json <ws.json> --cycle-id cyc_test
```
- Makes **real paid** seat calls. Without `--json` it calls `build_world_state()`
  (hits Kraken for price; may fire a schema-drift alert → phone).
- **Frozen ws fixture:** none ships in the repo. Get one by extracting the latest
  `debate_records.world_state` from `observer.db`, or by dumping `build_world_state()`.

**Dependency gap (IMPORTANT):** `requirements.txt` is **missing** `google-adk` and
`icontract` (it pins `google-genai`, not `google-adk`). Install both explicitly:
`.venv/bin/pip install google-adk icontract`. Note `google-adk` bumps `google-genai`
from the pinned 1.74.0 to ~2.10.0 (pip check stays clean). Add these to
`requirements.txt` for a reproducible fresh box.

**Full revival of the running system:** follow `RESUME.md` in the GCS archive's
`magi_final_glue_2026-06-17/` — it has the systemd/nginx units and the cold-restart
sequence. Keep PAPER safety: `MAGI_LIVE_CONFIRM=NO`, `CONFIRM_LIVE` disarmed, and
`system_state['paper_run_started_utc']` set (engine live-gate fails closed while it is).

---

## 6. What is now stale (don't be misled)
These describe the **dead arbiter** and do **not** reflect the running council:
- `CLAUDE.md` §2 "Layer 1 — Council judgment" and §3 (Balthasar-as-arbiter, sequential
  Casper→Melchior→Balthasar synthesis, R1 rebuttal, Melchior verdict-only).
- `00_PROJECT_OVERVIEW.md` council vote-field descriptions (regime/regime_action,
  geometry_veto, six-call choreography).
- `01_CURRENT_STATE.md` / `02_NEXT_BUILD_TASKS.md` references to the arbiter, the
  synthesis vote, and the regime grader.
The engine, hard rules, gate, data layout, paper/live toggle, and PnL scoping in
those docs are **still accurate** — only the council/decision-layer parts changed.

---

## 7. 2026-06-25 update — dashboard reconnect + Langfuse instrumentation rebuild

Two work areas landed after the Casper fix. The **trading engine stayed down**
throughout (paper, deliberate); none of this runs the engine. All commits are local
on `council-redesign`, not pushed.

### 7a. Dashboard reconnected publicly (commit `bba6218`)
The public path was broken at the infra level, not the app level:
- The box's `cloudflared` was running a **deleted tunnel** (`0a3c34dc…`), so its
  connector showed **"down"**; and `api.ethobs.uk` had **no public DNS** because it
  had been set up (in the Cloudflare dashboard) as a *private* application route
  (Gateway/WARP-only), not a public hostname.
- Fix: repointed the box to the real tunnel **`eth-observer` =
  `e4d95b41-e5fa-453a-b7ca-309703478094`**, now **locally-managed** via on-disk
  `/etc/cloudflared/config.yml` (ingress `api.ethobs.uk → http://localhost:5000`,
  catch-all 404) + credentials in `/root/.cloudflared/` (from `cloudflared tunnel
  login`). `cloudflared.service` ExecStart is now `--config … tunnel run` (the
  embedded `--token` is gone — chosen for on-disk reproducibility). Public DNS CNAME
  created with `cloudflared tunnel route dns`. cloudflared upgraded apt 2026.6.0 →
  2026.6.1.
- The dashboard now serves under **waitress** (a real WSGI server), not Flask's dev
  `app.run()`, via `magi-dashboard.service` (ExecStart uses **`.venv`**, not the
  archived unit's `venv/`). The `.venv` rebuild was missing `waitress` + `sentry-sdk`
  (the latter was the startup crash) — both now pinned.
- Verified end-to-end: `https://api.ethobs.uk/login → 200`; authenticated dashboard
  renders all panels. **NOTE:** `dashboard.ethobs.uk → localhost:8501` is a SEPARATE
  live Streamlit app — leave it alone.
- The dashboard reads only the legacy per-seat `*_r0_position` / `final_*` columns,
  which `council_v2._own_r0` still populates (Casper's column now carries an ACTION,
  not a regime — any "regime" label for Casper in the UI is now stale wording, not a
  break).

### 7b. Langfuse instrumentation rebuilt for the blind-review council
The plumbing (trace per cycle, 6 named seat generations, convergent 1h/6h outcome
delivery, deep-links) was intact; the eval/monitoring layer was thin/stale. Changes
(all observability-only — **nothing feeds back into a council decision or vote
weight**; the tally stays flat):
- **Seat grading made symmetric (B1, commit `f139fd0`).** The redesign retired
  Casper's regime grader but left only 2 of 3 seats graded (a P1 violation), and the
  Melchior/Balthasar graders only saw lossy verdict/risk *projections*. Fix: persist
  each seat's RAW proposed action in **3 additive columns** `casper_r0_action` /
  `melchior_r0_action` / `balthasar_r0_action` (written by `_build_debate_record`;
  kept OUT of `council_json` so grading authorship can't leak into the blind review),
  and grade all three co-equal seats with ONE anchored predicate
  (`database._grade_action_row`): grid-run/stop actions (MAINTAIN/RECONFIGURE/HALT)
  on grid-vs-hold alpha vs `FEE_FLOOR`; exposure-direction actions
  (STAND_ASIDE/PAUSE_LONGS/PAUSE_SHORTS) on realized forward price drift. Reuses the
  same `forward_sim` truth-standard as every other grader.
  `observer.backfill_seat_accuracy_scores` dispatches by era (blind-review rows →
  symmetric grader for all three; arbiter-era rows → legacy graders unchanged).
- **Decision-quality scores (B3) + sessions (B4), commit `5b01c34`.** On the 1h push,
  mirror from `council_json`: `decision_action`, `consensus_type`, `reconciled`,
  `vote_spread`, `vote_unanimous` (previously scored nowhere). And group cycles into
  Langfuse **sessions** (one per paper run, via `tracing.set_trace_session`).
- **Delivery alert (B5) + in-code score schema (B6), commit `f692e81`.** A sustained
  Langfuse outage now raises an edge-triggered `langfuse_delivery_degraded` warn alert
  (was silent retry); `observer.py` carries a SCORE SCHEMA reference block (every
  score family, type, maturity, owning function — all attach to the TRACE).
- **Deferred:** per-observation score attachment (B2) — per-seat scores are already
  distinct by NAME (`casper_correct`…), so it was a UI nicety, not worth the
  live-tracing complexity.

**These take effect only when the council runs again** (engine is down). The
instrumentation is correct and ready for the next bring-up.

### 7c. Dashboard alignment to the redesign — era-aware (commits `4274f3e`, `60cf20b`)
A review of `dashboard.py` after 7a/7b found the panels still rendered arbiter-era
vocabulary and one dead degradation check. **Material finding:** the live
`observer.db` holds **zero blind-review rows** — all 253 `debate_records` rows are
arbiter-era (`casper_r0_action` NULL everywhere, `council_json` NULL everywhere; the
newest is 2026-06-14). The blind-review council has never persisted a live cycle
because the engine has been down. So every redesign display path is validated
*synthetically*; today's dashboard renders exactly as before (arbiter data), and the
redesign paths first light up when the engine next runs. The fixes were made
**era-aware** so both eras render correctly:

- **CS1 — dashboard seat accuracy uses the shared grader (`4274f3e`).** B1 made only
  `observer.backfill_seat_accuracy_scores` era-aware, NOT the dashboard's
  `database.get_agent_accuracy`, so the accuracy panel and the Langfuse seat scores
  could disagree on the same cycle. New `database._score_action_seat` grades each
  seat's `{seat}_r0_action` through the SAME `_grade_action_row` the Langfuse path
  uses (two axes: grid run/stop on forward alpha, exposure direction on forward
  drift, anchored to `FEE_FLOOR`). `get_agent_accuracy` dispatches to it when
  blind-review rows exist and falls back to the legacy per-role scorer
  (Casper regime / Melchior verdict / Balthasar reality+counterfactual) only for
  arbiter-era windows. With zero blind-review rows today, every seat takes the legacy
  fallback — CS1 changes nothing visible until the engine writes blind-review data
  (verified: synthetic blind-review rows grade identically to an independent
  re-derivation; arbiter-only window → `eligible_calls==0` → legacy path).

- **CS2 — panel vocabulary + dead checks (`60cf20b`).**
  - `_fetch_agent_health` keyed "degraded" off the arbiter-era SAFE_DEFAULTS sentinel
    (`conviction=0 AND crux '(no response)%'`) that the blind-review council never
    writes (a non-responder is simply absent, columns NULL), so every seat read green
    forever. Now era-aware: a blind-review row degrades a seat iff its own
    `{seat}_r0_action` is NULL while a peer responded; arbiter rows keep the sentinel.
  - Model labels now come from `magi/agents/seats.py:MODELS` (the declared single
    source of truth), not the `agent_registry` table — that table still said
    Balthasar = `claude-sonnet-4-6` while the redesign dropped Balthasar to
    `claude-haiku-4-5`. (No DB write; the dashboard just stopped reading the stale
    table for model labels.)
  - Hero: dropped the relay-order `· 1/2/3` markers (equal seats have no order);
    added a blind-review decision strip (decision / vote multiset / consensus class)
    from `council_json`.
  - Deadlock banner: blind-review NO_CONSENSUS is a valid P3 outcome, not a
    human-review event — shows a calm "NO CONSENSUS … defaulted to safe stance"
    instead of "DEADLOCK — HUMAN REVIEW REQUESTED".
  - Council Log "Debate" column → "Consensus"; blind-review rows show the consensus
    class (clear / reconciled / no-consensus) from `council_json`, arbiter rows keep
    the `debate_triggered` flag.
  - `_SEAT_CALL_NAMES`: dropped the dead `:rebuttal`/`:synthesis` arbiter-era span
    names (blind-review seat generations are named by the bare seat; the phase —
    propose / review / reconcile — lives in span metadata, not the name).

  **Correction to 7a:** the line above worrying about a stale "regime" label for
  Casper was over-cautious — the dashboard shows `casper_r0_position` directly with no
  "regime" wording, so there was nothing to relabel there. Nothing was changed for it.

  Verified: live arbiter render → HTTP 200, relay markers gone, "Consensus" header,
  no blind-review strip; a synthetic blind-review context fires the redesign branches
  (calm NO CONSENSUS banner, decision strip, `no-consensus` log cell). Engine stayed
  down; display-only, no feedback into council decisions.

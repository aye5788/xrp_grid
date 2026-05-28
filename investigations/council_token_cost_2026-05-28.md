# MAGI Council Token-Cost Investigation — 2026-05-28

## 1. Executive summary

The tokens are going almost entirely to **per-cycle-rebuilt context that cannot be cached and accumulated thread history that isn't being reset**, not to the personas or the council logic itself. Two state-hygiene bugs inflate cost 2–3× above the architectural floor: **Balthasar's `self_model` block is 25,069 chars against a 5,000 cap** (≈6.3K tokens, 10% of its prompt), and its **30-cycle thread reset has been silently skipped since 2026-05-24** because the bloated block makes the rotation `merge_failed` — so ~43K tokens of accumulated history (≈70% of Balthasar's prompt) rides every call. On top of that, the **4-hour cycle cadence is far longer than every provider's prompt-cache TTL (~5 min)**, so all three agents pay full cold-cache input every cycle regardless of configuration — Balthasar's "cache-write every cycle" is this, not a tuning error. **The answer to "architecture's fault or configuration's fault" is: both, and they're separable.** The fixable configuration/hygiene bugs are the larger share right now; but even fully cleaned up, a 3-agent stateful Letta council on a 4h cadence has a structural cost floor (~3 agents × ~15–25K cold input × 6 cycles/day ≈ $2–3/day, ~$60–90/mo) that no config tweak removes — and that floor is large relative to a $67 book.

Note: the 2026-05-27 fixes (freshness-retry false positives, R1 novelty-gating) already landed and are visibly working — see the 05-28 token cliff in §5. This report measures the post-fix state.

---

## 2. Per-agent token breakdown

**Authoritative billed input (Letta Steps API → `token_usage` table).** Block sizes are char/4 estimates — **tiktoken is not installed in the venv**, so block token figures are approximate; the billed `prompt_tokens` are exact.

Recent per-cycle billed input (avg over 2026-05-28, the post-fix regime):

| Agent | Model | Billed input (prompt) | Billed total (incl. reasoning) | Output |
|---|---|---|---|---|
| Casper | gemini-3-flash-preview | ~27,900 | ~32,700 | ~175 |
| Melchior | gpt-4o | ~18,800 | ~18,900 | ~130 |
| Balthasar | claude-haiku-4-5 | ~61,800 | ~63,100 | ~1,100–2,000 |

These match your stated figures (a single representative cycle, 05-28 16:01, billed Casper 34,088 / Melchior 21,606 / Balthasar 65,160).

Decomposition into your four categories (% of billed input prompt; **"accumulated" is a residual** = billed − blocks − estimated scaffolding, so it absorbs estimation error; Letta scaffolding = base system prompt + base-tool JSON schemas, estimated ~2,500 tok):

**Casper (~27,900 billed input):**
| Category | Tokens (est) | % |
|---|---|---|
| Static — persona | 3,875 | 14% |
| Static — Letta scaffolding/tools (est) | ~2,500 | 9% |
| Semi-static — self_model | 678 | 2% |
| Semi-static — recent_outcomes | 237 | 1% |
| Per-cycle — world_state | 3,683 | 13% |
| Per-cycle — 3× r0_output + cycle_phase + R0 prompt | ~1,100 | 4% |
| **Accumulated — in-context thread (residual)** | **~15,900** | **57%** |

(+ ~4,800 Gemini "thinking" tokens billed into total.)

**Melchior (~18,800 billed input):**
| Category | Tokens (est) | % |
|---|---|---|
| Static — persona | 4,554 | 24% |
| Static — Letta scaffolding/tools (est) | ~2,500 | 13% |
| Semi-static — self_model | 372 | 2% |
| Semi-static — recent_outcomes | 237 | 1% |
| Per-cycle — world_state | 3,683 | 20% |
| Per-cycle — r0_outputs + cycle_phase + prompt | ~1,100 | 6% |
| **Accumulated — in-context thread (residual)** | **~6,350** | **34%** |

**Balthasar (~61,800 billed input):**
| Category | Tokens (est) | % |
|---|---|---|
| Static — persona | 4,820 | 8% |
| Static — Letta scaffolding/tools (est) | ~2,500 | 4% |
| **Semi-static — self_model (BLOATED, 25,069 chars / 5K cap)** | **~6,267** | **10%** |
| Semi-static — recent_outcomes | 237 | 0.4% |
| Per-cycle — world_state | 3,683 | 6% |
| Per-cycle — r0_outputs + cycle_phase + prompt | ~1,100 | 2% |
| **Accumulated — in-context thread (residual)** | **~43,200** | **70%** |

The shape is the finding: Casper/Melchior are dominated by a healthy mix; **Balthasar is 80% accumulated-history + bloated-self_model**, neither of which is intrinsic to its role.

---

## 3. Call flow for one cycle

Every agent has **one persistent Letta thread** that lives across all cycles; each council call is a `messages.create` that **appends** to that thread. Threads are only cleared by the 30-cycle rotation. There is no fresh-per-cycle thread.

```
scheduler (every 4h: 00/04/08/12/16/20 EST)
  └─ orchestrator.run_cycle()
     1. check_all_guardrails()                     [no LLM]
     2. build_world_state()                         → ~14.7K-char JSON snapshot
     3. update_world_state()                        → writes shared `world_state` block
                                                       (one block, read by all 3 agents)
     4. run_round_0_parallel(cycle_id, world_state) [3 agents, ThreadPoolExecutor]
          set `cycle_phase` block = "round_0"
          ├─ casper:    messages.create(R0 prompt)  → append to casper thread
          ├─ melchior:  messages.create(R0 prompt)  → append to melchior thread
          └─ balthasar: messages.create(R0 prompt)  → append to balthasar thread
          (per agent) if evidence fails freshness check:
                      ONE extra messages.create (correction)   ← extra billed call
          (per agent) parse → write `{agent}_r0_output` block (conviction stripped)
     5. _prior_r0_signature()                        [DB read]
     6. should_run_r1(round_0, prior_sig)            → fire iff genuine conflict AND
                                                        positions differ from last cycle
     7. if fire: run_round_1()                       [3 agents, parallel]
          set `cycle_phase` block = "round_1"
          each agent: messages.create(R1 prompt with the OTHER two agents'
                      R0 outputs pasted into the user message)  → append to thread
     8. resolve_consensus(round_0, round_1)          [no LLM]
     9. enforce_hard_rules()                          [no LLM]
    10. insert_debate_record + dual-write magi_decisions + token_usage rows
```

**What each agent sees at its call:** its system prompt (persona + all 8 memory blocks rendered in by Letta) + its full in-context thread tail + the cycle's user prompt. **Write-backs:** `world_state` (orchestrator, step 3); `cycle_phase` (council, steps 4/7); `{agent}_r0_output` (council, after R0); `self_model` (optionally self-appended by the agent via `core_memory` tools during R0, and rewritten by the 30-cycle rotation).

**LLM calls per cycle:** 3 (R0) + 0–3 freshness retries + 0 or 3 (R1) = **3 to 9 calls/cycle**. The representative 05-28 16:01 cycle fired 7 calls (3 R0 + 1 Balthasar retry + 3 R1) totaling **~312,000 billed input tokens in a single cycle**, ≈ $0.36.

---

## 4. Caching analysis

**Why "cw" (cache-write) shows on Balthasar but not the others is a billing-display artifact, not a caching-quality difference.** Anthropic itemizes cache-write/cache-read as separate billing lines; OpenAI (gpt-4o) and Google (gemini) do automatic/implicit caching with **no separate cache-write line item**. So Casper and Melchior are *not* caching better — you just can't see their cache activity in the same way.

**None of the three get meaningful cross-cycle cache benefit, by construction.** All three providers' prompt caches have a short TTL (~5 minutes default for Anthropic; OpenAI/Google automatic caches are similarly short-lived). **MAGI cycles are 4 hours apart.** The cache has always expired by the next cycle, so every cycle's first call is a cold write of essentially the whole prompt. This is the dominant reason Balthasar cache-writes ~its full prompt every cycle — **it's the cadence, not a misconfiguration.**

**Even within a cycle, the cacheable prefix isn't stable.** The R0→R1 calls are seconds apart (inside the TTL), but between them the mutable blocks change — `cycle_phase` flips round_0→round_1, the three `r0_output` blocks get rewritten, and the R1 user message is different — so the prefix isn't byte-identical and R1 re-writes rather than reads. More broadly, the memory-block region Letta renders into the prompt contains **`world_state` (changes every cycle), the 3 `r0_output` blocks (change every cycle), `cycle_phase`, and the mutable `self_model`** — any of these sitting ahead of the static persona in block order busts the cache even within the TTL window. (I did not dump Letta's exact compiled block ordering, so the "ahead of persona" detail is inferred, not verified.)

**Bottom line on caching:** at a 4h cadence, prompt caching can save ~nothing cross-cycle for any of the three. This is structural.

---

## 5. Memory-lifecycle health

Rotation cadence = 30 cycles (`config.ROTATION_CADENCE`), self_model cap = 5,000 chars.

Full `memory_rotations` history:

| When | Agent | Cycle | self_model before→after | Patterns | Status |
|---|---|---|---|---|---|
| 05-20 | casper | 30 | 2,481→3,163 | 2 | success |
| 05-20 | melchior | 30 | 1,859→2,510 | 2 | success |
| 05-20 | balthasar | 30 | 1,709→2,490 | 2 | success |
| 05-24 | casper | 30 | 1,157→1,800 | 2 | success |
| 05-24 | melchior | 30 | 1,156→1,798 | 2 | success |
| 05-24 | balthasar | 30 | 2,806→3,743 | 2 | success |
| 05-27 | casper | 60 | 2,064→2,714 | 2 | success |
| 05-27 | melchior | 60 | 10,159→1,488 | 1 | success (evicted hard) |
| **05-27** | **balthasar** | **60** | **25,069→(no change)** | **0** | **merge_failed** |

**Findings:**
- **Balthasar's self_model is 25,069 chars (live-confirmed), 5× the 5,000 cap.** The rotation `merge_failed` because `_evict_oldest_if_needed` can only drop `## Pattern N` blocks; Balthasar's block is mostly **non-pattern prose** (consistent with the manual curation from the prior self-model-anchoring incident), which the evictor cannot trim, so the merge stayed over cap and was refused.
- **Because merge failed, `messages.reset()` was skipped** (by design: thread integrity > token savings). So **Balthasar's thread has not been reset since cycle 30 on 2026-05-24** — ~4 days of accumulation — while Casper and Melchior reset at cycle 60 on 05-27. This is exactly why Balthasar's accumulated-history residual (~43K) dwarfs the others (~6–16K).
- **self_model growth is NOT bounded in practice.** The cap is only enforced at merge time and only via pattern-block eviction; non-pattern content escapes the cap entirely. Melchior had also bloated to 10,159 before the 05-27 eviction knocked it to 1,488. Casper stays small and healthy.
- **Thread depth (persisted log, live):** Casper 825 msgs, Balthasar 704, Melchior 466. **Important caveat:** this is Letta's full recall storage, not the in-context window — billed input (~28K/19K/62K) is far smaller than the full logs would imply, so Letta is sending only a managed sub-window. I did not separately confirm whether `reset()` purges recall storage vs. just the context pointer (one of the calls that hung), but the billed numbers are the authoritative measure of what's actually sent, and they're consistent with Balthasar's in-context tail being the largest.

**05-28 token cliff (confirms the 05-27 fixes work):** daily avg input prompt, council_r0:

| Day | Casper | Melchior | Balthasar |
|---|---|---|---|
| 05-24 | 92,040 | 45,142 | 64,242 |
| 05-25 | 68,265 | 35,193 | 78,163 |
| 05-26 | 89,950 | 69,347 | 93,056 |
| 05-27 | 93,706 | 79,099 | 68,082 |
| **05-28** | **27,932** | **18,759** | **61,779** |

Casper and Melchior dropped ~70% after the 05-27 freshness fix + cycle-60 thread reset. **Balthasar barely moved** — because its reset was the one that failed.

---

## 6. Cross-agent output sharing audit

**Current state (live-confirmed):** all three `r0_output` blocks — `casper_r0_output`, `melchior_r0_output`, `balthasar_r0_output` — are attached to **every** agent (via `block_ids=all_shared_block_ids` in provisioning), **including each agent's own output block.** They're also pasted *again* into the R1 user messages.

**Token cost: negligible.** The three blocks total 552 + 426 + 1,030 = ~2,008 chars (~500 tokens) per agent. This is the *least* important cost lever.

**Doctrinal mismatch (applying the CLAUDE.md §3 "only expose what the role needs" lens):**
- **Casper** (regime perception) does not need Melchior's grid verdict or Balthasar's risk gate to classify regime in R0 — yet sees both.
- **Melchior** needs Casper's regime to choose geometry; arguably does not need Balthasar's risk action in R0.
- **Balthasar** needs Melchior's proposed geometry to veto it, and plausibly Casper's regime; sees all.
- All three see their **own** r0_output, which is pointless.
- During R0 these blocks hold the **previous cycle's** outputs (stale); during R1 the peer outputs are pasted into the prompt explicitly, making the blocks **redundant**.

So sharing is broader than the doctrine implies, but it's a ~500-token issue — worth tidying for cleanliness, not for cost.

---

## 7. Findings — optimization opportunities IF the architecture is kept (ranked by token reduction)

1. **Fix Balthasar's self_model bloat + repair the rotation evictor. (Biggest lever.)** Curate the 25,069-char block down under 5,000 and/or make `_evict_oldest_if_needed` able to trim non-`## Pattern N` content. This directly removes ~6K tokens/call *and* unblocks the thread reset, which removes the ~43K accumulated tail. Estimated effect: Balthasar ~62K → ~20K input/call. **Trade-off:** curating loses whatever accumulated risk-reflection prose is in that block; must snapshot first (the anchoring history makes this block sensitive).
2. **Cap thread accumulation between resets.** Shorter rotation cadence or a token-budget-triggered reset (vs. fixed 30-cycle) would bound the accumulated-history residual that is 34–70% of each prompt. **Trade-off:** more frequent distillation = more compaction calls + risks distilling thinner windows.
3. **Per-agent `world_state` views.** The 14,733-char block (~3,683 tok) is sent to all three every cycle, but `scored_variants_top_10` / `shadow_variants` / scoring fields are Melchior-only. Trimming agent-irrelevant fields for Casper/Balthasar saves ~1–2K tok × 2 agents/cycle. **Trade-off:** breaks the single-shared-block simplicity; needs 3 maintained world_state blocks and schema-validator changes.
4. **Verify the already-shipped R1 novelty-gate and freshness-band fixes are holding.** The 05-28 cliff says they are; just confirm R1 fire-rate settled near the predicted ~23% and Casper's per-cycle freshness retry stopped. **Trade-off:** none — already done, this is monitoring.
5. **Detach own + unused peer `r0_output` blocks; rely on the R1 paste.** ~500 tok/agent/cycle. **Trade-off:** minor; mild loss of R0-time peer awareness (which is stale anyway).

---

## 8. Architectural concerns (structurally expensive regardless of configuration)

- **4h cadence ≫ cache TTL means caching can never help cross-cycle.** Every cycle pays full cold input on all three agents. The only "fixes" are architectural: raise cadence drastically (defeats the cost goal), or drop stateful threads for a compact stateless prompt each cycle (abandons the "accumulated experience" design). At this cadence, ~$2–3/day is a floor, not a tuning target.
- **Stateful threads inherently accrue context that must be distilled, and the distillation itself is a fallible LLM step** (Balthasar's `merge_failed` is the live proof). The architecture trades per-cycle prompt growth for "learning," but that machinery is also a recurring failure surface.
- **The world_state is sent 3× per cycle by design.** Three separate agents/threads each render the same ~3.7K-token snapshot independently; there is no "broadcast once." Multiply by 6 cycles/day and the redundant world_state alone is ~66K tok/day.
- **Model choice, not token volume, drives Melchior's share of spend.** Over the last 7 days Melchior was the single most expensive agent (~$16.6 vs Balthasar ~$11.1, Casper ~$4.4) *despite the lowest token count*, because gpt-4o's per-token price dominates. Balthasar is the token leader; Melchior is the dollar leader. No prompt optimization changes gpt-4o's rate card.
- **The honest bottom line:** the current ~$5/day (~$150/mo on a $67 book) is economically inverted, and roughly half of it is removable hygiene (Balthasar bloat + skipped reset, the now-fixed retries). But the irreducible remainder — 3 agents × cold cache × low cadence — is intrinsic to "a three-agent stateful Letta council." If the target operating cost needs to be well under that floor, that's an architecture decision (fewer agents, stateless calls, cheaper models, or a non-LLM rule layer for the cheap cycles), not a configuration one.

---

**Caveats on method:** block token figures are char/4 estimates (tiktoken absent from the venv); billed `prompt_tokens` are exact. The "accumulated history" rows are residuals after subtracting blocks and an *estimated* ~2,500-token Letta scaffolding, so they carry the estimation error. The Letta `reset()` recall-vs-context semantics and the day-by-day R1 fire rate were the two checks whose tool calls hung; I reasoned around them from data already in hand rather than assert beyond what I measured.

What I did not do per the brief: I did not web-fetch the Letta caching docs (the §4 cache-TTL mechanics are from established provider behavior, not re-verified this session), and I report the file inline rather than writing it, per your instruction.

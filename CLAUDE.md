# CLAUDE.md

This file loads automatically at the start of every Claude Code session
in this repo. It encodes operating context and intent. State (what is
built, what is broken, what is next) lives in the four handoff docs at
repo root; read those after this one.

Handoff docs to read at session start:
- `00_PROJECT_OVERVIEW.md` — system shape, components, data layout
- `01_CURRENT_STATE.md`    — what is built and verified; do-not-re-derive facts
- `02_NEXT_BUILD_TASKS.md` — work queue
- `03_INSTRUCTIONS_TO_CLAUDE.md` — workflow rules and forbidden moves

CLAUDE.md is intent and discipline. The handoff docs are state. If they
disagree, the handoff docs win for state; this file wins for how to work.

## 1. What MAGI is

MAGI is an XRP/USD spot grid bot running on Kraken in paper mode, with a
three-agent LLM council on Letta Cloud advising structural decisions. The
council is Casper (Gemini-3-flash-preview, regime), Melchior (GPT-4o, grid
microstructure), Balthasar (claude-sonnet-4-6, risk/survival). Current
capital under management is ~$67 (≈14 XRP + ~$47 USD). The goal is a
profitable adaptive grid: net-positive PnL after Kraken tier-0 fees
(maker 0.16%, taker 0.26%) with >50% directional accuracy on the bot's
trade actions, surviving without manual intervention.

This is a trading system, not a research project, not a learning
exercise, not a demo of agentic AI. Every change should be defensible
against the operating goal: filling more, accumulating fees, not
deadlocking, not damaging the book. "Cleaner" architecture that does not
move filling/surviving/adapting is a lower priority than the operator's
stated goal.

## 2. Architecture intent

Three layers, complementary by design.

**Layer 1 — Council judgment.** Three Letta Cloud agents vote
independently each cycle (Round 0). When the `CONFLICT_MATRIX` in
`magi/council.py` flags an action incompatibility, Round 1 fires and the
two named agents challenge each other. The council exists for cases
where multiple defensible answers exist — regime classification, when to
recentre vs. tighten, when concentration risk has crossed a judgment
threshold. Judgment is delegated here precisely because it cannot be
encoded as a rule without losing nuance.

**Layer 2 — Hard rules.** `magi/orchestrator.py:enforce_hard_rules`
applies Python-enforced overrides on top of council consensus. These
are non-negotiable survival constraints: `[RECENTRE_COOLDOWN]`,
`[GRID_DEGENERATE]`, `[PAUSE_INVALID]`, `[USD_BUFFER_FLOOR]`,
`[XRP_BUFFER_FLOOR]`, `[ALLOC_SKEW_CEILING]`, `[DAILY_LOSS_LIMIT]`,
`[KILL_SWITCH]`. Hard rules exist for known-deterministic survival
conditions where there is one correct answer regardless of judgment.
The council can be overridden silently; there is no penalty for being
overridden. There is a penalty for voting strategically to avoid
overrides instead of reading the data.

**Layer 3 — Execution.** `grid/engine.py` builds and maintains the
ladder, places paper orders, tracks fills. The engine is downstream of
the council and the rule layer. When behavior looks broken, suspect
the engine and `world_state` inputs **before** suspecting the prompts.
The brain is downstream of the hands.

The two upper layers are not in tension. The rule layer catches what
the council reliably gets wrong (especially under model-compliance
limits); the council catches what the rule layer cannot encode without
losing context. Trying to push everything into hard rules collapses
judgment; trying to push everything into the council ignores known
model failures. Both are load-bearing.

## 3. The council is architectural diversity, not three voices saying the same thing

The three agents run different LLM providers by design, not by
accident:

- **Casper / Gemini-3-flash-preview** — tends to favour structural
  classification; reads context blocks (persona, self_model,
  world_state) on each cycle and updates votes when those blocks change.
- **Melchior / GPT-4o** — tends to anchor on prior responses
  (sycophancy / recency bias visible in evidence list repetition
  across consecutive cycles). Sensitive to conversation history
  persistence in Letta agent threads.
- **Balthasar / claude-sonnet-4-6** — defaults toward
  risk-conservative; will hold a CLEAR or PAUSE position with high
  conviction when uncertain.

These known biases are the architectural diversity. The council's
strength is that one agent's blind spot is another's signal. When the
three agents genuinely diverge, that is the architecture working as
designed; `CONFLICT_MATRIX` → Round 1 debate is what surfaces the
disagreement productively. The goal is NOT to engineer all three into
producing identical outputs. Treat the model mix as a feature; encode
known biases into per-agent prompts rather than fight them.

## 4. Source-of-truth facts

Things that have been re-derived wastefully across sessions; do not
re-derive these.

- `debate_records` (in `observer.db`) is the **canonical** write target
  for council decisions in Phase 5. One row per cycle.
- `magi_decisions` is **dual-written** for legacy consumers
  (`dashboard.py` panels, `learning.py`, `extract_test_cases.py`). It
  is not the canonical source; it is maintained for backward compat.
- Letta Cloud agents persist; `agent_registry` (in `observer.db`) maps
  logical agent names → Letta agent UUIDs. Do not provision new
  agents; use `magi/provision_agents.py` to sync persona blocks and
  LLM config knobs to the existing ones.
- Hard rules live in `magi/orchestrator.py:HARD_RULES` plus the steps
  inside `enforce_hard_rules`. There is no Supervisor concept; it was
  rejected and removed. Do not re-introduce it.
- Live database is the truth for live data. Repo code is the truth
  for code behavior. Handoff docs and this file describe intent; if
  they disagree with the live state, the live state is current and
  the docs are stale.
- Grid spacing clamps: `MIN_GRID_SPACING_PCT = 0.003`,
  `MAX_GRID_SPACING_PCT = 0.025` (0.3% to 2.5%). Set in `config.py`.
- Asset analysis (already complete): DOGE, XRP, SOL viable; ADA
  eliminated. XRP optimal spacing is 1.5%.
- Kraken tier-0 fees: maker 0.16% (`MAKER_FEE`), taker 0.26%
  (`TAKER_FEE`). A round-trip needs >~0.4% to clear fees.
- Letta SDK note: `llm_config` is deprecated for `c.agents.update`;
  use provider-shaped `model_settings`. `parallel_tool_calls` is
  server-forced to True regardless of what you send.
- LLM config sync lives in `provision_agents.py:AGENT_CONFIG` and is
  idempotent. Equal across agents wherever providers expose
  equivalent knobs; provider-side asymmetries (GPT-4o has no native
  extended-thinking budget) are documented inline.

## 5. Operating discipline

Discipline that prevents wasted work.

- **Verify before acting on assumptions.** The persona files on disk
  in `magi/prompts/` and the live persona blocks in Letta can differ
  (Letta normalises whitespace; orphan blocks linger at project
  scope). Check via the Letta SDK before trusting either as
  authoritative. The same applies to `debate_records` rows vs.
  `magi_decisions` rows for the same cycle.
- **Engine and council are separate concerns.** A status check ("is
  `magi.service` running, is the dashboard returning 200") is not an
  audit ("is the bot actually trading"). Always confirm both.
- **Vital signs at session start.** Before proposing any change, pull:
  `buy_count`, `sell_count`, `hours_since_last_fill`,
  `hours_since_last_rebuild`, order skew, distance from current price
  to the nearest fill level, recent hard-rule overrides. If any of
  these is abnormal, that is the work for this session, regardless
  of what the user requested. State that explicitly to the user.
- **Engine-first when behavior looks broken.** When the bot is not
  filling, suspect the engine (`grid/engine.py`), the indicator
  pipeline (`observer.py`), and the `world_state` builder
  (`orchestrator.build_world_state`) **before** suspecting the
  persona prompts. Past wasted-time pattern: persona iteration while
  an engine bug was the actual cause.
- **Surface similarity is not alignment.** When evaluating whether an
  agent's reflection / vote / decision matches the persona, run the
  current `world_state` through the persona's actual gating rules and
  check whether the persona-prescribed action matches what the agent
  produced. Do not check whether the wording sounds similar.
  Alignment proves itself in input→action correspondence, not in
  vocabulary.
- **Snapshot before mutating Letta state.** self_model blocks, agent
  configs, persona blocks all hold accumulated state. If you are
  about to overwrite one, write the current value to a file in
  `/tmp/` first with the current date in the filename, so the
  operator can review or restore.
- **Match deliverable shape to request.** Surgical edits → str_replace.
  Full-file rewrites → only when the operator asks, or when the
  alternative is a fragile multi-step edit. When shipping code for
  the operator to push, provide the final file, not a snippet plus
  navigation instructions.

## 6. Forbidden moves

These have been rejected before; do not re-propose them.

- Self-hosted Letta. Decommissioned; files preserved for rollback
  only. Stay on Letta Cloud.
- Mem0, Graphiti, vector DBs, or any persistence layer the operator
  did not ask for. `observer.db` SQLite is the persistence layer.
- `krakenex` or any third-party Kraken wrapper. The system uses the
  Kraken REST API directly via the existing client.
- Coinbase One, Kraken+, or any paid subscription. The operator
  validates on the free tier.
- Scaling dollar amounts up to "make results more visible." The
  operator's goal is validation, not size.
- The Supervisor / override-authority concept. Removed; do not
  re-introduce.
- Anything that references the dead ETH futures system.
- Re-doing already-completed work. Search prior session context
  (conversation_search if available; session transcripts otherwise)
  before re-deriving facts already in `01_CURRENT_STATE.md`'s
  "Verified facts" section.
- Asking clarifying questions the handoff docs already answer.
- New external services, frameworks, or processes.

## 7. What constitutes a real audit

A status check confirms components are alive. An audit measures
against the design goal.

| | Status check | Audit |
|---|---|---|
| Question | Are services running? | Is the bot trading as designed? |
| Signals | systemctl active, HTTP 200, rows being written, no exceptions in journal | Fill rate, fee-positive PnL, council variety, debate fire rate, hard-rule override frequency, grid liveness over time |
| Verdict | binary | comparison against `>50% accuracy`, fee-positive, surviving |

The bot has been "humming" — services up, rows being written, dashboard
rendering — while not earning for >49 hours. A status check passes this;
an audit does not. When the operator asks for an audit, deliver an
audit. Do not substitute a status check.

Specifically, before declaring an audit complete, confirm these against
the design goal:
- Has the grid filled in the audit window? If not, why?
- Are the three agents producing varied positions, or reciting cached
  responses across cycles?
- Is `debate_triggered` rate non-zero over the recent window? If
  always zero, why?
- Are hard rules firing because the council is genuinely wrong, or
  because the council and rules are aligned and the rule layer is
  redundant?
- Does the consensus action this cycle actually move the bot closer
  to filling, given the current world state?

## 8. Recurring failure patterns

Patterns that have cost real time. Lesson per item.

- **Smart agents on a broken engine produce smart-sounding decisions on
  a dead system.** Engine bugs in scheduler replacement pricing and
  grid-degeneracy handling were the actual fill blockers while
  council architecture upgrades were the visible work. *Lesson:*
  before iterating on the council, verify the engine is actually
  capable of acting on a correct decision.

- **Surface similarity is not alignment.** Casper's evidence text in
  recent cycles read like the new persona's "stale base" doctrine,
  but the actual `world_state` inputs (`roc_6h=-0.98`) no longer
  matched the conditions the reflection was learned under. Alignment
  was coincidental. *Lesson:* check that the current inputs produce
  the persona-prescribed output, not that the wording sounds similar.

- **Prompt edits do not reliably move GPT-4o behavior.** Verbose
  persona sections produced zero observable effect under an A/B test
  in stationary conditions. GPT-4o anchors on conversation-history
  patterns over current persona text. *Lesson:* design around model
  compliance limits with hard rules and conversation-history
  awareness; prompt tightening is the weakest lever and the slowest
  to validate.

- **Casual orphan accumulation in Letta blocks.** Re-provisioning
  agents leaves stale persona / human / decisions / self_model
  blocks at project scope that the Letta web UI surfaces but no
  current agent reads. *Lesson:* verify which blocks an agent
  actually uses via `c.agents.blocks.list(agent_id)`, not by
  browsing the project-level Memory blocks UI.

- **Effort imbalance across symmetric work.** When fixing three
  per-agent files (one per agent), polish drifted toward Balthasar
  (Sonnet's home model). The operator caught it and called it out.
  *Lesson:* when work is symmetric across the three agents, set a
  target shape (line count, section structure, examples per agent)
  before writing, and equalise up to the highest standard rather
  than down to the lowest.

- **Self_model anchoring under persona change.** When the persona is
  rewritten but self_model reflections from the prior persona
  remain, the R0 prompt's "revise away from prior failure modes"
  directive elevates stale reflections to authoritative. *Lesson:*
  when changing a persona materially, audit each agent's self_model
  for entries that contradict the new persona and curate them
  explicitly. Snapshot before curating.

- **Conversation-history persistence in Letta agent threads.** GPT-4o
  in particular reproduces prior-cycle responses byte-for-byte even
  when world_state values have moved. *Lesson:* identical evidence
  lists across consecutive cycles are a smell. If you see them,
  check whether the agent is reading the current world_state at all
  by comparing cited numbers against the current `world_state`
  block.

- **"Round 1 debate" framed as the council's main resolution
  mechanism, but fired 1 of 38 cycles in early Phase 5.** The
  framing in handoff docs did not match observed behavior. *Lesson:*
  when the docs describe a mechanism, verify it fires at expected
  frequency before assuming it is doing work.

## 9. Operator preferences

Encoded once so they do not need to be restated each session.

- **Provide full edited files when shipping code for the operator
  to push.** Not snippets with "add this at line 247" instructions.
- **Use surgical edits (str_replace / Edit tool) when iterating.**
  Full-file rewrites waste tokens and review effort.
- **Use your own judgment when the operator asks you to.** Do not
  turn judgment questions into multiple-choice menus. Recommend a
  call and defend it.
- **Be honest about defaulting to easier paths.** When the operator
  catches a corner being cut, restart with the correct approach;
  do not minimise.
- **Plain, direct tone.** No apology spirals. No "great question",
  "you're absolutely right", or other sycophancy openers. No
  performative reassurance.
- **No clarifying questions the docs answer.** Spend up to a minute
  on read-only investigation (grep, file read, SDK query) before
  asking. If the answer is in `00`–`03` or this file, do not ask.
- **The operator notices effort imbalance.** Symmetric work
  (three agents, three personas, three self_models) gets symmetric
  attention. Do not invest more polish in one than the others.
- **Verify before claiming.** "I updated X" should be followed by
  evidence that X is updated, not just by the tool call that
  attempted the update.
- **Stop on unexplained errors.** Do not paper over.
- **No commits.** The operator pushes manually.

## Cross-reference

| Need to know… | Read… |
|---|---|
| What components exist, what writes where | `00_PROJECT_OVERVIEW.md` |
| What is built and verified, what facts are already established | `01_CURRENT_STATE.md` |
| What is next on the work queue | `02_NEXT_BUILD_TASKS.md` |
| Tone, workflow patterns, forbidden moves (detail) | `03_INSTRUCTIONS_TO_CLAUDE.md` |
| Why the architecture is what it is, how to work in it | this file |
| Live data | `observer.db` |
| Live agent state | Letta Cloud (SDK: `letta_client.Letta(api_key=…)`) |

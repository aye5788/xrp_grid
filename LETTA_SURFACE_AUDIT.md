# LETTA SURFACE AUDIT

**Generated:** 2026-05-29 · **Method:** read-only static analysis (no service run, no Letta/Kraken/LLM API calls).
**Scope:** all Python under `magi/` and repo root.
**Produced by:** a deterministic 15-agent workflow — 7 file-group enumerators (Sonnet) → 7 independent re-grep reviewers (Sonnet) → 1 whole-tree adversarial sweep (Opus) → synthesis (Opus). Every reviewer found at least one addition or correction to its peer (`agree=False` across all 7 groups), so the figures below are post-review, post-adversarial.

> **Purpose.** Enumerate every Letta Cloud touch-point so the port to vendor-native platforms (Anthropic / OpenAI / Google) can be specced cleanly. This documents *what couples to Letta and where the seams are* — it does not decide the target architecture. Open decisions are collected in §7.

---

## 0. Scope, exclusions, and one deliberate omission

**In scope:** `magi/*.py` + repo-root `*.py` (17 files carry at least one touch-point).
**Excluded:** `snapshots/`, `letta/` (decommissioned self-hosted Docker), `.venv/`, `venv/`, `__pycache__/`, `grid/` (recon confirmed 0 Letta hits), `scratch/`, `investigations/`, `analysis/`, `phase1_balthasar/`.
**`evals/` deliberately excluded** by operator direction: the eval factory targets MAGI versions that won't survive the migration, so auditing it is wasted work. One item from it is *noted but not audited*: `evals/common/factory_base.py:41` hard-codes `"balthasar": "anthropic/claude-sonnet-4-6"`, diverging from the live config (live Balthasar runs `claude-haiku-4-5`). That is a **model-choice question for the rebuild, not migration infrastructure** — tracked, not lost.

---

## 1. Executive summary — the shape of the port

The Letta coupling is **narrow and well-isolated**, which is the good news for the port:

- **All live agent invocation goes through one file.** Every `client.agents.messages.create` lives in `magi/council.py` (R0 fan-out, R0 freshness retry, R1 synthesis). `magi/orchestrator.py` and `scheduler.py` reach Letta only *indirectly*, through six imported `council.py` wrappers. **`council.py` is the port's center of gravity.**
- **Six SDK construction sites**, all `Letta(api_key=...)` against Letta Cloud's default endpoint: `council.py:238` (module singleton), `memory_lifecycle.py:155` (module singleton), `provision_agents.py:205`, `config_validator.py:125`, `observer.py:233` (lazy), `dashboard.py:3009` (lazy, per-request).
- **The shared state is 8 Letta blocks**, not a database: `world_state`, `cycle_phase`, `persona` (per-agent), `self_model` (per-agent), `recent_outcomes`, and three `*_r0_output` peer-visibility blocks. Most are **write-then-read signalling channels** the orchestrator refreshes each cycle — on a stateless native platform they collapse into prompt-injected strings. The two that hold *accumulated* state are `self_model` (per-agent, agent-written) and the agents' conversation threads.
- **The only persistence that genuinely lives *in* Letta** (i.e. has no local mirror) is: (a) per-agent **conversation thread history**, and (b) **`self_model` block contents between rotations**. Everything else — `world_state`, agent UUIDs, model config, outcomes — is already mirrored in `observer.db` or rebuilt fresh each cycle. Agent UUIDs live in `agent_registry`; the three Letta agents persist on Letta's servers and are snapshotted under `snapshots/letta_shutdown_2026-05-28/`.
- **No custom Letta tools.** `TOOL_DEF` count is **0** — agents are created with `tools=[]` and `include_base_tools=True`, relying on Letta's native `core_memory` tools for self_model writes. That native dependency (mid-inference `core_memory.append`) is the one behavior with no direct stateless equivalent (see §7-B).
- **Two Letta-specific scaffolds will not survive as-is** and need explicit replacement decisions: the raw-HTTP `sweep_letta_steps_for_failures` (polls `api.letta.com/v1/runs` for credit/provider errors) and `client.steps.retrieve` token/error accounting — both replaced by native `response.usage` / SDK exceptions. And the `SAFE_DEFAULTS` degradation fingerprint (`conviction==0.0 AND crux LIKE '(no response)%'`) encodes a *Letta transport* failure shape that native SDK exceptions won't produce.

**One discrepancy to verify (not resolved by static analysis):** `council.py`'s `should_run_r1` is called as a gate at `orchestrator.py:1877`, but a comment at `run_round_1` (council.py:1472) reportedly says `run_cycle` calls R1 *unconditionally*. CLAUDE.md states R1 is novelty-gated. Whether `should_run_r1` actually gates the `run_round_1` call at `orchestrator.py:1880` could not be confirmed from the call sites alone — flagged in §7-H.


---

## 2. Touch-point inventory

**112 unique touch-points** across 17 files (deduped by file+line+category, post-correction).


### Counts per category

| Category | Count |
|---|---:|
| SDK_CONSTRUCT | 12 |
| AGENT_INVOKE | 3 |
| AGENT_LIST | 7 |
| BLOCK_CRUD | 19 |
| BLOCK_ATTACH | 1 |
| THREAD_COMPACT | 1 |
| THREAD_RESET | 1 |
| TOOL_DEF | 0 |
| MODEL_CONFIG | 12 |
| IMPORT_ONLY | 12 |
| INDIRECT | 44 |
| **TOTAL** | **112** |

> **`SDK_CONSTRUCT` breakdown (why 12, not the "six sites" in §1):** only **6** are literal `Letta(api_key=...)` constructions — `council.py:238`, `memory_lifecycle.py:155`, `provision_agents.py:205`, `config_validator.py:125`, `observer.py:233`, `dashboard.py:3009`. The other 6 are construction-*adjacent* rows the enumerators grouped here for traceability: 2 lazy `from letta_client import Letta` lines inside construct functions (`dashboard.py:3004`, `observer.py:232`) and 4 lazy-client scaffolding rows (`observer.py:217` global, `observer.py:220` wrapper def, `observer.py:346` + `config_validator.py:137` call sites). The true construction-site count is **6**.

### Top files by touch-point density

| Rank | File | Touch-points |
|---:|---|---:|
| 1 | `magi/council.py` | 14 |
| 2 | `magi/provision_agents.py` | 14 |
| 3 | `magi/config_validator.py` | 14 |
| 4 | `scheduler.py` | 12 |
| 5 | `database.py` | 9 |
| 6 | `magi/memory_lifecycle.py` | 8 |
| 7 | `dashboard.py` | 7 |
| 8 | `observer.py` | 7 |
| 9 | `config.py` | 7 |
| 10 | `magi/orchestrator.py` | 6 |

### Files that import `letta_client` but make no call (dead imports)

**None.** All 6 files that import the SDK (`council.py`, `memory_lifecycle.py`, `provision_agents.py`, `config_validator.py`, `dashboard.py`, `observer.py`) also use it. No dead imports to prune.

### Full inventory (grouped by file)

Source column: `E` enumerated, `E+R` confirmed by reviewer, `R` added by reviewer, `ADV` added by adversarial sweep.


#### `magi/council.py` (14)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 46 | IMPORT_ONLY | `from letta_client import Letta` | Imports the Letta SDK class; construction happens at line 238 | E |
| 85 | INDIRECT | `step = client.steps.retrieve(sid)` | Inside _check_steps_for_alerts; walks step_ids from response.messages, retrieves each Step to inspect stop_reason/status/error_data for credit/auth/provider failures. Not a dire… | E |
| 182 | INDIRECT | `step = client.steps.retrieve(sid)` | Inside _record_token_usage_from_response; retrieves Step to sum prompt_tokens/completion_tokens/total_tokens for cost accounting | E |
| 238 | SDK_CONSTRUCT | `client = Letta(api_key=_api_key)` | Module-level singleton; api_key from LETTA_API_KEY env var; base_url defaults to Letta Cloud (https://api.letta.com). Comment at line 27 notes token= is wrong — only api_key= wo… | E |
| 342 | BLOCK_CRUD | `matches = list(client.blocks.list(label=label, limit=1))` | Inside _get_shared_block_id; looks up a shared block by exact label string and caches the ID. Called before every blocks.update. Labels looked up include: world_state, cycle_pha… | E |
| 1007 | BLOCK_CRUD | `block_id = _get_shared_block_id("world_state")` | Indirect BLOCK_CRUD: calls _get_shared_block_id which executes client.blocks.list internally. This is the resolver call inside update_world_state(); the resulting block_id feeds… | R |
| 1011 | BLOCK_CRUD | `client.blocks.update(block_id, value=payload)` | update_world_state() — writes JSON-serialized world_state dict to the shared 'world_state' block. Called once per cycle before R0 fan-out | E |
| 1019 | BLOCK_CRUD | `block_id = _get_shared_block_id("cycle_phase")` | Same pattern as line 1007 but for the cycle_phase block. Indirect BLOCK_CRUD via _get_shared_block_id -> client.blocks.list. Feeds client.blocks.update at line 1020. Not listed … | R |
| 1020 | BLOCK_CRUD | `client.blocks.update(block_id, value=phase)` | set_cycle_phase() — writes 'round_0' or 'round_1' to the shared 'cycle_phase' block. Called at start of run_round_0_parallel and run_round_1 | E |
| 1070 | AGENT_INVOKE | `response = client.agents.messages.create(` | send_round_0 attempt 1 — sends R0 prompt (with triggers section + self_model directive + JSON schema) to one agent. letta_id resolved from agent_registry DB. messages=[{role:use… | E |
| 1134 | AGENT_INVOKE | `retry_response = client.agents.messages.create(` | send_round_0 freshness retry — fires only when _validate_r0_freshness detects stale evidence. Sends corrective re-prompt with inline correct values. Capped at exactly one extra … | E |
| 1231 | BLOCK_CRUD | `client.blocks.update(` | send_round_0 SAFE_DEFAULTS path — publishes safe default payload (position/key_evidence/crux, conviction stripped) to '{agent_id}_r0_output' block when R0 fails all attempts. En… | E |
| 1245 | BLOCK_CRUD | `client.blocks.update(` | send_round_0 success path — publishes parsed R0 (conviction stripped) to '{agent_id}_r0_output' block after successful parse. The three r0_output blocks are read by Letta agents… | E |
| 1350 | AGENT_INVOKE | `response = client.agents.messages.create(` | send_round_1_synthesis — sends R1 synthesis prompt to one agent. Peer R0 outputs are pasted explicitly in the user message (no reliance on Letta memory-tool reads). No retry on … | E |

#### `magi/provision_agents.py` (14)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 26 | IMPORT_ONLY | `from letta_client import Letta` | Import only at module top; client is constructed inside main() on line 205, not at module level. | E |
| 144 | MODEL_CONFIG | `for m in client.models.list():` | [reclassified AGENT_LIST->MODEL_CONFIG: client.models.list() is a model-catalog query, not an agent listing] Inside _validate_model: enumerates available LLM handles to verify r… | E |
| 165 | BLOCK_CRUD | `existing = list(client.blocks.list(label=label, limit=1))` | Inside _get_or_create_block: checks whether a shared block with the given label already exists (idempotency guard). Called 6x from main for world_state, casper_r0_output, melchi… | E |
| 170 | BLOCK_CRUD | `b = client.blocks.create(` | Inside _get_or_create_block: creates a new shared block if none found by label. Reaches this only on first provision run per label. Parameters: label, value, description, limit,… | E |
| 205 | SDK_CONSTRUCT | `client = Letta(api_key=api_key)` | Client instantiated inside main(); api_key only, Letta Cloud default. Local variable passed to all helpers and the provisioning loop. | E |
| 211 | INDIRECT | `_validate_model(client, spec['model'], spec['agent_id'])` | Wrapper call site that drives client.models.list() at line 144 on each AGENT_SPECS iteration (three calls total at runtime). The enumeration captured only the inner SDK line at … | R |
| 309 | BLOCK_CRUD | `client.agents.blocks.list(existing_letta_id)` | UPDATE path: lists currently attached blocks for an already-provisioned agent to locate the persona block by label. Wrapped in try/except; result used to decide create-then-atta… | E |
| 318 | BLOCK_CRUD | `new_block = client.blocks.create(` | Defensive branch: creates a new persona block if agents.blocks.list found no block with label='persona' attached to an existing agent. Should not happen in normal operation. | E |
| 322 | BLOCK_ATTACH | `client.agents.blocks.attach(` | Attaches the newly created persona block (from line 318) to the existing agent. Only reached in the defensive branch where the live agent had no persona block. | E |
| 338 | BLOCK_CRUD | `client.blocks.update(` | Bumps the persona block's limit field to PERSONA_BLOCK_LIMIT (20000) when the live limit is below it, before pushing new content. Conditional on persona_block.limit < PERSONA_BL… | E |
| 343 | BLOCK_CRUD | `client.agents.blocks.update(` | UPDATE path: pushes new rendered persona content via agents.blocks.update keyed by block_label='persona' and agent_id. Only fires when persona_block.value differs from new_persona. | E |
| 365 | MODEL_CONFIG | `live = client.agents.retrieve(existing_letta_id).model_dump()` | Reads live agent state to extract current model_settings for diff comparison before deciding whether to push AGENT_CONFIG. .model_dump() converts Pydantic model to dict. | E |
| 371 | MODEL_CONFIG | `client.agents.update(` | Pushes model_settings=desired (from AGENT_CONFIG[agent_id]) to the live agent only when _model_settings_diff reports at least one differing key. Covers temperature, max_output_t… | E |
| 415 | AGENT_LIST | `agent_state = client.agents.create(` | CREATE path: provisions a new agent with name, model, embedding, memory_blocks (persona + self_model), block_ids (all 6 shared blocks), tools=[], include_base_tools=True. Only r… | E |

#### `magi/config_validator.py` (14)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 52 | IMPORT_ONLY | `from database import get_letta_agent_id` | Late import of the agent_registry lookup helper; no Letta network call — reads observer.db SQLite | E |
| 53 | IMPORT_ONLY | `from magi.provision_agents import _model_settings_diff` | Late import inside _check_one_agent() of the diff helper from provision_agents | E+R |
| 55 | INDIRECT | `letta_id = get_letta_agent_id(agent_key)` | Calls database helper get_letta_agent_id to look up the Letta agent UUID; this is the indirect Letta-registry read that gates all subsequent SDK calls for that agent. Not enumer… | R |
| 65 | AGENT_LIST | `state = client.agents.retrieve(letta_id)` | Retrieves live agent state to read model handle and model_settings; letta_id from agent_registry via get_letta_agent_id(agent_key) | E |
| 74 | MODEL_CONFIG | `sd = state.model_dump()` | Extracts serialized agent state dict; sd.get('model') and sd.get('model_settings') are the two drift-check targets | E |
| 76 | MODEL_CONFIG | `live_ms = sd.get("model_settings") or {}` | Reads live model_settings from retrieved agent state; compared against AGENT_CONFIG desired settings via _model_settings_diff | E |
| 82 | INDIRECT | `settings_diff = _model_settings_diff(live_ms, desired_settings)` | Calls provision_agents._model_settings_diff; that function compares dict keys only (no Letta call inside it), so this is local logic post-retrieve | E |
| 124 | IMPORT_ONLY | `from letta_client import Letta` | Import is inside _letta_client() function body — executed at call time, not module load | E |
| 125 | SDK_CONSTRUCT | `return Letta(api_key=api_key)` | _letta_client() factory; api_key read from LETTA_API_KEY env var or .env file; no base_url override means Letta Cloud default | E |
| 135 | MODEL_CONFIG | `from magi.provision_agents import AGENT_CONFIG, AGENT_SPECS` | Imports the canonical desired-config dicts; AGENT_CONFIG is the per-agent model_settings target; AGENT_SPECS carries model handles and agent_ids | E |
| 137 | SDK_CONSTRUCT | `client = _letta_client()` | Call site in validate_agent_configs(); delegates to _letta_client() wrapper defined at line 105 | E |
| 176 | IMPORT_ONLY | `from database import insert_alert` | Second database import inside alert_on_config_drift(); distinct from the line-52 import in _check_one_agent(). Not enumerated. | R |
| 177 | INDIRECT | `insert_alert(severity="critical", category="config_drift", agent_id=result["agent_key"], m` | insert_alert call inside alert_on_config_drift() — the primary production call path for config drift alerts. Not enumerated. The enumeration only listed the world_state_schema.p… | R |
| 203 | IMPORT_ONLY | `from magi.provision_agents import AGENT_CONFIG` | Third provision_agents import, in main(); imports AGENT_CONFIG for CLI reporting. Not enumerated. | R |

#### `scheduler.py` (12)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 11 | IMPORT_ONLY | `from magi.orchestrator import run_cycle` | run_cycle is the only orchestrator symbol directly imported. All Letta path is INDIRECT through this chain: run_magi_cycle -> run_cycle -> council.{update_world_state, run_round… | E |
| 333 | INDIRECT | `result = run_cycle(trigger=trigger)` | Called inside run_magi_cycle. Chain: run_cycle -> update_world_state (BLOCK_CRUD), run_round_0_parallel (AGENT_INVOKE x3 + BLOCK_CRUD x3), optional run_round_1 (AGENT_INVOKE x3)… | E |
| 385 | INDIRECT | `def sweep_letta_steps_for_failures():` | Function definition that owns the direct Letta REST API calls (lines 437, 458) and the LETTA_API_KEY env-var read (405). The enumerator listed its internal HTTP lines and its ca… | R |
| 429 | AGENT_LIST | `row = get_agent_registry_row(agent_id)` | Inside sweep_letta_steps_for_failures: resolves logical agent name ('casper'/'melchior'/'balthasar') to Letta agent UUID via agent_registry table. The UUID is then used as the a… | R |
| 437 | INDIRECT | `r = requests.get('https://api.letta.com/v1/runs', headers=headers, params={'agent_id': let` | Raw HTTP to Letta REST /v1/runs — not using letta_client SDK. Part of sweep_letta_steps_for_failures. | E |
| 458 | INDIRECT | `rs = requests.get(f"https://api.letta.com/v1/runs/{run['id']}/steps", headers=headers, tim` | Raw HTTP to Letta REST /v1/runs/{run_id}/steps — not using letta_client SDK. Part of sweep_letta_steps_for_failures. | E |
| 949 | INDIRECT | `run_magi_cycle(trigger='startup')` | Startup cycle — same chain as line 333. | E |
| 952 | INDIRECT | `run_magi_cycle(trigger='startup')` | Second startup call in the except-branch of the debounce try/except block (line 950: 'except Exception as e'). Enumerator listed only line 949; line 952 is equally reachable at … | R |
| 1005 | INDIRECT | `sweep_letta_steps_for_failures()` | sweep_letta_steps_for_failures is defined in scheduler.py:385. It calls Letta REST API directly via requests.get('https://api.letta.com/v1/runs') and 'https://api.letta.com/v1/r… | E |
| 1040 | INDIRECT | `run_magi_cycle(trigger='scheduled')` | Scheduled-hour cycle — same chain as line 333. | E |
| 1107 | INDIRECT | `run_magi_cycle(trigger=f'gate_wake:{pending}')` | Off-schedule gate-wake cycle — same chain as line 333. | E |
| 1126 | INDIRECT | `run_magi_cycle(trigger='manual')` | Manual trigger via Flask /internal/trigger_magi endpoint — same chain as line 333. | E |

#### `database.py` (9)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 291 | INDIRECT | `# world_state was only pushed to a single Letta Cloud block that gets` | Comment documents that orchestrator formerly pushed world_state to a Letta Cloud block each cycle (overwriting it); now snapshotted to debate_records.world_state column instead.… | E |
| 308 | INDIRECT | `# step_id: Letta Step.id for traceback / sweep dedup` | magi_alerts.step_id stores Letta Step.id values written by council.py alert path; database.py provides insert_alert() which accepts step_id. No SDK call in this file. | E |
| 362 | INDIRECT | `letta_agent_id TEXT NOT NULL,` | Schema column: Letta-assigned UUID for each agent stored in agent_registry table. No SDK call here; read by get_letta_agent_id() and consumed by council.py to route messages. | E |
| 363 | INDIRECT | `shared_world_block_id TEXT,` | Schema column: Letta block UUID for the shared world_state block; stored in agent_registry so council.py can call client.agents.blocks.attach / blocks.update by ID without listi… | E |
| 364 | INDIRECT | `shared_peer_block_ids TEXT,` | Schema column: JSON-serialised Letta block UUIDs for peer-output blocks (casper_r0_output, melchior_r0_output, balthasar_r0_output); stored in agent_registry. | E |
| 1264 | INDIRECT | `def register_agent(agent_id, letta_agent_id, model,` | Pure SQLite upsert persisting Letta UUID mappings; no SDK call. Called by magi/provision_agents.py after client.agents.create/retrieve. | E |
| 1300 | INDIRECT | `def get_letta_agent_id(agent_id):` | Returns the Letta UUID string from SQLite; downstream callers (council.py) use it to construct SDK calls. No SDK call here. | E |
| 1311 | INDIRECT | `def get_agent_registry_row(agent_id):` | Peer to get_letta_agent_id (line 1300) — returns the full agent_registry row including letta_agent_id and shared_world_block_id / shared_peer_block_ids. Enumerated 1300 but skip… | R |
| 1519 | INDIRECT | `# (step_id is a Letta-assigned unique identifier; if we've already` | insert_alert() dedup logic uses Letta Step.id as a dedup key to avoid re-alerting on the same Letta step failure; no SDK call. | E |

#### `magi/memory_lifecycle.py` (8)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 49 | IMPORT_ONLY | `from letta_client import Letta` | Import of Letta class; client constructed at module level on line 155 | E |
| 155 | SDK_CONSTRUCT | `_client = Letta(api_key=_api_key)` | Module-level singleton client; api_key only (Letta Cloud default base_url). Mirrors council.py pattern. Constructed at import time — raises RuntimeError if LETTA_API_KEY missing. | E |
| 272 | BLOCK_CRUD | `blocks = list(_client.agents.blocks.list(letta_id))` | Inside _snapshot_self_model: lists all blocks attached to the agent to locate the self_model block before snapshotting. Called once per agent per rotation. | E |
| 298 | THREAD_COMPACT | `result = _client.agents.messages.compact(` | Inside _compact_and_extract: runs self_compact_sliding_window with DISTILL_PROMPT to produce new self_model patterns. Mode='self_compact_sliding_window', sliding_window_percenta… | E |
| 319 | BLOCK_CRUD | `blocks = list(_client.agents.blocks.list(letta_id))` | Inside _merge_into_self_model: re-reads all attached blocks to get the live self_model value immediately before pushing the merged update. Second list call in the same rotation … | E |
| 341 | BLOCK_CRUD | `_client.blocks.update(getattr(sm, 'id'), value=merged)` | Inside _merge_into_self_model: server-side write of merged self_model patterns. Uses top-level blocks.update (not agents.blocks.update) keyed by block id, not label. Only fires … | E |
| 358 | THREAD_RESET | `_client.agents.messages.reset(` | Inside _reset_thread: clears the agent message thread post-merge. add_default_initial_messages=False so thread starts clean. Only fires after successful merge — guarded by the m… | E |
| 503 | BLOCK_CRUD | `blocks = list(_client.agents.blocks.list(letta_id))` | Inside rotate_agent_memory step 4 post-merge: third agents.blocks.list in a single rotation to re-read chars_after for the DB record. Best-effort; exception is caught and logged. | E |

#### `dashboard.py` (7)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 2970 | INDIRECT | `letta_census=_fetch_letta_agent_census(),` | Call site that invokes the entire Letta census block on every dashboard render. The enumerator reported the implementation lines (3004/3009/3020) but missed this call site that … | R |
| 3004 | SDK_CONSTRUCT | `from letta_client import Letta as _Letta` | Lazy import inside _fetch_letta_agent_census(); executed only when the 60-second cache is stale and no cached value exists. | E |
| 3009 | SDK_CONSTRUCT | `c = _Letta(api_key=os.environ['LETTA_API_KEY'])` | Client constructed unconditionally inside _fetch_letta_agent_census() on every cache-miss. No module-level client; construction is strictly local to this one function. | E |
| 3020 | AGENT_LIST | `page = list(c.agents.list(limit=100, **({{"after": after}} if after else {})))` | Paginated list of ALL agents on the API key. Used to compute total/eval/prod counts for the LETTA AGENTS chip panel. Called inside a while-True loop until fewer than 100 results… | E |
| 3028 | AGENT_LIST | `after = page[-1].id` | Cursor-pagination continuation: reads .id attribute from a Letta SDK agent object returned by c.agents.list(). This is part of the paginated AGENT_LIST loop and was not enumerated. | R |
| 3030 | AGENT_LIST | `prod_count = sum(1 for a in all_agents if a.id in prod_ids)` | Iterates over Letta SDK agent objects and reads .id to cross-reference against agent_registry. Direct SDK object attribute consumption from the agents.list() result. | R |
| 3031 | AGENT_LIST | `eval_count = sum(1 for a in all_agents if eval_re.match(a.name or ""))` | Reads .name attribute from Letta SDK agent objects in the agents.list() result to classify eval vs prod agents. | R |

#### `observer.py` (7)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 217 | SDK_CONSTRUCT | `_letta_client = None` | Module-level global that holds the lazy-init Letta client instance. The SDK_CONSTRUCT at line 233 assigns into this. Omitting it leaves the client lifecycle incomplete. | R |
| 220 | SDK_CONSTRUCT | `def _get_letta_client():` | Wrapper function that lazy-initialises and caches the Letta client. Lines 232-233 live inside it, so the function definition is the enclosing SDK_CONSTRUCT boundary. | R |
| 232 | SDK_CONSTRUCT | `from letta_client import Letta` | Lazy import inside _get_letta_client(); only executed on first 6h backfill cycle when LETTA_API_KEY is set | E |
| 233 | SDK_CONSTRUCT | `_letta_client = Letta(api_key=api_key)` | Module-level singleton; api_key-only construction defaults to Letta Cloud endpoint | E |
| 346 | SDK_CONSTRUCT | `client = _get_letta_client()` | Invocation of the lazy-init wrapper inside _record_outcome_to_block. This is the live call-site that materialises the Letta client before the BLOCK_CRUD calls at 370 and 383. | R |
| 370 | BLOCK_CRUD | `blk = next(iter(client.blocks.list(label='recent_outcomes', limit=1)), None)` | Reads the project-scoped recent_outcomes block by label to get its ID and current value before overwriting | E |
| 383 | BLOCK_CRUD | `client.blocks.update(blk.id, value=header + "\n" + "\n".join(kept))` | Overwrites recent_outcomes block with rolling 6-item outcome log; called once per 6h backfill maturation | E |

#### `config.py` (7)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 65 | INDIRECT | `WAKE_REQUIRES_ACTIVE_GRID = True` | [line corrected 62->65] Comment at line 62 documents that the flag prevents waking 'the LLM council' (Letta agents). The flag itself is pure Python; no Letta call here. Consumed… | E |
| 140 | MODEL_CONFIG | `LETTA_STEPS_SWEEP_INTERVAL_MIN = 30` | Cadence constant governing scheduler.py:sweep_letta_steps_for_failures (the background HTTP sweep of https://api.letta.com/v1/runs). Pure config; no Letta call in this file. Con… | E |
| 146 | MODEL_CONFIG | `ROTATION_CADENCE = 30` | Governs how often memory_lifecycle.rotate_agent_memory (which calls client.agents.messages.compact and client.blocks.update) is triggered. Consumed by scheduler.py and memory_li… | E |
| 148 | MODEL_CONFIG | `# Sliding-window percentage passed to client.agents.messages.compact().` | Comment documents ROTATION_WINDOW_PCT is forwarded to client.agents.messages.compact() in magi/memory_lifecycle.py. config.py itself makes no Letta call. | E |
| 151 | MODEL_CONFIG | `ROTATION_WINDOW_PCT = 0.35` | Value passed as sliding_window_percentage to client.agents.messages.compact() inside magi/memory_lifecycle.py:rotate_agent_memory. Config-level seam for THREAD_COMPACT migration. | E |
| 157 | MODEL_CONFIG | `SELF_MODEL_CHAR_CAP = 5000` | Cap applied after client.agents.messages.compact() returns the summarised self_model in memory_lifecycle.py. Config-level seam. | E |
| 162 | MODEL_CONFIG | `MAX_NEW_PATTERNS = 2` | [line corrected 163->162] Limits patterns extracted from the compacted thread in memory_lifecycle.py. Config-level seam. | E |

#### `magi/orchestrator.py` (6)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 58 | IMPORT_ONLY | `from magi.council import (emit_human_alert, resolve_consensus, run_round_0_parallel, run_r` | All council.py wrappers are imported here; the actual Letta calls are in council.py. The orchestrator calls these wrappers at lines 1849, 1857, 1877, 1880, 1899. | E |
| 1849 | INDIRECT | `update_world_state(world_state)` | Wrapper update_world_state in council.py:1002 — underlying call: client.blocks.update(block_id, value=payload) on the 'world_state' block (council.py:1011) | E |
| 1857 | INDIRECT | `round_0 = run_round_0_parallel(cycle_id, world_state)` | Wrapper run_round_0_parallel in council.py:1254 — calls set_cycle_phase (blocks.update 'cycle_phase'), then fans out send_round_0 to all 3 agents (client.agents.messages.create … | E |
| 1877 | INDIRECT | `fire_r1, r1_reason = should_run_r1(round_0, prior_sig)` | Wrapper should_run_r1 in council.py:1445 — pure logic, no Letta call. Listed because it is a council boundary function; no SDK touch. | E |
| 1880 | INDIRECT | `round_1 = run_round_1(round_0, cycle_id)` | Wrapper run_round_1 in council.py:1466 — calls set_cycle_phase (blocks.update 'cycle_phase'), then fans out send_round_1_synthesis to all 3 agents (client.agents.messages.create… | E |
| 1899 | INDIRECT | `cons = resolve_consensus(round_0, round_1, conflict)` | Wrapper resolve_consensus in council.py:1566 — pure logic operating on already-parsed dicts, no Letta call. | E |

#### `magi/readiness.py` (4)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 117 | INDIRECT | `"SELECT casper_r0_position FROM debate_records "` | _regime_at(): reads casper_r0_position from debate_records, which is written by council.py from Casper's Letta agent R0 response. No SDK call; reads the artefact. | E |
| 229 | INDIRECT | `"SELECT timestamp, casper_r0_position, casper_r0_conviction "` | gate_L3(): reads casper_r0_position and casper_r0_conviction from debate_records (Casper Letta agent outputs). No SDK call. | E |
| 364 | INDIRECT | `"FROM debate_records"` | [line corrected 359->364; FROM debate_records is at 364] gate_L8(): reads hard_rule_overrides from debate_records (written per-cycle by orchestrator after Letta council round). … | E |
| 386 | INDIRECT | `"FROM debate_records"` | gate_L9(): reads debate_triggered from debate_records (flag set by council.py's should_run_r1 logic, which gates a second Letta round). No SDK call. | E |

#### `magi/validate_schema.py` (3)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 27 | IMPORT_ONLY | `from magi.world_state_schema import (AGENTS, load_persona, validate_persona_references, va` | validate_schema.py has zero direct Letta SDK imports or calls; all Letta awareness is indirect via world_state_schema helpers that read on-disk persona files | E |
| 47 | INDIRECT | `from magi.orchestrator import build_world_state` | build_world_state() in orchestrator.py calls alert_on_runtime_drift (world_state_schema) and reads observer.db; does NOT make a Letta call itself — Letta coupling is in the coun… | E |
| 58 | INDIRECT | `ws = build_world_state()` | Executes build_world_state to get a runtime snapshot for schema validation; no Letta network traffic during this call in isolation | E |

#### `magi/world_state_schema.py` (2)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 870 | IMPORT_ONLY | `from database import insert_alert` | Late import inside alert_on_runtime_drift(); writes to observer.db, not Letta; fires ntfy via notify.py on critical severity as a side-effect | E |
| 881 | INDIRECT | `insert_alert(severity="critical", category="schema_drift_runtime", message=msg,)` | No direct Letta call; insert_alert → notify.py:send_ntfy() → ntfy.sh HTTPS on critical. This is the runtime schema-drift alert path | E |

#### `magi/gate_monitor.py` (2)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 355 | INDIRECT | `from magi.gate import evaluate_gate` | Imports and calls evaluate_gate (magi/gate.py). gate.evaluate_gate is a pure DB-read + predicate function that writes magi_gate_events rows. It does NOT call Letta. gate_monitor… | E |
| 356 | INDIRECT | `fired = evaluate_gate(self.db_path)` | Actual call site for evaluate_gate. Rows written to magi_gate_events are later consumed by orchestrator.build_world_state and pushed to Letta agent world_state blocks. The chain… | E |

#### `magi/learning.py` (1)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 36 | INDIRECT | `rows = conn.execute('''SELECT * FROM magi_decisions` | Reads magi_decisions table (legacy dual-write of per-agent council votes: melchior_action, casper_action, balthasar_action, consensus_*). This data originates from Letta agent r… | E |

#### `extract_test_cases.py` (1)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 21 | INDIRECT | `FROM magi_decisions` | Reads magi_decisions table, selecting melchior_action, casper_action, balthasar_action, consensus_grid_action, consensus_regime, notes — all fields populated by Letta agent resp… | E |

#### `magi/costs.py` (1)

| Line | Category | Call / import | Note | Src |
|---:|---|---|---|---|
| 3 | INDIRECT | `# agent_registry stores 'anthropic/claude-haiku-4-5'; estimate_cost` | Comment documents that the model key format used by estimate_cost matches what agent_registry (Letta DB table) stores. estimate_cost is called from observer.py after it reads ag… | E |

---

## 3. Migration seams — functions that wrap a Letta call

These are the natural cut-points for the port. A native rebuild replaces the *body* of each; callers stay the same.

| Wrapper | File:Line | Underlying Letta call | Callers |
|---|---|---|---|
| `update_world_state` | `magi/council.py:1002` | client.blocks.update(block_id, value=payload) — block label 'world_state' | magi/orchestrator.py:1849 |
| `run_round_0_parallel` | `magi/council.py:1254` | set_cycle_phase (client.blocks.update 'cycle_phase') + send_round_0 x3 (client.agents.messages.create) + client.blocks.u | magi/orchestrator.py:1857 |
| `run_round_1` | `magi/council.py:1466` | set_cycle_phase (client.blocks.update 'cycle_phase') + send_round_1_synthesis x3 (client.agents.messages.create per agen | magi/orchestrator.py:1880 |
| `should_run_r1` | `magi/council.py:1445` | None — pure logic gate, no Letta call | magi/orchestrator.py:1877 |
| `resolve_consensus` | `magi/council.py:1566` | None — pure dict processing of already-parsed R0/R1 votes, no Letta call | magi/orchestrator.py:1899 |
| `emit_human_alert` | `magi/council.py:1638` | Not called from orchestrator.py in the current cycle flow — imported but not invoked in run_cycle; underlying call when  | magi/orchestrator.py:59 (import only) |
| `run_magi_cycle` | `scheduler.py:287` | run_cycle (magi/orchestrator.py:1832) which reaches all Letta calls via council.py wrappers | scheduler.py:949 (startup), scheduler.py:952 (startup fallback), scheduler.py:1040 (schedu |
| `sweep_letta_steps_for_failures` | `scheduler.py:385` | requests.get('https://api.letta.com/v1/runs') and requests.get('https://api.letta.com/v1/runs/{run_id}/steps') — raw HTT | scheduler.py:1005 |
| `_get_shared_block_id` | `magi/council.py:337` | client.blocks.list(label=label, limit=1) — lookup + cache shared block IDs by label | council.py:1007 (update_world_state), council.py:1019 (set_cycle_phase), council.py:1232 ( |
| `_check_steps_for_alerts` | `magi/council.py:67` | client.steps.retrieve(sid) | send_round_0 (attempt loop), send_round_0 (freshness retry), send_round_1_synthesis |
| `_record_token_usage_from_response` | `magi/council.py:157` | client.steps.retrieve(sid) | send_round_0 (attempt loop), send_round_0 (freshness retry), send_round_1_synthesis |
| `set_cycle_phase` | `magi/council.py:1015` | client.blocks.update(block_id, value=phase) via _get_shared_block_id('cycle_phase') | run_round_0_parallel, run_round_1 |
| `send_round_0` | `magi/council.py:1024` | client.agents.messages.create(letta_id, messages=[...]) | run_round_0_parallel (via ThreadPoolExecutor, parallel x3) |
| `send_round_1_synthesis` | `magi/council.py:1332` | client.agents.messages.create(letta_id, messages=[...]) | run_round_1 (via ThreadPoolExecutor, parallel x3) |
| `_snapshot_self_model` | `magi/memory_lifecycle.py:264` | _client.agents.blocks.list(letta_id) [line 272] | rotate_agent_memory |
| `_compact_and_extract` | `magi/memory_lifecycle.py:295` | _client.agents.messages.compact(agent_id=letta_id, compaction_settings={...}) [line 298] | rotate_agent_memory |
| `_merge_into_self_model` | `magi/memory_lifecycle.py:313` | _client.agents.blocks.list(letta_id) [line 319]; _client.blocks.update(block_id, value=merged) [line 341] | rotate_agent_memory |
| `_reset_thread` | `magi/memory_lifecycle.py:352` | _client.agents.messages.reset(letta_id, add_default_initial_messages=False) [line 358] | rotate_agent_memory |
| `rotate_agent_memory` | `magi/memory_lifecycle.py:370` | _snapshot_self_model, _compact_and_extract, _merge_into_self_model, _reset_thread (all Letta-reaching wrappers); plus di | maybe_rotate |
| `maybe_rotate` | `magi/memory_lifecycle.py:544` | rotate_agent_memory for each of casper/melchior/balthasar — full Letta pipeline per agent | scheduler._update_rotation_counter_and_maybe_rotate (scheduler.py:279) |
| `_get_or_create_block` | `magi/provision_agents.py:160` | client.blocks.list(label=label, limit=1) [line 165]; client.blocks.create(...) [line 170] | main (6x: world_state, casper_r0_output, melchior_r0_output, balthasar_r0_output, cycle_ph |
| `_validate_model` | `magi/provision_agents.py:138` | client.models.list() [line 144] | main (3x, once per AGENT_SPECS entry) |
| `_fetch_letta_agent_census` | `dashboard.py:2986` | c.agents.list(limit=100, after=...) | index (line 2970) |
| `_get_letta_client` | `observer.py:220` | Letta(api_key=api_key) | _record_outcome_to_block |
| `_record_outcome_to_block` | `observer.py:330` | client.blocks.list(label='recent_outcomes', limit=1) + client.blocks.update(blk.id, value=...) | backfill_outcomes (observer.py:444) |
| `register_agent` | `database.py:1264` | SQLite upsert on agent_registry — no direct SDK call; called by magi/provision_agents.py after Letta agent creation | magi/provision_agents.py (external) |
| `get_letta_agent_id` | `database.py:1300` | SQLite SELECT on agent_registry.letta_agent_id — UUID used by council.py for client.agents.messages.create | magi/council.py (external) |
| `get_agent_registry_row` | `database.py:1311` | SQLite SELECT * on agent_registry — returns shared_world_block_id and shared_peer_block_ids used by council.py for block | magi/council.py (external) |
| `insert_alert` | `database.py:1510` | SQLite INSERT into magi_alerts; step_id dedup key is Letta Step.id sourced from council.py SDK responses | magi/council.py (external), magi/orchestrator.py (external) |
| `_letta_client` | `magi/config_validator.py:105` | from letta_client import Letta; return Letta(api_key=api_key) | validate_agent_configs (line 137) |
| `_check_one_agent` | `magi/config_validator.py:40` | client.agents.retrieve(letta_id) | validate_agent_configs (line 146) |
| `validate_agent_configs` | `magi/config_validator.py:128` | client.agents.retrieve via _check_one_agent | alert_on_config_drift (line 159), main (line 198) |
| `alert_on_config_drift` | `magi/config_validator.py:151` | validate_agent_configs -> client.agents.retrieve; database.insert_alert on drift | scheduler.run_magi_cycle (start + end hooks, per module docstring) |
| `alert_on_runtime_drift` | `magi/world_state_schema.py:858` | database.insert_alert (no direct Letta call; triggers notify.py:send_ntfy on critical) | magi/orchestrator.py:build_world_state (per module docstring line 13) |
| `render_persona_with_signals` | `magi/world_state_schema.py:1227` | load_persona (disk read) + render_signals_block (pure FIELDS computation); called by provision_agents.py which then writ | magi/provision_agents.py lines 306 and 398 |
| `evaluate_gate` | `magi/gate.py:638` | None — evaluate_gate itself is pure DB read/write (magi_gate_events). Its outputs flow to orchestrator.build_world_state | /root/xrp_grid/magi/gate_monitor.py:356 |
| `estimate_cost` | `magi/costs.py:26` | No Letta call. Pure arithmetic. Consumes model name strings originally sourced from agent_registry (Letta metadata table | observer.py (reads debate_records + agent_registry, then calls estimate_cost to log per-cy |
| `_is_wake_suppressed_nontrading` | `scheduler.py:507` | No Letta call. Reads debate_records and grid_state from observer.db to decide whether to suppress a wake that would othe | /root/xrp_grid/wake_guard_sim.py:94 |

---

## 4. Letta block labels — the shared-state surface

Eight live block labels plus two `agent_registry` columns that hold block UUIDs. On a stateless native platform, the per-cycle signalling blocks (`world_state`, `cycle_phase`, `*_r0_output`, `recent_outcomes`) become prompt-injected strings; only `persona` (→ system prompt, already on disk) and `self_model` (→ needs a new local store) carry state.


**`world_state`**
- *Schema constraint:* JSON-serialized world_state dict; contents validated by world_state_schema.py FIELDS at every build_world_state() call. Size up to 15000 chars limit set in provision_agents.py.
- *Written by:* magi/council.py (update_world_state — external, not in these two files); magi/council.py:1011 (update_world_state — client.blocks.update); magi/orchestrator.py (build_world_state / cycle push); magi/orchestrator.py (build_world_state, updat
- *Read by:* All three Letta agents at R0 inference time; All three agents read via their attached block (Letta memory context on each messages.create call); all three agents read natively via Letta memory context during R0/R1; _validate_r0_freshness cr

**`cycle_phase`**
- *Schema constraint:* Values: 'round_0' \| 'round_1'. Limit=200 chars. read_only=True for agents.
- *Written by:* magi/council.py:1020 (set_cycle_phase — client.blocks.update, values: 'round_0' \| 'round_1'); magi/orchestrator.py (cycle phase transitions: round_0 / round_1); magi/orchestrator.py (sets 'round_0' or 'round_1' at debate phase transitions);
- *Read by:* All three agents read via attached block; tells agents which round they are responding to; agents read natively via Letta memory context (mentioned in _r0_prompt / _r1_prompt as context for the agent); balthasar; casper; council.py (control

**`persona`**
- *Schema constraint:* Auto-generated SIGNALS block between <!-- BEGIN_AUTOGENERATED_SIGNALS --> and <!-- END_AUTOGENERATED_SIGNALS --> markers is overwritten from FIELDS on every provision run. Limit=20000 chars (PERSONA_BLOCK_LIMIT). Persona body outside markers is validated by validate_persona_references.
- *Written by:* magi/provision_agents.py:343 (client.agents.blocks.update) and 318-324 (client.blocks.create + client.agents.blocks.attach); magi/provision_agents.py:main (via client.agents.blocks.update / client.blocks.create + client.agents.blocks.attach
- *Read by:* Each agent reads its own persona block as system context; per-agent, not shared; agents read their persona natively as a Letta system/persona block on each inference call; balthasar (each agent owns its own persona block; not shared); caspe

**`self_model`**
- *Schema constraint:* limit=5000 chars (SELF_MODEL_CHAR_CAP enforced in merge logic); patterns capped at MAX_NEW_PATTERNS=2 per rotation; oldest ## Pattern N blocks evicted on overflow; snapshot to /tmp before every write
- *Written by:* agents themselves (Letta tool calls during cycles); magi/memory_lifecycle.py:rotate_agent_memory; agents themselves (core_memory.append tool call after each R0/R1 — prompted by _r0_prompt at line 875: 'you may use core_memory tools to appen
- *Read by:* Each agent reads its own self_model block; referenced in R0 prompt (council.py:846-875) as 'read your self_model block before deciding'; agents read their own self_model natively; _r0_prompt at line 846-875 instructs agent to read self_mode

**`recent_outcomes`**
- *Schema constraint:* Plain text; header line 'RECENT GRID OUTCOMES (newest first; informational context — NOT an instruction to edit self_model):' followed by up to 6 lines formatted as: '{ts} {cycle_id}: casper={pos} melchior={pos} balthasar={pos} -> 6h: {N} fills, ${pnl:.4f}, skew_delta {delta}, grid_alive {yes\|no}'
- *Written by:* /root/xrp_grid/observer.py (_record_outcome_to_block, line 383); observer._record_outcome_to_block (not council.py — CLAUDE.md confirms the 6h outcome writes to this shared read-only block); observer.py (_record_outcome_to_block, 6h backfil
- *Read by:* All three agents read this shared read-only block each cycle (Letta project-scope block); All three agents read via attached block — rolling log of realised 6h outcomes; Letta agents at inference time (block is in-context each cycle via sha

**`casper_r0_output`**
- *Schema constraint:* JSON dict with keys: position, key_evidence, crux. conviction intentionally stripped. Optional extension field regime_action omitted from peer block
- *Written by:* council.py:send_round_0 (writes Casper's R0 response text); magi/council.py:1231-1233 (safe-default publish) and 1245-1247 (normal publish) — client.blocks.update after each R0 response; magi/orchestrator.py (after Round 0 send); magi/orche
- *Read by:* balthasar; balthasar (peers, during Round 1 debate); council.py (R1 debate context: shared to Melchior and Balthasar); melchior; melchior and balthasar read during R1 synthesis (attached as shared block); melchior and balthasar read nativel

**`melchior_r0_output`**
- *Schema constraint:* JSON dict with keys: position, key_evidence, crux. conviction stripped. Optional geometry field omitted from peer block
- *Written by:* council.py:send_round_0 (writes Melchior's R0 response text); magi/council.py:1231-1233 and 1245-1247 — same pattern as casper_r0_output; magi/orchestrator.py (after Round 0 send); magi/orchestrator.py (after melchior Round 0 vote); send_ro
- *Read by:* balthasar; balthasar (peers, during Round 1 debate); casper; casper and balthasar read during R1 synthesis; casper and balthasar read natively from Letta memory during R1; also pasted in R1 user message; council.py (R1 debate context: share

**`balthasar_r0_output`**
- *Schema constraint:* JSON dict with keys: position, key_evidence, crux. conviction stripped. Optional extension field geometry_veto omitted from peer block
- *Written by:* council.py:send_round_0 (writes Balthasar's R0 response text); magi/council.py:1231-1233 and 1245-1247 — same pattern as casper_r0_output; magi/orchestrator.py (after Round 0 send); magi/orchestrator.py (after balthasar Round 0 vote); send_
- *Read by:* casper; casper and melchior read during R1 synthesis; casper and melchior read natively from Letta memory during R1; also pasted in R1 user message; council.py (R1 debate context: shared to Casper and Melchior); melchior; melchior (peers, d

**`shared_world_block_id`**
- *Schema constraint:* Letta block UUID string stored in agent_registry.shared_world_block_id column
- *Written by:* magi/provision_agents.py via database.register_agent()
- *Read by:* magi/council.py via database.get_agent_registry_row()

**`shared_peer_block_ids`**
- *Schema constraint:* JSON-serialised dict/list of Letta block UUIDs for per-agent R0 output blocks (casper_r0_output, melchior_r0_output, balthasar_r0_output)
- *Written by:* magi/provision_agents.py via database.register_agent()
- *Read by:* magi/council.py via database.get_agent_registry_row()

**`letta_agent_id (agent_registry column)`**
- *Schema constraint:* SELECT letta_agent_id FROM agent_registry WHERE letta_agent_id IS NOT NULL — used to identify prod agents vs. eval agents in the census count
- *Written by:* —
- *Read by:* _fetch_letta_agent_census (line 3012-3014)

---

## 5. world_state — build / push / read, and the schema contract

`world_state` is the single richest coupling. Flow each cycle:

1. **Build** — `magi/orchestrator.py:build_world_state()` (≈ lines 483–609) assembles the full dict from `observer.db` via `database.py` helpers (incl. `get_trajectory_context`, `database.py:744–875`) plus local computation.
2. **Validate** — `magi/world_state_schema.py:alert_on_runtime_drift(ws)` runs at the end of every build; any schema mismatch writes a `critical` `magi_alerts` row (→ ntfy) but does not stop trading. `validate_schema.py` enforces the same `FIELDS` contract at provisioning time (exit 1 on error).
3. **Push** — `orchestrator.run_cycle():1849` → `council.update_world_state()` → `client.blocks.update` on the shared `world_state` block (limit 15000 chars, `read_only=True` for agents). **Also snapshotted** to `debate_records.world_state` (`database.py:295`), so the full per-cycle JSON already has a local home.
4. **Read** — the three agents read it natively from the Letta block at inference; the Python side reads the *in-memory dict* (never the block) in `enforce_hard_rules` (orchestrator.py:845–1528) and `_validate_r0_freshness` (council.py:587–664).

**Schema-contract status (FIELDS ↔ build_world_state).** `magi/world_state_schema.py:FIELDS` is the declared source of truth; `build_world_state` is validated against it at runtime by `alert_on_runtime_drift` and at provisioning by `validate_schema`. **No static drift was surfaced by the audit** — but note the agents relied on these runtime/provisioning validators as the enforcement point rather than producing an exhaustive field-by-field static diff. If you want belt-and-suspenders confidence before the port, an explicit `FIELDS` vs. `build_world_state()` output diff is a small residual task (see §8). The migration implication is favorable: **`world_state` needs no new persistence** — it is rebuilt fresh each cycle and would be injected directly into each native API call's message payload; the block is pure transport.

**Recorded build/push/read sites:**

| Action | Location | Fields (sample) |
|---|---|---|
| build | magi/orchestrator.py:build_world_state() lines 483-609 | timestamp, price, indicators.*, grid_state.*, inventory.*, open_orders.*, hours_since_last_fill, hours_since_last_rebuil |
| push | magi/orchestrator.py:run_cycle() line 1849 | all top-level keys — the entire world_state dict is JSON-serialized |
| read | magi/orchestrator.py:run_cycle() lines 1857, 1880 | triggers_since_last_cycle (threaded into send_round_0 prompt construction) |
| read | magi/orchestrator.py:enforce_hard_rules() lines 845-1528 | inventory.xrp_held, inventory.usd_held, inventory.inventory_skew, price, portfolio.xrp_value_usd, open_orders.buy_count, |
| push | council.py:1002-1012 update_world_state() | entire world_state dict — all fields from magi/world_state_schema.py:FIELDS |
| read | council.py:1064 send_round_0() | triggers_since_last_cycle |
| read | council.py:587-664 _validate_r0_freshness() | all scalar fields recursively via _walk_ws_path_value_pairs — compared against agent's key_evidence numbers |
| read | council.py:271-282 _buy_count/_sell_count/_hours_since_fill (CONFLICT_MATRIX predicates) | open_orders.buy_count, open_orders.sell_count, hours_since_last_fill |
| build | magi/provision_agents.py:218-223 | (block created with placeholder value '(awaiting first cycle)') |
| read | magi/memory_lifecycle.py:114-143 (DISTILL_PROMPT) | buy_count, sell_count, allocation_skew, hours_since_last_fill, roc_6h, pause_longs, grid_alive |
| read | dashboard.py _fetch_council_data (line 2426, 2471-2589) | casper_r0_conviction, melchior_r0_conviction, balthasar_r0_conviction, casper_r0_position, melchior_r0_position, balthas |
| read | dashboard.py _fetch_agent_health (line 2690-2699) | casper_r0_conviction, casper_r0_crux, melchior_r0_conviction, melchior_r0_crux, balthasar_r0_conviction, balthasar_r0_cr |
| read | /root/xrp_grid/database.py:287-295 (ALTER TABLE comment + column definition) | all world_state fields (full JSON snapshot) |
| build | /root/xrp_grid/database.py:744-875 (get_trajectory_context) | regime_consecutive, melchior_blocked_cycles, skew_delta, skew_trend, fills_since_last_magi_buys, fills_since_last_magi_s |
| push | /root/xrp_grid/observer.py:330-386 (_record_outcome_to_block) | cycle_id, casper_r0_position, melchior_r0_position, balthasar_r0_position, fills_count (6h), pnl (6h), skew_delta, grid_ |
| build | magi/orchestrator.py:build_world_state | timestamp, price, hours_since_last_fill, hours_since_last_rebuild, skew_delta_since_rebuild, current_spacing_pct, curren |
| push | magi/orchestrator.py:build_world_state → world_state Letta block push | all FIELDS |
| read | magi/validate_schema.py:check_runtime_output | all FIELDS (schema paths) |
| read | magi/world_state_schema.py:validate_runtime_output | all FIELDS |
| read | magi/world_state_schema.py:alert_on_runtime_drift | all FIELDS |
| read | /root/xrp_grid/magi/readiness.py:117 | casper_r0_position |
| read | /root/xrp_grid/magi/readiness.py:229-230 | casper_r0_position, casper_r0_conviction |
| read | /root/xrp_grid/magi/readiness.py:359-364 | hard_rule_overrides |
| read | /root/xrp_grid/magi/readiness.py:386 | debate_triggered |
| read | /root/xrp_grid/magi/learning.py:36 | consensus_grid_action, consensus_risk_action, consensus_regime, trigger, notes |
| read | /root/xrp_grid/extract_test_cases.py:7-26 | melchior_action, melchior_reasoning, melchior_concerns, casper_action, casper_reasoning, balthasar_action, balthasar_rea |

---

## 6. Persistence that lives in Letta today → local target

What must move when Letta Cloud goes away. **Bold = no local mirror exists today** (genuine migration work); the rest are already mirrored or rebuilt fresh.

| What | Where in Letta | Local target needed |
|---|---|---|
| **Per-agent conversation thread history** | Letta-managed message thread per agent; appended every R0/R1 via `messages.create`. Melchior/GPT-4o anchors on it (documented). | **No local mirror.** Decide: stateless fresh call each cycle (drop history) vs. replay into native thread. `debate_records` holds structured votes but NOT raw turn text. Export before decommission if wanted for audit/fine-tuning. |
| **`self_model` block contents (between rotations)** | Per-agent Letta block, agent-written via `core_memory` tools; rotated every 30 cycles by `memory_lifecycle`. | **No local mirror between rotations** (only a pre-mutation `/tmp` snapshot + `memory_rotations` metadata). Needs a per-agent mutable text store in `observer.db` (or file), read at cycle start. |
| `world_state` block | Shared block, overwritten each cycle. | Already snapshotted to `debate_records.world_state`; rebuilt fresh from SQLite each cycle. Inject as message content — no new store. |
| `cycle_phase` block | Shared block ('round_0'\|'round_1'). | Eliminate — embed phase in the prompt text. |
| `*_r0_output` peer blocks (×3) | Shared blocks for R1 peer visibility. | Already in Python memory (the `round_0` dict). Inline peer outputs into the R1 prompt (R1 already pastes them explicitly). Block writes droppable. |
| `recent_outcomes` block | Project-scope shared block; written by `observer._record_outcome_to_block`. | Derived entirely from `debate_records` + `grid_orders`. Build the string fresh from DB and inject; remove the block write. |
| `persona` blocks (per-agent) | Per-agent Letta block, synced from `magi/prompts/*`. | Source of truth already on disk; `render_persona_with_signals` already builds the text. Pass as system prompt — no migration of content, but SIGNALS-block rendering must be reproduced. |
| Agent UUIDs (`letta_agent_id`) | `observer.db:agent_registry` maps logical name → Letta UUID; agents persist on Letta's servers. | Repurpose `agent_registry`: replace UUID with native identifier (OpenAI assistant_id / Anthropic has none / Gemini has none). `get_letta_agent_id` / `get_agent_registry_row` are the lookups. |
| LLM model config (`model_settings`) | Letta agent `model_settings`, synced from `AGENT_CONFIG`. | `AGENT_CONFIG` in `provision_agents.py` is already the source of truth → becomes constructor/call-time kwargs per provider SDK. |
| Letta `Step` objects (tokens, stop_reason, errors) | Letta Cloud step log via `client.steps.retrieve`. | Native `response.usage` / SDK exceptions return this inline — no separate retrieval. Local sink (`magi_token_usage`, `magi_alerts.step_id`) already platform-agnostic; map `step_id`→provider request_id. |

---

## 7. Design questions surfaced (operator decisions before the spec)

Curated and de-duplicated from all groups; grouped by theme. These cannot be answered mechanically.


### A. Statelessness & thread history
- Start each native cycle **stateless** (drop server-side thread) or replay history into the new provider? Melchior/GPT-4o specifically anchors on thread history — dropping it changes behavior. (council, orch, observer, utilities)
- Before decommissioning Letta, **export raw message-turn history** for audit/fine-tuning? `debate_records` keeps structured votes (position/crux/evidence) but not raw response text. (observer+database)
- `_validate_r0_freshness` exists to catch thread-history anchoring — structurally absent when stateless. Keep it as a generic hallucination guard (costs one extra call per stale agent) or drop it? (orch, council)

### B. self_model lifecycle (no `core_memory`, no `messages.compact` natively)
- The R0 prompt tells agents to `core_memory.append` to their `self_model` mid-inference. Native stateless calls can't do this. Replace with: (a) agent emits a structured `self_model_update` field the orchestrator persists, or (b) batch updates at cycle end? (council, validators)
- `memory_lifecycle` uses Letta's `messages.compact` to distil patterns every 30 cycles. No native equivalent — replace with an explicit summarise LLM call using `DISTILL_PROMPT`, or a different rolling-window strategy? Validate `## Pattern N` / `cyc_` citation compliance per provider (esp. Haiku). (orch, memory)
- Where does `self_model` live between rotations? Today only in Letta + a `/tmp` snapshot. Add an `observer.db` table or per-agent file. (memory, validators, observer)

### C. Shared blocks → prompt injection; context limits
- Confirm the per-cycle blocks (`world_state`, `cycle_phase`, `*_r0_output`, `recent_outcomes`) can be **dropped entirely** and delivered as prompt content. R1 already pastes peer outputs explicitly, so the `*_r0_output` block writes (council.py:1231/1245) look droppable — confirm. (council, memory, utilities)
- Is the 15000-char `world_state` ceiling still right when passed as a message string? Re-evaluate against native context windows (Haiku-4-5 200k, GPT-4o 128k, Gemini 1M). (validators)
- Do persona files hard-code references to the `cycle_phase` block by name that must be reworded? (orch)

### D. Config & drift detection (no agent to `retrieve()`)
- `config_validator` reads live config via `client.agents.retrieve().model_settings`. Post-migration there's no agent to retrieve. New source of truth for 'live config': (a) none (inject fresh from `AGENT_CONFIG`), (b) local snapshot at last run, or (c) native assistant-config APIs (OpenAI Assistants has one; Anthropic Messages has none)? (validators)
- `AGENT_SPECS` model handles are Letta-shaped (`anthropic/claude-haiku-4-5`). Update to native handle strings, or add a mapping table? Is `_model_settings_diff` still right, or per-provider validators? (validators)
- What keeps `agent_registry.model` in sync with the native platform so the AGENT HEALTH chip isn't stale? (dashboard)

### E. Error / telemetry scaffolds that don't port
- `sweep_letta_steps_for_failures` (raw HTTP to `api.letta.com/v1/runs`) scans for credit/provider-error stop_reasons. No native equivalent — replace with provider SDK exception types + `response.usage`, or drop and rely on the `_alert_exception` path? (orch, utilities; `config.py:LETTA_STEPS_SWEEP_INTERVAL_MIN`)
- `client.steps.retrieve` (token usage + error classification) → native `response.usage`; per-provider `stop_reason` strings differ and need a mapping for error classification. (council)
- The `SAFE_DEFAULTS` degradation fingerprint (`conviction==0.0 AND crux LIKE '(no response)%'`) encodes a Letta *transport* failure. Native failures surface as exceptions — rework `_check_council_degradation` to the native error shape. Downstream detection reads `debate_records` (SQLite), not the block, so the block write is only peer-visibility. (orch, council)
- `magi_alerts.step_id` dedup key → native run/request ID. (observer+database)

### F. Provider routing & cost model
- Agreed mapping from logical agent → provider for `insert_alert`'s `provider_name`/`provider_category` and the rebuild (e.g. casper→google, melchior→openai, balthasar→anthropic)? (observer+database)
- `costs.py:estimate_cost` strips a `provider/` prefix; will `agent_registry` store bare or prefixed model names natively? (utilities)
- Re-evaluate `WAKE_MIN_INTERVAL_MIN` / `MAGI_HOURS_EST` scheduling constants against native per-token pricing (the $20 Letta-plan cost model no longer applies). (orch)

### G. Dashboard LETTA AGENTS panel
- `_fetch_letta_agent_census` (`c.agents.list`) + the eval-name regex are Letta-specific. Retire the panel, or repurpose as a generic 'agents alive' health check? (dashboard)

### H. Dead/uncertain code to verify before porting
- **`should_run_r1` vs. unconditional R1.** It's called as a gate at `orchestrator.py:1877`, but a comment at `run_round_1` (council.py:1472) reportedly says R1 fires unconditionally; CLAUDE.md says R1 is novelty-gated. Confirm whether the `run_round_1` call at `orchestrator.py:1880` is actually guarded by `fire_r1` — if not, `should_run_r1` is dead code and the docs are wrong. (council)
- `emit_human_alert` is imported by `orchestrator.py:59` but appears not invoked in `run_cycle` — confirm live or dead. (orch)
- `wake_guard_sim.py` imports `scheduler` at module level (initialises the live `GridEngine` on import). Confirm it stays valid post-migration or document it as moot. (utilities)
- `extract_test_cases.py` reads `magi_decisions` — confirm it's a one-off, not in any live path. (utilities)

### I. Schema continuity in the rebuild
- Will the native council keep dual-writing `magi_decisions` (legacy consumers: `learning.py`, `extract_test_cases.py`, dashboard) or should those switch to `debate_records`? (utilities)
- Confirm `debate_records` schema (the `*_r0_position/conviction`, `hard_rule_overrides`, `debate_triggered` columns that readiness gates L3/L7/L8/L9 depend on) is unchanged in the rebuild. (utilities)
- Letta-free modules to confirm need no change: `notify.py`, `portfolio.py`, `spacing_evaluator.py`, `market_knowledge.py`, `guardrails.py`, `gate.py`/`gate_monitor.py`. (utilities)

---

## 8. Audit method, coverage, and known limitations

- **Fan-out:** 7 file groups (orchestrator+scheduler, council, memory_lifecycle+provisioning, dashboard, observer+database, validators, utilities). Enumerators and reviewers ran as read-only `Explore` agents on Sonnet; the adversarial sweep and this synthesis ran on Opus.
- **Review layer earned its keep:** every group returned `agree=False`. Reviewers added 19 confirmations/additions and corrected 4 line-number/category issues — all reconciled into §2 (line fixes for `config.py` ×2 and `readiness.py` ×1; `provision_agents.py:144` reclassified `AGENT_LIST`→`MODEL_CONFIG` because `client.models.list()` is a model-catalog query). `agents.create`/`agents.retrieve` were **kept** as `AGENT_LIST` per the audit taxonomy (category 3 explicitly covers list/retrieve/delete/create).
- **Adversarial sweep result:** grepped the whole in-scope tree for `letta`, `Letta(`, `agent_id`, `block_id`, `messages.create`, `messages.compact`, `blocks.`, `agents.`, `AGENT_CONFIG`, `model_settings`, `self_model`, `world_state`, `r0_output`. It reported exactly **1 candidate miss — `memory_lifecycle.py:341` — which was already enumerated** by the memory group; it surfaced only because the dedup key compared an absolute path against relative paths. **Net genuinely-missed touch-points: 0.** The sweep's notes individually accounted for every other grep hit as non-touch-point (Jinja text, SQLite-only `agent_registry` CRUD, prompt-string construction, comments, config literals).
- **Known limitations (honest):**
  1. No exhaustive field-by-field `FIELDS` ↔ `build_world_state()` static diff was produced; the audit relied on the runtime (`alert_on_runtime_drift`) and provisioning (`validate_schema`) validators as the enforcement point. Small residual task if belt-and-suspenders is wanted.
  2. The `should_run_r1`/unconditional-R1 control-flow question (§7-H) needs a human to read the `orchestrator.py:1877–1900` block — it was correctly left as a design question rather than guessed.
  3. `evals/` was excluded by direction; the `factory_base.py` model divergence is noted in §0 but not enumerated.
- **Discipline:** read-only throughout. No source file was modified; no service was started; no Letta/Kraken/LLM API was called. The only write is this file.


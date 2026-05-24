# RUNBOOK — BYOK Failover for MAGI Council Agents

Manual procedure to swap one of the three MAGI council agents (Casper /
Melchior / Balthasar) from its base-routed model handle onto its BYOK
(Bring-Your-Own-Key) provider, and to revert when the base route recovers.

BYOK is the contingency layer. Production stays on base-routed handles.
Use this runbook only when the conditions in **When to use** are met.

---

## When to use this runbook

Use it when **all of these** are true:

1. A `credit_exhausted` row appears in `magi_alerts` for exactly **one** of
   `casper` / `melchior` / `balthasar`. Check:
   ```bash
   sqlite3 -header -column /root/xrp_grid/observer.db \
     "SELECT id, timestamp, severity, category, agent_id, message
      FROM magi_alerts
      WHERE category='credit_exhausted'
      ORDER BY id DESC LIMIT 5;"
   ```
2. The base route has not recovered in **≥30 minutes** (i.e. the same
   agent is still hitting 402s on each cycle, and the other two agents
   are healthy).
3. The operator's **Letta account credits are non-zero** (BYOK routes
   bypass Letta credits but still need an active account). Confirm in
   the Letta Cloud billing UI before swapping.

## When NOT to use this runbook

- **Transient error** (single 402 followed by recovery in <30 min). Wait
  it out; do not swap.
- **All three agents are 402ing simultaneously.** That's a Letta-wide
  outage; BYOK calls route through the same Letta API surface and will
  also fail. Apply HALT and wait for Letta to recover.
- **Letta account is fully de-credited / suspended.** BYOK uses Letta
  infrastructure even if it doesn't consume Letta credits — if the
  account is locked, BYOK won't help.
- **Non-credit error** (e.g. provider 5xx, rate limit on the underlying
  provider). The fix is on the upstream provider; BYOK uses the same
  upstream providers via a different key, so it may or may not help.
  Investigate root cause first.

---

## Agent → BYOK handle mapping

Verified handles (from `/tmp/byok_models.json`, cross-checked via
`c.models.list()` 2026-05-20):

| agent_id   | base handle                          | BYOK handle                          | provider |
|------------|--------------------------------------|--------------------------------------|----------|
| casper     | `google_ai/gemini-3-flash-preview`   | `GEMMNY/gemini-3-flash-preview`      | Google   |
| melchior   | `openai/gpt-4o`                      | `GEEP/gpt-4o`                        | OpenAI   |
| balthasar  | `anthropic/claude-haiku-4-5`         | `BATHY/claude-haiku-4-5-20251001`    | Anthropic|

Notes:
- BATHY/BYOK pins to a dated Anthropic model id (`claude-haiku-4-5-20251001`).
  Base Anthropic handle is the alias `claude-haiku-4-5`. Same family.
- GEEP/`gpt-4o` resolves to OpenAI's currently-pointed `gpt-4o`. Dated
  variants are also available (`gpt-4o-2024-11-20`, etc.) if needed.
- GEMMNY/`gemini-3-flash-preview` mirrors the base handle exactly.

---

## Pre-flight checks

> **CRITICAL CONVENTION.** Any `c.agents.update()` call that passes
> `model=` MUST also pass `model_settings=` to prevent the Letta server
> from resetting per-agent knobs (temperature, thinking budget,
> reasoning effort) to provider defaults. This applies to **swap and
> revert paths equally** — the revert path is not exempt. The 2026-05-20
> BYOK verification caused a 17-hour Balthasar config drift (temp 0.3
> → 1.0, thinking budget 2048 → 1024, effort medium → None) because
> the revert step shipped `model=` without `model_settings=`. The swap
> and revert commands below now include `model_settings=AGENT_CONFIG[agent_key]`
> imported from `magi.provision_agents`.

```bash
cd /root/xrp_grid
source venv/bin/activate
```

1. **Identify the failing agent.** Pick `casper`, `melchior`, or
   `balthasar` based on which is in the credit_exhausted alert above.

2. **Pull the live `letta_agent_id` from `agent_registry`** (do not
   hardcode — Letta UUIDs can change on re-provision):
   ```bash
   AGENT_KEY=balthasar   # or casper, melchior
   AGENT_ID=$(sqlite3 /root/xrp_grid/observer.db \
     "SELECT letta_agent_id FROM agent_registry WHERE agent_id='${AGENT_KEY}';")
   echo "agent_id=${AGENT_ID}"
   ```

3. **Set BASE and BYOK handles** (from the table above):
   ```bash
   # Balthasar example
   BASE_HANDLE="anthropic/claude-haiku-4-5"
   BYOK_HANDLE="BATHY/claude-haiku-4-5-20251001"
   # Casper:   BASE=google_ai/gemini-3-flash-preview   BYOK=GEMMNY/gemini-3-flash-preview
   # Melchior: BASE=openai/gpt-4o                      BYOK=GEEP/gpt-4o
   ```

4. **(Optional, extra-cautious) Apply HALT before swap.** If you want to
   prevent cycles from firing during the swap window, set the HALT file:
   ```bash
   touch /root/xrp_grid/HALT
   ```
   The trade-off: while HALTed, the bot does not place orders. The swap
   takes <30s and the model change is applied atomically on the next
   cycle, so swapping **hot** (no HALT) is acceptable — at worst, the
   in-flight cycle that started before the swap may still 402 once more.
   **Default: swap hot.** Apply HALT only if multiple consecutive
   cycles have been failing and you want a clean restart.

5. **Confirm BYOK provider credentials are valid** (one-line test
   against the BYOK route via a throwaway agent — skip if you've used
   BYOK recently and have no reason to suspect a key issue):
   ```bash
   python -c "
   import os
   from letta_client import Letta
   from dotenv import load_dotenv
   load_dotenv()
   c = Letta(api_key=os.environ['LETTA_API_KEY'])
   # Just list models filtered by the BYOK provider — succeeds if key is live.
   models = c.models.list()
   handles = [m.handle for m in models if m.handle.startswith('${BYOK_HANDLE%%/*}/')]
   print(f'BYOK provider ${BYOK_HANDLE%%/*}: {len(handles)} models visible')
   assert any(h == '${BYOK_HANDLE}' for h in handles), 'target BYOK handle not visible'
   print('credential check passed')
   "
   ```

---

## Swap procedure (parameterized — works for any agent)

With `AGENT_KEY`, `AGENT_ID`, `BASE_HANDLE`, `BYOK_HANDLE` set as above:

1. **Snapshot current Letta state.** Filename pattern:
   `/tmp/<agent_key>_pre_byok_swap_<YYYYMMDD>.json`.
   ```bash
   SNAP_PATH="/tmp/${AGENT_KEY}_pre_byok_swap_$(date -u +%Y%m%d).json"
   python -c "
   import os, json
   from letta_client import Letta
   from dotenv import load_dotenv
   load_dotenv()
   c = Letta(api_key=os.environ['LETTA_API_KEY'])
   agent = c.agents.retrieve('${AGENT_ID}')
   with open('${SNAP_PATH}', 'w') as f:
       json.dump({
           'agent_key': '${AGENT_KEY}',
           'agent_id': '${AGENT_ID}',
           'model': agent.model,
           'llm_config': agent.llm_config.model_dump() if hasattr(agent, 'llm_config') else None,
       }, f, indent=2, default=str)
   print('snapshot saved to ${SNAP_PATH}, current model:', agent.model)
   "
   ```

2. **Swap to BYOK handle.** `model_settings=` is mandatory — see CRITICAL
   CONVENTION at the top of Pre-flight.
   ```bash
   python -c "
   import os
   from letta_client import Letta
   from dotenv import load_dotenv
   from magi.provision_agents import AGENT_CONFIG
   load_dotenv()
   c = Letta(api_key=os.environ['LETTA_API_KEY'])
   updated = c.agents.update(
       agent_id='${AGENT_ID}',
       model='${BYOK_HANDLE}',
       model_settings=AGENT_CONFIG['${AGENT_KEY}'],
   )
   assert updated.model == '${BYOK_HANDLE}', f'unexpected post-swap model: {updated.model}'
   print('swap OK, model now:', updated.model)
   "
   ```

3. **If HALT was applied, lift it now:**
   ```bash
   rm -f /root/xrp_grid/HALT
   ```

---

## Verification (post-swap)

1. **Trigger one cycle** and watch for the response:
   ```bash
   curl -s -X POST http://127.0.0.1:5001/internal/trigger_magi --max-time 180
   ```

2. **Check the latest `debate_records` row** for the swapped agent.
   Confirm `<agent>_r0_conviction > 0` and `<agent>_r0_crux` is not
   `(no response)` or empty:
   ```bash
   sqlite3 -header -column /root/xrp_grid/observer.db "
     SELECT cycle_id, timestamp,
            ${AGENT_KEY}_r0_position    AS pos,
            ${AGENT_KEY}_r0_conviction  AS conv,
            ${AGENT_KEY}_r0_crux        AS crux
     FROM debate_records ORDER BY id DESC LIMIT 1;"
   ```
   Healthy response: `conv > 0`, `pos` is one of the agent's allowed
   positions (CLEAR/PAUSE/HOLD etc. depending on agent), `crux` is a
   real sentence.

3. **Check for new alerts in the last 15 minutes.** Note the `replace`
   on `timestamp` — string comparison without it picks up older rows
   because the column uses ISO 'T' separator:
   ```bash
   sqlite3 -header -column /root/xrp_grid/observer.db "
     SELECT id, timestamp, severity, category, agent_id, provider_name, message
     FROM magi_alerts
     WHERE datetime(replace(timestamp,'T',' ')) > datetime('now', '-15 minutes')
     ORDER BY id DESC;"
   ```
   Expect: no new `credit_exhausted` rows for the swapped agent.

If verification fails (still 402, or empty response), **revert immediately**
using the next section, and escalate.

---

## Revert procedure

When the base route has recovered (Letta credits topped up, provider
incident closed), revert in the reverse direction.

Set the same variables as for the swap (`AGENT_KEY`, `AGENT_ID`,
`BASE_HANDLE`), then:

```bash
python -c "
import os
from letta_client import Letta
from dotenv import load_dotenv
from magi.provision_agents import AGENT_CONFIG
load_dotenv()
c = Letta(api_key=os.environ['LETTA_API_KEY'])
reverted = c.agents.update(
    agent_id='${AGENT_ID}',
    model='${BASE_HANDLE}',
    model_settings=AGENT_CONFIG['${AGENT_KEY}'],
)
assert reverted.model == '${BASE_HANDLE}', f'unexpected post-revert model: {reverted.model}'
print('revert OK, model now:', reverted.model)
"
```

Verify with the same two-query check as the swap verification — trigger
one cycle and confirm the agent's R0 returns a real response and no new
alerts fire.

---

## Known divergence (BYOK vs base)

- **`parallel_tool_calls`** is `False` on all three BYOK handles
  (BATHY/GEEP/GEMMNY). Base-route handles are **server-forced to True**
  by Letta regardless of submitted config. Not expected to matter for
  MAGI's structured-response usage (each council agent does one
  `send_message` per round), but if tool orchestration behaves oddly
  post-swap (e.g. an agent suddenly stalls on multi-tool sequences),
  this is the first suspect.
- **Anthropic BATHY pins a dated model id** (`claude-haiku-4-5-20251001`)
  vs. the base alias (`claude-haiku-4-5`). If Anthropic ships a newer
  Haiku-4-5 build, base will pick it up automatically; BYOK won't until
  the dated id is updated in this runbook.
- **OpenAI GEEP/`gpt-4o`** uses OpenAI's current `gpt-4o` pointer. Same
  family as base `openai/gpt-4o`, but the pointer may move
  independently if OpenAI rotates `gpt-4o` to a new version.
- **Persona / self_model / memory blocks are unaffected** by a model
  swap. The agent keeps its memory, conversation history, and persona;
  only the upstream LLM changes.

---

## Snapshot retention

- All pre-swap snapshots are written to `/tmp/` with the
  `<agent_key>_pre_byok_swap_<YYYYMMDD>.json` pattern.
- `/tmp/` is not persistent across reboots — if you swap and don't get
  to review the snapshot before reboot, it's gone. That's acceptable for
  this runbook: the only thing in the snapshot is the original `model`
  handle and `llm_config`, both of which are also available from
  `agent_registry` (model) and Letta's current state.
- **Cleanup is the operator's job.** After the incident is resolved and
  the base route has been stable for ≥24h, delete the snapshot:
  ```bash
  rm /tmp/${AGENT_KEY}_pre_byok_swap_<YYYYMMDD>.json
  ```

---

## Quick reference — full balthasar swap (worked example, 2026-05-20)

For copy-paste reference. To use for a different agent, substitute the
three variables at the top.

```bash
cd /root/xrp_grid && source venv/bin/activate

AGENT_KEY=balthasar
AGENT_ID=$(sqlite3 /root/xrp_grid/observer.db \
  "SELECT letta_agent_id FROM agent_registry WHERE agent_id='${AGENT_KEY}';")
BASE_HANDLE="anthropic/claude-haiku-4-5"
BYOK_HANDLE="BATHY/claude-haiku-4-5-20251001"
SNAP_PATH="/tmp/${AGENT_KEY}_pre_byok_swap_$(date -u +%Y%m%d).json"

# snapshot
python -c "
import os, json
from letta_client import Letta
from dotenv import load_dotenv
load_dotenv()
c = Letta(api_key=os.environ['LETTA_API_KEY'])
a = c.agents.retrieve('${AGENT_ID}')
open('${SNAP_PATH}','w').write(json.dumps({'model':a.model,'llm_config':a.llm_config.model_dump()}, indent=2, default=str))
print('snap:', a.model)
"

# swap to BYOK (model_settings= mandatory — see CRITICAL CONVENTION)
python -c "
import os
from letta_client import Letta
from dotenv import load_dotenv
from magi.provision_agents import AGENT_CONFIG
load_dotenv()
c = Letta(api_key=os.environ['LETTA_API_KEY'])
print(c.agents.update(
    agent_id='${AGENT_ID}',
    model='${BYOK_HANDLE}',
    model_settings=AGENT_CONFIG['${AGENT_KEY}'],
).model)
"

# verify
curl -s -X POST http://127.0.0.1:5001/internal/trigger_magi --max-time 180
sqlite3 -header -column /root/xrp_grid/observer.db "
  SELECT cycle_id, timestamp,
         ${AGENT_KEY}_r0_position AS pos,
         ${AGENT_KEY}_r0_conviction AS conv,
         ${AGENT_KEY}_r0_crux AS crux
  FROM debate_records ORDER BY id DESC LIMIT 1;"

# revert when base route recovers (model_settings= mandatory)
python -c "
import os
from letta_client import Letta
from dotenv import load_dotenv
from magi.provision_agents import AGENT_CONFIG
load_dotenv()
c = Letta(api_key=os.environ['LETTA_API_KEY'])
print(c.agents.update(
    agent_id='${AGENT_ID}',
    model='${BASE_HANDLE}',
    model_settings=AGENT_CONFIG['${AGENT_KEY}'],
).model)
"
```

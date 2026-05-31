# Pre-ADK-migration restore point — 2026-05-31

Safety archive taken when the MAGI agent-call layer was migrated from Letta Cloud
to Google ADK. The ONLY file overwritten in place during that migration was
`magi/council.py`; this archive preserves its original (Letta) contents.

## What's here
- `council.py.letta` — the original Letta-based `magi/council.py`, extracted
  byte-for-byte from git HEAD (`git show HEAD:magi/council.py`). 1642 lines;
  contains the Letta client, `client.agents.messages.create`, shared-block /
  thread helpers, the freshness validator, and the Letta step/token accounting.

## What changed in the migration (working tree)
- `magi/council.py` — REWRITTEN: agent-call layer now uses ADK `LlmAgent` +
  `Runner` + `InMemorySessionService`; Letta path removed. Public boundary
  (run_round_0_parallel / run_round_1 / should_run_r1 / resolve_consensus /
  update_world_state / emit_human_alert) and the parsed-vote dict shapes are
  unchanged, so `orchestrator.py` needs no changes.
- `magi/agents/` — NEW: `schemas.py` (RegimeVote/GridVote/RiskVote), and
  `personas/` (casper.md, melchior.md, balthasar.md, loader). All new files; no
  prior versions existed.
- `magi_adk/` scaffold — DISCARDED (its schema/persona content was moved into
  `magi/agents/`).
- Nothing else was modified: orchestrator.py, the engine, scheduler, gate,
  database/SQLite, and droplet services were not touched.

## How to restore the original council.py
Either of these fully reverts the agent-call layer to Letta:

    # Option 1 — from this archive
    cp archive/pre_adk_migration_2026-05-31/council.py.letta magi/council.py

    # Option 2 — from git (HEAD still holds the original; nothing was committed)
    git checkout HEAD -- magi/council.py

After restoring, the ADK additions under `magi/agents/` are inert (nothing imports
them once council.py is the Letta version) and can be left in place or removed.

## Verdict-vocabulary change (same day, after the ADK migration)

A follow-up change made the orchestrator consume Melchior's economic verdict
directly (so NO_PROFITABLE_GRID stands down instead of being flattened to
MAINTAIN), plus a Casper persona alignment pass. Files overwritten in place, each
archived here BEFORE the edit:

- `orchestrator.py.preverdict`   — orchestrator.py before the verdict→grid_action
                                    translation + rule-6 guard + debate-record /
                                    dual-write / icontract-snapshot edits.
- `council.py.adk-preverdict`    — the ADK council.py (verdict-flattening version)
                                    before verdict pass-through.
- `casper.md.preverdict`         — casper.md before the council-framing + float-
                                    conviction alignment.

Restore the verdict change only (keep the ADK migration):

    cp archive/pre_adk_migration_2026-05-31/orchestrator.py.preverdict magi/orchestrator.py
    cp archive/pre_adk_migration_2026-05-31/council.py.adk-preverdict   magi/council.py
    cp archive/pre_adk_migration_2026-05-31/casper.md.preverdict        magi/agents/personas/casper.md

(`council.py.adk-preverdict` flattens verdict→grid_action in council.py and expects
the original orchestrator.py; restore both together. `council.py.letta` remains the
full pre-ADK restore point.)

## Note
No commits were made during the migration or the verdict change. The entire
pre-migration repo state is also recoverable from git HEAD; this archive exists as
an explicit, obvious restore point for the files overwritten in place.

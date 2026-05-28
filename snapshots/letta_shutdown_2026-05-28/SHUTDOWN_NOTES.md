# MAGI Shutdown Notes — Letta Migration Prep

## Shutdown timestamp
- **UTC:**   2026-05-28 18:48:38
- **Local:** 2026-05-28 18:48:38 (droplet is on UTC)
- Scheduler logged clean stop at 18:49:07 UTC ("Scheduler stopped cleanly.").

## Reason
Controlled stop of the running MAGI council ahead of migrating off Letta Cloud
and rebuilding the council on each vendor's native platform (OpenAI / Anthropic /
Google) with native caching and owned state. This was a stop + preserve step.
**No agents were deleted, no infrastructure was torn down, no subscription
was cancelled.**

## Last cycle that ran
- **cyc_1779984050** @ 2026-05-28T16:00:50 UTC, trigger=`scheduled`
  - Casper = RANGING, Melchior = RECENTRE, Balthasar = CLEAR
- Last billed LLM call (token_usage): 2026-05-28T16:02:20 UTC (casper council_r1),
  i.e. the 16:00 cycle fired Round 1.
- Between 16:02 and 18:48 the process stayed up ingesting market data
  (WebSocket OHLC + indicators) but ran no further council cycle (next scheduled
  slot was 20:00 UTC, which never arrived — stopped first).

## Service status (confirmed)
| Service | Active | Enabled |
|---|---|---|
| magi.service | inactive (dead) | disabled |
| magi-dashboard.service | inactive (dead) | disabled |

Both stopped + disabled (won't auto-start on reboot). Self-hosted Letta Docker:
`docker ps -a | grep -i letta` returned nothing — no Letta containers present/running
(decommissioned state confirmed; nothing started or removed).

## Kraken orders cancelled
The system was running **LIVE** (not paper — operator corrected this during shutdown).
3 real open orders existed on the account at shutdown; all were MAGI grid orders
(1.65 XRP each, the fixed ORDER_SIZE_XRP). **All 3 cancelled; 0 remaining (verified
by re-query).**

| txid | side | vol (XRP) | limit price |
|---|---|---|---|
| OQHKVY-BBBZV-ZJ2FCX | sell | 1.65 | 1.34010 |
| OXTOWS-WMPV5-5LVEDQ | buy  | 1.65 | 1.29095 |
| OZKOST-BDX6E-I5AFPB | buy  | 1.65 | 1.30078 |

Raw before/after dumps: `kraken_open_orders_before.json`, `kraken_open_orders_after.json`.
Cancellation was done via the repo's `KrakenExchange.cancel_all_open_orders()`
(direct REST, HMAC-SHA512 auth, CancelOrderBatch) — no third-party wrappers.
Note: cancelling resting limit orders returns the reserved balance to the account;
no realised loss. XRP/USD inventory itself was NOT liquidated (out of scope).

## Snapshot contents (file manifest)

Base dir: `/root/xrp_grid/snapshots/letta_shutdown_2026-05-28/`

### Letta agent state (read-only dumps of raw API responses)
| File | Bytes |
|---|---|
| casper_config.json | 110,603 |
| casper_blocks.json | 43,860 |
| casper_messages.jsonl | 3,777,081 |
| casper_tools.json | 12,569 |
| melchior_config.json | 113,708 |
| melchior_blocks.json | 45,426 |
| melchior_messages.jsonl | 959,408 |
| melchior_tools.json | 12,569 |
| balthasar_config.json | 166,067 |
| balthasar_blocks.json | 70,670 |
| balthasar_messages.jsonl | 3,142,347 |
| balthasar_tools.json | 12,569 |
| letta_snapshot_summary.json | 337 |

Per-agent capture: full config (model + relationships), all **8** memory blocks with
full text, full recall-storage message history, and 3 attached tool defs.
Message counts: casper 825, melchior 466, balthasar 704.
**Forensic note:** balthasar's `self_model` block is preserved at its bloated
**25,069 chars** (limit 5,000) — the cause of the cycle-60 rotation `merge_failed`
and skipped thread reset (see investigation report).

### Databases (copies; integrity_check = ok)
| File | Bytes |
|---|---|
| db/observer.db | 12,914,688 |
| db/magi.db | 0 (empty; copied for completeness) |

observer.db row counts at shutdown: debate_records 231, magi_decisions 435,
token_usage 1001, memory_rotations 9.
**Caveat (per operator):** the `debate_records` table is considered contaminated /
not authoritative — preserved for completeness, but do not treat as a trustworthy
source during the rebuild.

### Config / env / docs / modules (copies)
| File | Bytes |
|---|---|
| config/.env | 2,743 |
| config/config.py | 8,569 |
| config/CLAUDE.md | 31,236 |
| config/00_PROJECT_OVERVIEW.md | 15,047 |
| config/01_CURRENT_STATE.md | 105,958 |
| config/02_NEXT_BUILD_TASKS.md | 50,528 |
| config/03_INSTRUCTIONS_TO_CLAUDE.md | 10,717 |
| config/orchestrator.py | 86,893 |
| config/council.py | 71,431 |
| config/memory_lifecycle.py | 23,995 |
| config/prompts/casper_prompt.txt | 13,088 |
| config/prompts/melchior_prompt.txt | 13,329 |
| config/prompts/balthasar_prompt.txt | 14,939 |

(`prompts/` added beyond the brief's explicit list — the persona source files are
core to any rebuild and were cheap to include. `.env` is unredacted by design:
this snapshot stays on the droplet, it is not exported.)

### Git record
| File | Bytes |
|---|---|
| git_head.txt | 78 |
| git_status.txt | 234 |
| git_diff.txt | 0 (working tree clean) |

Code running at shutdown: **6dc434520b301d2366695c7caa61d28ef8c8501c**
"doc update" (2026-05-28 14:02:45 +0000), branch `main`, clean tree
(only untracked file is this `snapshots/` dir). Nothing was committed, pushed,
or branch-switched.

## Letta Cloud agents — NOT deleted
The three Letta Cloud agents (casper / melchior / balthasar) **still exist on
Letta's servers** with all their state intact. We did not delete, archive, or
modify any agent — we only stopped the runtime that calls them and snapshotted
their state read-only. They can still be reached via the Letta API
(`LETTA_API_KEY`) if needed during the rebuild. The bridge is not burned; we are
simply no longer calling them.

Agent IDs (for reference):
- casper:    agent-66c4f9f8-b2a5-4a50-bbbc-e2498dbf293b (google_ai/gemini-3-flash-preview)
- melchior:  agent-c80fdc4a-ef57-49b5-8c9f-237a46b7a503 (openai/gpt-4o)
- balthasar: agent-7f2f2065-11c5-4114-8a50-c67b4d5a4d3d (anthropic/claude-haiku-4-5)

## REMINDER
**Do NOT cancel the Letta Cloud subscription** until the rebuild is complete AND
these snapshots have been verified usable. The live agent state on Letta's servers
is the fallback if a snapshot turns out to be incomplete.

## Final process check — note on stale processes
`systemctl is-active` = inactive for both services; `is-enabled` = disabled;
nothing listening on :5000. **No MAGI or Letta process is running.**

`ps aux | grep -E '(magi|letta)'` matched two processes, but **neither is MAGI** —
they are orphaned Claude Code shell-snapshot bash loops from **2026-05-07** (PIDs
671668, 677419) left over from prior agent sessions. Each is an `until` loop polling
`observer.db` for a new `magi_decisions` row; they appear stuck (likely a quoting bug
in the old eval string makes the `[ -gt ]` test error every iteration, so they never
exit) and have been sleeping/polling every 2-3s for ~3 weeks. They match the grep only
because their command line contains the `observer.db` path. They are harmless but
wasteful orphans. **Recommend killing PIDs 671668 and 677419** — left running pending
operator confirmation (not killed automatically, since they belong to the Claude Code
tooling, not MAGI).

## Failures during shutdown
None. Every step completed:
- services stopped + disabled ✓
- Letta Docker confirmed down ✓
- 3 live Kraken orders cancelled, 0 remaining ✓
- Letta state snapshotted (3 agents, 0 errors) ✓
- databases copied + integrity ok ✓
- config/env/docs/modules copied ✓
- git state recorded ✓

## Post-shutdown cleanup (2026-05-28 19:59 UTC)

- **Orphaned process kills:** the two stale Claude Code shell-loop PIDs flagged
  above (671668, 677419) were re-verified — both still running (PPID 1, ~21 days
  elapsed = 2026-05-07 start, both the `observer.db`/`magi_decisions` polling loops
  as identified). **Killed cleanly with SIGTERM; both gone, no SIGKILL needed, no
  survivors.** No MAGI or Letta processes were affected.
- **Investigation report written:** the council token-cost investigation report was
  written to `/root/xrp_grid/investigations/council_token_cost_2026-05-28.md`
  (17,717 bytes, 192 lines; heading "# MAGI Council Token-Cost Investigation —
  2026-05-28"). Verified present.
- No change to MAGI services, Letta agents, or the rest of this snapshot.

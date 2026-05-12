# MAGI XRP Grid Bot — Instructions to Claude

**Last updated:** 2026-05-07

These instructions capture hard-won operating patterns for this codebase. Read before starting any diagnostic or development work.

---

## Read first, every session

1. `00_PROJECT_OVERVIEW.md` — what the system is
2. `01_CURRENT_STATE.md` — where it is, what's verified
3. `02_NEXT_BUILD_TASKS.md` — what to do next
4. The connected GitHub repo (aye5788/xrp_grid) — actual current code
5. The most recent prior session if context relevant

Do not start working until you've read these. If the operator opens with a task, still read these first — most "questions" are actually answered in the docs and you'll waste a turn re-deriving it.

Operational note on CDN caching: raw.githubusercontent.com (Fastly) caches files for up to 5 minutes per URL, ignoring query strings. After running magi-sync, wait 5+ minutes before opening a new Claude session — otherwise the session may read the pre-push version. Cache-busting query strings (?bust=...) do NOT work; Fastly ignores them. If a session reads stale content, the only reliable fixes are: (a) wait for the 5-minute TTL to expire and start a fresh chat, or (b) paste the current doc content directly into the chat from the droplet (cat /root/xrp_grid/<file>).

---

## Workflow patterns to use

### Restart procedure — when code changes are deployed

The system runs two systemd services: `magi.service` (scheduler) and `magi-dashboard.service` (Flask dashboard / manual triggers). Both load Python modules independently at process start. Any code change in shared modules — `magi/*.py`, `database.py`, `config.py`, `grid/engine.py`, `guardrails.py`, etc. — must restart **both** services.

**Standard command after any code change:**
```
systemctl restart magi.service magi-dashboard.service
```

Restarting only `magi.service` leaves the dashboard process serving stale code, producing inconsistent behavior — scheduled cycles see the new code, manual triggers see the old. This pattern has produced multiple wasted diagnostic cycles. Always restart both.

If symptoms after a code change are inconsistent between scheduled and manual triggers, the dashboard process is the first thing to check. `ps aux | grep dashboard` will show its start time; if it's older than the last code change, restart it.

### Diagnosis before fixes

Before editing any code in response to a misbehaving agent, capture the literal payload the agent is receiving. Add a temporary file-write log immediately before the API call, restart both services, trigger one cycle, and read the file. This takes one extra step but eliminates an entire class of false hypotheses (wrong variable name, wrong dict, correct code never loaded, etc.).

### Verify the fix was actually loaded

After restarting services, confirm the new code is live before running cycles. Two ways:
1. Check process start time: `systemctl status magi.service magi-dashboard.service` — both should show a timestamp after the code change.
2. Check md5 of the edited file before and after, then compare against what you expect.

---

## Forbidden moves

- **Assume `systemctl restart magi.service` reloads all relevant code.** It restarts the scheduler only; the dashboard service is separate. Always restart both after any code change.
- **Edit code in response to a diagnostic result before understanding why the result occurred.** If the first fix didn't work, do not try a second variant of the same fix. Stop, re-read the failure, and ask what it's actually telling you.
- **Commit or push changes.** The operator runs `magi-sync` manually. Never commit or push from within a session.
- **Edit Python code files during documentation-only tasks.** If the task says documentation only, that means documentation only.
- **ALWAYS append to CHANGELOG.md after every significant change.**
  Format: [DATE] [FILE(S)] — one-line description of what changed
  and why. Do this as part of the same commit as the code change.
  Significant = any change to trading logic, agent prompts, database
  schema, or system architecture. Does not apply to typo fixes or
  comment-only changes.

---

## What "bad" looks like — recognize when you're sliding into it

- **Applying the same shape of fix multiple times.** If a fix didn't resolve the symptom, making a more refined version of the same fix is almost certainly wrong. The symptom is telling you the hypothesis is wrong, not that the fix was imprecise.
- **Treating contradictions in diagnostic output as obstacles to push past instead of as the actual signal.** When a test produces results that contradict the hypothesis, the contradiction IS the data. Stop, reread the result, ask what it's telling you about the underlying system. Don't reach for a more clever fix on the same hypothesis. Especially when "more clever fix" is the third or fourth attempt at the same shape of solution.
- **Declaring a fix successful based on a single cycle.** Model stochasticity means one clean cycle is not confirmation. Run at least 3–5 cycles before reporting a confabulation bug resolved.
- **Conflating stochastic model behavior with a deterministic code bug.** Low conviction or hedged language from an agent is normal. "inventory_skew is NULL" or "data is missing" when the value is clearly present in the DB is a code bug. Keep these categories distinct.
- **Silent restarts.** If a restart is part of the diagnostic procedure, explicitly verify the new PID and start time before running the next test. Process identity is observable; don't assume.

---

## Agent behavior reference

### Melchior (gpt-4o — grid microstructure)
- Returns `action` ∈ {MAINTAIN, RECENTRE, TIGHTEN, WIDEN}, `conviction` ∈ {low, medium, high}
- Clean output: reasoning focuses on `vol_regime`, autocorrelation signals, and `vwap_dev_pct`
- Confabulation signal: reasoning says "inventory_skew data is missing" or "NULL" when `inventory_skew` is populated in the DB — means the inventory dict was not passed to `build_context`
- `conviction = low` in every cycle is also a red flag — healthy market conditions produce `medium` most of the time

### Balthasar (claude-sonnet-4-6 — risk guardian)
- Returns `action` ∈ {CLEAR, PAUSE_LONGS, PAUSE_SHORTS, HALT}
- Spurious PAUSE_LONGS under normal conditions = allocation math denominator wrong (see 2026-05-06 fix)
- Buffer-floor rule: PAUSE_LONGS when `usd_held < $10`, PAUSE_SHORTS when `xrp_value_usd < $10`

### Casper (gemini-2.5-flash — market regime)
- Returns `regime` ∈ {RANGING, TRENDING, UNCERTAIN}
- When TRENDING: Melchior's grid action is overridden to MAINTAIN regardless

---

## DB query patterns

```sql
-- Latest decision (all agents)
SELECT timestamp, trigger, melchior_action, melchior_conviction, melchior_reasoning,
       balthasar_action, casper_regime
FROM magi_decisions ORDER BY timestamp DESC LIMIT 1;

-- Recent manual cycles
SELECT timestamp, melchior_action, melchior_conviction, melchior_reasoning
FROM magi_decisions WHERE trigger='manual' ORDER BY timestamp DESC LIMIT 5;

-- Current inventory
SELECT * FROM inventory ORDER BY timestamp DESC LIMIT 1;

-- Current indicators
SELECT * FROM indicators WHERE timeframe='1h' ORDER BY timestamp DESC LIMIT 1;
```

Always use `ORDER BY timestamp DESC` — the timestamp column uses Python ISO format (`YYYY-MM-DDTHH:MM:SS.ffffff`) throughout.

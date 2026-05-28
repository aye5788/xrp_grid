"""
memory_lifecycle.py — distil Letta thread history into self_model on a
30-cycle cadence, then reset the thread for the next window.

Cadence is driven by scheduler.py persisting `rotation_cycle_counter`
to the `system_state` table; `maybe_rotate(n)` fires when
`n % ROTATION_CADENCE == 0`.

Safety invariants (all enforced — none negotiable):
  - Snapshot self_model to /tmp before any write; abort that agent on
    snapshot failure (status='snapshot_failed').
  - Validate the compaction output strictly. Min validity: at least one
    `## Pattern <N>` heading AND at least one `cyc_\\d+` reference.
    On failure: skip merge AND reset; record status='validation_failed'.
    No silent damage.
  - Renumber incoming patterns from existing_max_N + 1 before merge.
  - Evict oldest `## Pattern N` blocks from the existing self_model when
    the merged text would exceed SELF_MODEL_CHAR_CAP. If eviction
    cannot fit the merge under cap, refuse merge (status='merge_failed').
  - Merge via `client.blocks.update()` — server-side write only. Never
    ask the agent to write its own block via tool calls.
  - `messages.reset()` only fires after a successful merge. If merge
    fails, skip the reset — thread integrity is more valuable than
    token savings.
  - Per-agent independence: a failure on one agent must not affect the
    others; each is wrapped in its own try/except.
  - All exceptions caught and logged — `maybe_rotate` must never crash
    the scheduler or the trading loop.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
load_dotenv(_REPO_ROOT / '.env')

from letta_client import Letta

import config
import database as db


log = logging.getLogger(__name__)


# Validator regex: matches `## Pattern <N>` at line start, no colon
# required (agents in practice emit either `## Pattern 6: title` or
# `## Pattern 6 — title`; both are acceptable).
_PATTERN_HEADING_RE = re.compile(r"^##\s*Pattern\s+(\d+)", re.MULTILINE)
_CYCLE_ID_RE = re.compile(r"\bcyc_\d+\b")

# Pre-rotation degradation gate. Counted over the last 30 R0 rows per
# agent in debate_records; safe-default fingerprint matches
# magi/council.py:SAFE_DEFAULTS (conviction=0 AND crux LIKE '(no response)%').
# If ≥40% of the window is degraded, skip rotation for that agent — distilling
# safe-default cycles into self_model would either pollute the block with
# "I had no response" patterns or fail validation (no real cycle id
# substance to cite).
_ROTATION_DEGRADATION_WINDOW = 30
_ROTATION_DEGRADATION_THRESHOLD = 12  # 12/30 = 0.40


def _count_degraded_in_window(agent_id: str) -> int:
    """Count safe-default R0 rows in the last 30 cycles for one agent.
    Returns 0 on any DB error (fail-open — don't skip rotation because
    of a transient query failure)."""
    try:
        conn = db.get_conn()
        rows = conn.execute(
            f"SELECT {agent_id}_r0_conviction AS conv, "
            f"       {agent_id}_r0_crux       AS crux "
            f"FROM debate_records ORDER BY id DESC LIMIT {_ROTATION_DEGRADATION_WINDOW}"
        ).fetchall()
        conn.close()
    except Exception as e:
        log.warning(
            "[%s] degradation-window query failed: %s — proceeding with rotation",
            agent_id, e,
        )
        return 0
    count = 0
    for r in rows:
        conv = r['conv']
        crux = r['crux'] or ''
        conv_zero = (conv is None) or (abs(float(conv)) < 1e-9)
        if conv_zero and crux.startswith('(no response)'):
            count += 1
    return count

# Logical → Letta lookup goes through agent_registry; this tuple just
# fixes the order rotations are attempted in.
_AGENTS = ("casper", "melchior", "balthasar")

_SNAPSHOT_DIR = Path("/tmp")


# DISTILL_PROMPT — designed to be parseable by a regex validator (no LLM
# needed to check the output) and to work across Gemini, GPT-4o, and
# Haiku. Each pattern must cite a cycle id (cyc_XXXXXXXXXX) and at
# least one world_state field name. Cap is enforced both in the prompt
# (max 2) and in code (MAX_NEW_PATTERNS).
DISTILL_PROMPT = (
    "You are reviewing your own recent decision history. Your task is to "
    "distil up to 2 new behavioural patterns worth adding to your "
    "self_model. Each pattern must reflect what you actually observed "
    "in the cycles below — do not invent.\n"
    "\n"
    "Requirements for every pattern:\n"
    "- Body must be no more than ~300 characters.\n"
    "- Body must cite at least one specific cycle id in the form "
    "cyc_XXXXXXXXXX.\n"
    "- Body must cite at least one specific world_state field name "
    "(examples: buy_count, sell_count, allocation_skew, "
    "hours_since_last_fill, roc_6h, pause_longs, grid_alive).\n"
    "- Heading must be exactly: ## Pattern <N>: <short title>\n"
    "- Number incoming patterns starting at 1; the server will "
    "renumber them.\n"
    "\n"
    "Output format (no exceptions):\n"
    "\n"
    "## Pattern 1: <short title>\n"
    "<2 to 3 sentence body citing the required fields and a cycle id>\n"
    "\n"
    "## Pattern 2: <short title>\n"
    "<2 to 3 sentence body citing the required fields and a cycle id>\n"
    "\n"
    "Output ONLY the pattern blocks. No preamble. No closing summary. "
    "No sign-off. No markdown fences. No commentary outside the blocks. "
    "If you cannot identify a pattern meeting these requirements, "
    "output nothing."
)


# --- Letta client ---
# Mirrors magi/council.py exactly: api_key only, Letta Cloud default
# base_url. Module-level so scheduler imports it once.
_api_key = os.environ.get("LETTA_API_KEY")
if not _api_key:
    raise RuntimeError(
        "LETTA_API_KEY must be set in /root/xrp_grid/.env "
        "(Letta Cloud API key from app.letta.com → Settings → API Keys)"
    )
_client = Letta(api_key=_api_key)


# --- Helpers ---

def _find_pattern_blocks(text: str) -> list[tuple[int, int, int]]:
    """Return [(pattern_number, start_offset, end_offset), ...] for each
    `## Pattern N` block in `text`. `end_offset` is the start of the
    next heading or end-of-text. Ordered by start offset."""
    if not text:
        return []
    matches = list(_PATTERN_HEADING_RE.finditer(text))
    blocks = []
    for i, m in enumerate(matches):
        n = int(m.group(1))
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append((n, start, end))
    return blocks


def _validate_patterns(text: str) -> bool:
    """Strict per spec — malformed output records validation_failed and
    skips merge+reset for that agent this rotation. No softening."""
    if not text or not text.strip():
        return False
    if not _PATTERN_HEADING_RE.search(text):
        return False
    if not _CYCLE_ID_RE.search(text):
        return False
    return True


def _renumber_patterns(existing_self_model: str, new_patterns: str) -> str:
    """Renumber incoming patterns to start from existing_max_N + 1. No-op
    if numbering is already correct. Also caps incoming to
    MAX_NEW_PATTERNS — any extras the agent emitted past the prompt cap
    are discarded here, not at validation time (validation has already
    passed at this point)."""
    incoming = _find_pattern_blocks(new_patterns)
    if not incoming:
        return new_patterns

    existing = _find_pattern_blocks(existing_self_model)
    max_n = max((b[0] for b in existing), default=0)

    incoming_sorted = sorted(incoming, key=lambda b: b[0])
    capped = incoming_sorted[:config.MAX_NEW_PATTERNS]

    # Fast path: already-correct numbering and within cap → no-op
    expected_numbers = list(range(max_n + 1, max_n + 1 + len(capped)))
    actual_numbers = [b[0] for b in capped]
    if (actual_numbers == expected_numbers
            and len(incoming_sorted) <= config.MAX_NEW_PATTERNS):
        # Still need to strip any pre-heading prefix / post-block junk —
        # re-stitch from blocks to keep output clean
        out = "\n\n".join(
            new_patterns[s:e].rstrip() for _, s, e in capped
        ).strip()
        return out

    out_parts = []
    for new_idx, (_old_n, start, end) in enumerate(capped, start=max_n + 1):
        block_text = new_patterns[start:end]
        # Replace only the first heading line of this block. Preserves
        # whatever separator the agent used after the number (em-dash,
        # colon, etc.).
        block_text_new = re.sub(
            r"^##\s*Pattern\s+\d+",
            f"## Pattern {new_idx}",
            block_text,
            count=1,
        )
        out_parts.append(block_text_new.rstrip())
    return "\n\n".join(out_parts).strip()


def _evict_oldest_if_needed(existing_self_model: str,
                             new_patterns: str) -> str:
    """Drop the lowest-numbered `## Pattern N` block from
    existing_self_model repeatedly until
    `existing + "\\n\\n" + new_patterns` fits under SELF_MODEL_CHAR_CAP.
    Returns the (possibly trimmed) existing text.

    If all pattern blocks are evicted and the residual non-pattern text
    still pushes the merge over the cap, returns whatever is left — the
    caller (`_merge_into_self_model`) detects the over-cap merged text
    and refuses to push it."""
    sep = "\n\n"
    while True:
        candidate_len = (
            len(existing_self_model.rstrip())
            + len(sep)
            + len(new_patterns.strip())
        )
        if candidate_len <= config.SELF_MODEL_CHAR_CAP:
            return existing_self_model
        blocks = sorted(
            _find_pattern_blocks(existing_self_model),
            key=lambda b: b[0],
        )
        if not blocks:
            return existing_self_model
        _, start, end = blocks[0]
        existing_self_model = (
            existing_self_model[:start] + existing_self_model[end:]
        ).strip()


def _snapshot_self_model(agent_id: str, letta_id: str) -> str:
    """Write the current self_model block + agent metadata to
    /tmp/self_model_pre_rotation_<agent>_<YYYYMMDD>.json. Returns the
    path. Raises on any failure — the caller treats that as abort."""
    yyyymmdd = datetime.utcnow().strftime("%Y%m%d")
    path = _SNAPSHOT_DIR / (
        f"self_model_pre_rotation_{agent_id}_{yyyymmdd}.json"
    )
    blocks = list(_client.agents.blocks.list(letta_id))
    sm = next((b for b in blocks if getattr(b, 'label', None) == 'self_model'),
              None)
    if sm is None:
        raise RuntimeError(
            f"no self_model block attached to agent_id={agent_id!r} "
            f"letta_id={letta_id!r}"
        )
    value = getattr(sm, 'value', '') or ''
    payload = {
        'agent_id':            agent_id,
        'letta_agent_id':      letta_id,
        'snapshot_ts':         datetime.utcnow().isoformat(),
        'self_model_block_id': getattr(sm, 'id', None),
        'self_model_value':    value,
        'self_model_chars':    len(value),
        'self_model_limit':    getattr(sm, 'limit', None),
    }
    with open(path, 'w') as fp:
        json.dump(payload, fp, indent=2, default=str)
    return str(path)


def _compact_and_extract(letta_id: str) -> Optional[str]:
    """Run the self-compaction summarisation. Returns the summary text
    on success, None on empty / no-summary."""
    result = _client.agents.messages.compact(
        agent_id=letta_id,
        compaction_settings={
            'mode': 'self_compact_sliding_window',
            'sliding_window_percentage': config.ROTATION_WINDOW_PCT,
            'prompt': DISTILL_PROMPT,
            'clip_chars': 2000,
        },
    )
    summary = getattr(result, 'summary', None)
    if not summary or not summary.strip():
        return None
    return summary


def _merge_into_self_model(agent_id: str, letta_id: str,
                            new_patterns: str) -> bool:
    """Re-read the live self_model, renumber + evict + concat, then push
    via `client.blocks.update()`. Returns True on success, False on any
    failure path (missing block, over-cap after eviction, API error)."""
    try:
        blocks = list(_client.agents.blocks.list(letta_id))
        sm = next(
            (b for b in blocks if getattr(b, 'label', None) == 'self_model'),
            None,
        )
        if sm is None:
            log.warning("[%s] merge: no self_model block found", agent_id)
            return False
        existing = getattr(sm, 'value', '') or ''

        renumbered = _renumber_patterns(existing, new_patterns)
        trimmed = _evict_oldest_if_needed(existing, renumbered)
        merged = (trimmed.rstrip() + "\n\n" + renumbered.strip()).strip()

        if len(merged) > config.SELF_MODEL_CHAR_CAP:
            log.warning(
                "[%s] merge: merged self_model %d chars > cap %d even "
                "after eviction — refusing to push",
                agent_id, len(merged), config.SELF_MODEL_CHAR_CAP,
            )
            return False

        _client.blocks.update(getattr(sm, 'id'), value=merged)
        log.info(
            "[%s] merge: self_model updated %d → %d chars",
            agent_id, len(existing), len(merged),
        )
        return True
    except Exception as e:
        log.exception("[%s] merge raised: %s", agent_id, e)
        return False


def _reset_thread(letta_id: str) -> bool:
    """Reset the agent's message thread. We pass
    add_default_initial_messages=False so the post-rotation thread
    starts clean — the next MAGI cycle will feed in world_state and
    cycle prompts through the existing council.py flow."""
    try:
        _client.agents.messages.reset(
            letta_id,
            add_default_initial_messages=False,
        )
        return True
    except Exception as e:
        log.warning("messages.reset failed for %s: %r", letta_id, e)
        return False


# --- Public API ---

def rotate_agent_memory(agent_id: str) -> dict:
    """One-agent rotation. Returns:
        {status, patterns_added, chars_before, chars_after,
         snapshot_path, error}

    `status` values:
      'success'           — full pipeline completed
      'validation_failed' — compaction output failed strict validator
      'merge_failed'      — merge into self_model was refused or errored
      'snapshot_failed'   — pre-write snapshot could not be saved
      'compact_failed'    — compaction API errored or returned empty
      'skipped'           — agent_registry has no row / no letta_agent_id
      'skipped_degraded'  — ≥40% of last 30 R0 rows were SAFE_DEFAULTS;
                            distilling them would pollute self_model
      'error'             — unexpected exception (top-level catch)
    """
    result: dict = {
        'status':         'unknown',
        'patterns_added': 0,
        'chars_before':   None,
        'chars_after':    None,
        'snapshot_path':  None,
        'error':          None,
        'degraded_count_in_window': 0,
    }

    try:
        row = db.get_agent_registry_row(agent_id)
        if not row or not row.get('letta_agent_id'):
            result['status'] = 'skipped'
            result['error'] = 'no letta_agent_id in agent_registry'
            log.warning("[%s] rotation skipped — %s", agent_id, result['error'])
            return result
        letta_id = row['letta_agent_id']

        # 0. Pre-rotation degradation gate. If too many recent R0s were
        # SAFE_DEFAULTS, skip rotation — there's no real signal to distil and
        # forcing a compact here would either (a) inject "(no response)"-laced
        # patterns into self_model, or (b) fail the strict validator and
        # waste a Letta compaction call. Either way it's the wrong move.
        deg_count = _count_degraded_in_window(agent_id)
        result['degraded_count_in_window'] = deg_count
        if deg_count >= _ROTATION_DEGRADATION_THRESHOLD:
            result['status'] = 'skipped_degraded'
            result['error'] = (
                f"{deg_count}/{_ROTATION_DEGRADATION_WINDOW} recent R0s "
                f"were SAFE_DEFAULTS (>= "
                f"{_ROTATION_DEGRADATION_THRESHOLD} threshold) — refusing "
                f"to distil degraded cycles into self_model"
            )
            log.info(
                "[%s] rotation skipped_degraded — %s",
                agent_id, result['error'],
            )
            try:
                db.insert_alert(
                    severity='warn',
                    category='rotation_skipped_degraded',
                    agent_id=agent_id,
                    message=(
                        f"Memory rotation skipped for {agent_id}: "
                        f"{deg_count}/{_ROTATION_DEGRADATION_WINDOW} of the "
                        f"last R0 rows matched SAFE_DEFAULTS. Self_model and "
                        f"thread untouched; next rotation on the next "
                        f"30-cycle boundary."
                    ),
                )
            except Exception as alert_err:
                log.warning(
                    "[%s] could not insert rotation_skipped_degraded alert: %r",
                    agent_id, alert_err,
                )
            return result

        # 1. Snapshot (abort agent on failure)
        try:
            snap_path = _snapshot_self_model(agent_id, letta_id)
            result['snapshot_path'] = snap_path
            with open(snap_path) as fp:
                result['chars_before'] = json.load(fp).get('self_model_chars')
            log.info(
                "[%s] snapshot ok (%d chars) -> %s",
                agent_id, result['chars_before'] or 0, snap_path,
            )
        except Exception as e:
            result['status'] = 'snapshot_failed'
            result['error'] = f"{type(e).__name__}: {e}"
            log.warning(
                "[%s] snapshot failed: %s — aborting rotation", agent_id, e
            )
            return result

        # 2. Compact + extract
        try:
            new_patterns = _compact_and_extract(letta_id)
        except Exception as e:
            result['status'] = 'compact_failed'
            result['error'] = f"{type(e).__name__}: {e}"
            log.warning("[%s] compact failed: %s", agent_id, e)
            return result
        if not new_patterns:
            result['status'] = 'compact_failed'
            result['error'] = 'compact returned empty summary'
            log.warning("[%s] %s", agent_id, result['error'])
            return result

        # 3. Validate — strict
        if not _validate_patterns(new_patterns):
            result['status'] = 'validation_failed'
            preview = new_patterns[:200].replace('\n', ' ')
            result['error'] = f"validator rejected output: {preview!r}"
            log.warning(
                "[%s] validation_failed — skipping merge and reset. "
                "preview=%r", agent_id, preview,
            )
            return result

        # Count what we'll actually merge (post-cap)
        incoming_count = len(_PATTERN_HEADING_RE.findall(new_patterns))
        patterns_added = min(incoming_count, config.MAX_NEW_PATTERNS)

        # 4. Merge (renumber + evict + push)
        if not _merge_into_self_model(agent_id, letta_id, new_patterns):
            result['status'] = 'merge_failed'
            result['error'] = 'merge into self_model failed (see logs)'
            log.warning(
                "[%s] merge_failed — skipping reset to preserve thread",
                agent_id,
            )
            return result

        # Re-read chars_after from the live block
        try:
            blocks = list(_client.agents.blocks.list(letta_id))
            sm = next(
                (b for b in blocks
                 if getattr(b, 'label', None) == 'self_model'),
                None,
            )
            if sm is not None:
                result['chars_after'] = len(getattr(sm, 'value', '') or '')
        except Exception as e:
            log.warning(
                "[%s] could not re-read self_model post-merge: %r",
                agent_id, e,
            )

        # 5. Reset thread — best-effort. If reset fails the merge still
        # stands and we mark success; next rotation will retry the reset
        # via the natural compaction cycle.
        if not _reset_thread(letta_id):
            log.warning(
                "[%s] thread reset failed AFTER successful merge — "
                "self_model already updated; thread will rotate next cadence",
                agent_id,
            )

        result['status'] = 'success'
        result['patterns_added'] = patterns_added
        log.info(
            "[%s] rotation success — patterns_added=%d chars %s→%s",
            agent_id, patterns_added,
            result['chars_before'], result['chars_after'],
        )
        return result

    except Exception as e:
        # Top-level catch — must never propagate; scheduler depends on this.
        result['status'] = result.get('status') or 'error'
        result['error'] = f"unexpected: {type(e).__name__}: {e}"
        log.exception("[%s] rotate_agent_memory unexpected error", agent_id)
        return result


def maybe_rotate(cycle_number: int) -> None:
    """Called by the scheduler after each successful MAGI cycle. Fires a
    rotation when `cycle_number % ROTATION_CADENCE == 0`. Wraps every
    agent in its own try/except so a failure on one cannot cascade.
    Catches all exceptions at the top level — must never crash the
    scheduler or the trading loop."""
    try:
        if not isinstance(cycle_number, int) or cycle_number <= 0:
            return
        if cycle_number % config.ROTATION_CADENCE != 0:
            return

        log.info(
            "=== MEMORY ROTATION at cycle %d (cadence=%d) ===",
            cycle_number, config.ROTATION_CADENCE,
        )

        for agent_id in _AGENTS:
            try:
                res = rotate_agent_memory(agent_id)
            except Exception as e:
                log.exception(
                    "[%s] rotate_agent_memory raised at top level: %s",
                    agent_id, e,
                )
                res = {
                    'status':         'error',
                    'patterns_added': 0,
                    'chars_before':   None,
                    'chars_after':    None,
                    'snapshot_path':  None,
                    'error':          f"top-level: {type(e).__name__}: {e}",
                }

            # DB record — best-effort, never fatal
            try:
                db.insert_memory_rotation(
                    agent_id       = agent_id,
                    cycle_number   = cycle_number,
                    chars_before   = res.get('chars_before'),
                    chars_after    = res.get('chars_after'),
                    patterns_added = res.get('patterns_added') or 0,
                    status         = res.get('status') or 'error',
                    snapshot_path  = res.get('snapshot_path'),
                    error_detail   = res.get('error'),
                    degraded_count_in_window = res.get('degraded_count_in_window') or 0,
                )
            except Exception as db_err:
                log.error(
                    "[%s] failed to record memory_rotations row: %r",
                    agent_id, db_err,
                )

            log.info(
                "[%s] rotation result status=%s chars=%s→%s "
                "patterns_added=%s",
                agent_id, res.get('status'),
                res.get('chars_before'), res.get('chars_after'),
                res.get('patterns_added'),
            )
    except Exception:
        log.exception("maybe_rotate top-level catch")

"""
council.py — Round 0 / Round 1 debate engine for the MAGI council.

Flow:
  1. orchestrator calls update_world_state(world_state_dict)
  2. orchestrator calls run_round_0_parallel(cycle_id)
       -> all three agents respond in parallel; r0_output blocks updated
  3. orchestrator calls detect_conflict(round_0)
  4. if conflict, orchestrator calls run_round_1(conflict, cycle_id)
       -> only the conflict participants get a Round 1 challenge
  5. orchestrator calls resolve_consensus(round_0, round_1, conflict)

Action vocabularies (from existing magi/prompts/*.txt):
  - casper.position    ("regime")       : RANGING | TRENDING | UNCERTAIN
  - melchior.position  ("grid_action")  : MAINTAIN | RECENTRE | TIGHTEN | WIDEN
  - balthasar.position ("risk_action")  : CLEAR | PAUSE_LONGS | PAUSE_SHORTS | HALT

CONFLICT MATRIX (rules below are positive — absence = no conflict):
  - (TRENDING, TIGHTEN, *)             → casper vs. melchior  : tightening into a trend amplifies directional risk
  - (*, WIDEN, PAUSE_LONGS)            → melchior vs. balthasar: widening grid while pausing longs sends contradictory signals
  - (*, WIDEN, PAUSE_SHORTS)           → melchior vs. balthasar: widening grid while pausing shorts sends contradictory signals
  - (*, *, HALT)  + bal.conv > 0.6     → melchior vs. balthasar: HALT with conviction conflicts with any continued-trading recommendation
  - Explicit non-conflict (documented, not coded): (TRENDING, RECENTRE, *) — RECENTRE is regime-neutral

If multiple rules match, the rule with the highest combined conviction (of the two named agents) wins.

NOTE on Letta SDK: letta-client 1.11.0's constructor accepts api_key=, not
token= — the original spec used token=, which raises TypeError. Using api_key
here.
"""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load /root/xrp_grid/.env so LETTA_* env vars are present at import time
_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / '.env')

from letta_client import Letta

from database import (get_agent_registry_row, get_letta_agent_id,
                      insert_alert, insert_token_usage)
from magi.costs import estimate_cost


log = logging.getLogger(__name__)


# Letta Step.stop_reason → (category, severity) for alerts. Anything not in
# this map is either a normal completion ('end_turn', 'tool_call', etc.) or
# something we don't yet have a category for; the live hook ignores those.
_STOP_REASON_ALERTS = {
    'insufficient_credits':    ('credit_exhausted', 'critical'),
    'llm_api_error':           ('provider_error',   'warn'),
    'invalid_llm_response':    ('provider_error',   'warn'),
    'error':                   ('unknown_failure',  'warn'),
}


def _check_steps_for_alerts(agent_id: str, response, phase: str = "R0") -> None:
    """Walk the assistant messages in `response`, retrieve each step from
    Letta, and emit a magi_alerts row when stop_reason indicates a
    credit/auth/provider failure. Idempotent via step_id dedup in
    database.insert_alert.

    No-op when no step_id is present (e.g. response was a safe-default
    early return) or when stop_reason is a normal completion.
    """
    step_ids = []
    for msg in getattr(response, 'messages', []) or []:
        sid = getattr(msg, 'step_id', None)
        if sid and sid not in step_ids:
            step_ids.append(sid)
    if not step_ids:
        return
    for sid in step_ids:
        try:
            step = client.steps.retrieve(sid)
        except Exception as e:
            log.warning("[%s] could not retrieve step %s: %r",
                        agent_id, sid, e)
            continue
        stop = getattr(step, 'stop_reason', None)
        status = getattr(step, 'status', None)
        # Auth failures often surface as error_data containing 401/403
        # while stop_reason may be 'llm_api_error'. Detect that first.
        err_data_str = str(getattr(step, 'error_data', '') or '').lower()
        if '401' in err_data_str or 'unauthorized' in err_data_str \
                or 'invalid api key' in err_data_str \
                or 'authentication_error' in err_data_str:
            category, severity = 'auth_failed', 'critical'
        elif '429' in err_data_str or 'rate limit' in err_data_str:
            category, severity = 'rate_limited', 'warn'
        elif stop in _STOP_REASON_ALERTS:
            category, severity = _STOP_REASON_ALERTS[stop]
        elif status == 'failed':
            category, severity = 'unknown_failure', 'warn'
        else:
            continue  # normal completion, no alert
        msg_summary = (
            f"[{phase}] stop_reason={stop!s} status={status!s} "
            f"provider={getattr(step,'provider_name','?')}"
            f"/{getattr(step,'provider_category','?')} "
            f"error_data={err_data_str[:200]}"
        )
        try:
            insert_alert(
                severity=severity, category=category, message=msg_summary,
                agent_id=agent_id,
                provider_category=getattr(step, 'provider_category', None),
                provider_name=getattr(step, 'provider_name', None),
                step_id=sid,
            )
            log.warning("[%s] alert recorded: %s (step %s)",
                        agent_id, category, sid)
        except Exception as alert_err:
            log.error("insert_alert failed: %r", alert_err)


def _alert_exception(agent_id: str, exc: BaseException,
                      phase: str = "R0") -> None:
    """Record an alert for an exception raised by the SDK call itself
    (network failure, pre-step error, etc.). Used as a fallback when no
    Step is created because the call failed before producing one.

    Classifies via the SDK's typed status_code where available; otherwise
    'provider_error' / warn.
    """
    status = getattr(exc, 'status_code', None)
    text = (str(exc) or '').lower()
    if status == 401 or 'unauthorized' in text or 'authentication' in text:
        category, severity = 'auth_failed', 'critical'
    elif status == 402 or 'insufficient_credits' in text \
            or 'payment required' in text:
        category, severity = 'credit_exhausted', 'critical'
    elif status == 429 or 'rate limit' in text:
        category, severity = 'rate_limited', 'warn'
    else:
        category, severity = 'provider_error', 'warn'
    try:
        insert_alert(
            severity=severity, category=category,
            message=f"[{phase}] {type(exc).__name__}: {exc}"[:500],
            agent_id=agent_id,
        )
    except Exception as alert_err:
        log.error("insert_alert failed: %r", alert_err)


def _record_token_usage_from_response(agent_id: str, response,
                                       phase: str = "R0") -> None:
    """Walk the step ids in `response`, sum token counts across them, and
    write one token_usage row per LLM call. Model is taken from
    agent_registry (the live source of truth); the per-step model string
    from Letta is used only as a fallback when the registry has no row.

    Best-effort: any exception is logged and swallowed — token accounting
    must never block the council. Skipped when no step_id is present
    (safe-default early returns, etc.).
    """
    step_ids = []
    for msg in getattr(response, 'messages', []) or []:
        sid = getattr(msg, 'step_id', None)
        if sid and sid not in step_ids:
            step_ids.append(sid)
    if not step_ids:
        return

    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    step_model = None
    for sid in step_ids:
        try:
            step = client.steps.retrieve(sid)
        except Exception as e:
            log.warning("[%s] %s: token-usage step retrieve %s failed: %r",
                        agent_id, phase, sid, e)
            continue
        prompt_tokens     += int(getattr(step, 'prompt_tokens', 0) or 0)
        completion_tokens += int(getattr(step, 'completion_tokens', 0) or 0)
        total_tokens      += int(getattr(step, 'total_tokens', 0) or 0)
        # Last non-empty step.model wins as the fallback label
        sm = getattr(step, 'model', None)
        if sm:
            step_model = sm

    if total_tokens == 0 and prompt_tokens == 0 and completion_tokens == 0:
        # Nothing billable to record (all steps came back empty)
        return

    # Authoritative model string lives in agent_registry. Fall back to the
    # step's model only if the registry has no row for this agent.
    model = None
    try:
        reg = get_agent_registry_row(agent_id)
        if reg and reg.get('model'):
            model = reg['model']
    except Exception as e:
        log.warning("[%s] %s: agent_registry lookup failed: %r",
                    agent_id, phase, e)
    if not model:
        model = step_model or 'unknown'

    cost = 0.0
    try:
        cost = estimate_cost(model, prompt_tokens, completion_tokens)
    except Exception as e:
        log.warning("[%s] %s: estimate_cost failed: %r", agent_id, phase, e)

    try:
        insert_token_usage(
            agent_id, model, prompt_tokens, completion_tokens,
            total_tokens, cost, source=f"council_{phase.lower()}",
        )
    except Exception as e:
        log.warning("[%s] %s: insert_token_usage failed: %r",
                    agent_id, phase, e)


# --- Letta client (module-level) ---
# Letta Cloud is the SDK default when only api_key is passed.
# base_url resolves to https://api.letta.com automatically.

_api_key = os.environ.get("LETTA_API_KEY")
if not _api_key:
    raise RuntimeError(
        "LETTA_API_KEY must be set in /root/xrp_grid/.env "
        "(Letta Cloud API key from app.letta.com → Settings → API Keys)"
    )
client = Letta(api_key=_api_key)


# --- Constants ---

VALID_REGIMES      = ("RANGING", "TRENDING", "UNCERTAIN")
VALID_GRID_ACTIONS = ("MAINTAIN", "RECENTRE", "TIGHTEN", "WIDEN")
VALID_RISK_ACTIONS = ("CLEAR", "PAUSE_LONGS", "PAUSE_SHORTS", "HALT")

# Per-agent safe defaults used when the LLM response is unparseable after retry
SAFE_DEFAULTS = {
    "casper":    {"position": "UNCERTAIN", "conviction": 0.0,
                  "key_evidence": [], "crux": "(no response)"},
    "melchior":  {"position": "MAINTAIN",  "conviction": 0.0,
                  "key_evidence": [], "crux": "(no response)"},
    "balthasar": {"position": "CLEAR",     "conviction": 0.0,
                  "key_evidence": [], "crux": "(no response)"},
}

# Risk conservatism order: HIGH index = more conservative
_RISK_CONSERVATISM_ORDER = {
    "CLEAR":         0,
    "PAUSE_LONGS":   1,
    "PAUSE_SHORTS":  1,
    "HALT":          2,
    "MAINTAIN":      0,  # synonym for "no action" if it ever appears
}


# Each rule: (regime, grid, risk, predicate_or_None, agents_in_conflict, reason)
# Star "*" means wildcard match. predicate(round_0, world_state) -> bool may
# add a runtime gate (e.g. conviction > 0.6, or grid-state checks). world_state
# is passed positionally; predicates that don't need it can ignore the arg.
def _buy_count(world_state):
    return int(((world_state or {}).get("open_orders") or {}).get("buy_count") or 0)

def _sell_count(world_state):
    return int(((world_state or {}).get("open_orders") or {}).get("sell_count") or 0)

def _hours_since_fill(world_state):
    v = (world_state or {}).get("hours_since_last_fill")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None

CONFLICT_MATRIX = [
    ("TRENDING", "TIGHTEN", "*", None, ["casper", "melchior"],
     "Tightening grid into a trending regime amplifies directional risk."),

    ("*", "WIDEN", "PAUSE_LONGS", None, ["melchior", "balthasar"],
     "Widening the grid while pausing longs sends contradictory signals on long exposure."),

    ("*", "WIDEN", "PAUSE_SHORTS", None, ["melchior", "balthasar"],
     "Widening the grid while pausing shorts sends contradictory signals on short exposure."),

    ("*", "*", "HALT",
     lambda r0, ws: float(r0.get("balthasar", {}).get("conviction", 0.0)) > 0.6,
     ["melchior", "balthasar"],
     "Balthasar recommends HALT with conviction > 0.6; this conflicts with any "
     "continued-grid recommendation from Melchior."),

    # Grid-state-aware conflicts. These catch cases where the council's R0
    # consensus would leave a degenerate or stuck book. The hard-rule layer
    # also enforces RECENTRE in those cases, but routing through Round 1
    # gives the agents a chance to surface better geometry / risk reasoning
    # before Python overrides them.
    ("*", "MAINTAIN", "*",
     lambda r0, ws: _buy_count(ws) == 0 or _sell_count(ws) == 0,
     ["melchior", "balthasar"],
     "Grid is one-sided (zero orders on one side) — MAINTAIN would leave it "
     "degenerate. Council must justify or revise."),

    ("*", "MAINTAIN", "*",
     lambda r0, ws: (_hours_since_fill(ws) is not None
                     and _hours_since_fill(ws) > 12),
     ["casper", "melchior"],
     "No fills for >12h — MAINTAIN preserves an inactive grid. Council must "
     "justify or revise."),

    ("*", "*", "PAUSE_LONGS",
     lambda r0, ws: _buy_count(ws) == 0,
     ["melchior", "balthasar"],
     "PAUSE_LONGS while buy_count=0 cancels nothing and prevents rebuild on "
     "the empty side. Council must justify or revise."),

    ("*", "*", "PAUSE_SHORTS",
     lambda r0, ws: _sell_count(ws) == 0,
     ["melchior", "balthasar"],
     "PAUSE_SHORTS while sell_count=0 cancels nothing and prevents rebuild on "
     "the empty side. Council must justify or revise."),
]


# --- Internal helpers ---

_block_id_cache: dict = {}


def _get_shared_block_id(label: str) -> str:
    """Look up a shared block id by exact label and cache it."""
    cached = _block_id_cache.get(label)
    if cached:
        return cached
    matches = list(client.blocks.list(label=label, limit=1))
    if not matches:
        raise RuntimeError(
            f"shared Letta block label={label!r} not found — has "
            "provision_agents.py been run?"
        )
    _block_id_cache[label] = matches[0].id
    return matches[0].id


def _assistant_texts(response) -> list:
    """Return every assistant_message text in response.messages, in order.

    Sonnet (and occasionally other models) emit a structured JSON
    assistant_message, then run core_memory.append, then emit a SECOND
    chat-style assistant_message summarising the vote. Returning only the
    last message silently loses the structured vote and trips
    SAFE_DEFAULTS. Callers should scan this list and accept the first
    text that parses to the expected schema (R0: has 'position', R1: has
    'hold'). Defensively handles content as either str or list of parts.
    """
    texts: list = []
    for msg in getattr(response, "messages", []) or []:
        mtype = getattr(msg, "message_type", None)
        role = getattr(msg, "role", None)
        if mtype != "assistant_message" and role != "assistant":
            continue
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list) and content:
            parts = []
            for part in content:
                t = getattr(part, "text", None) or (
                    part.get("text") if isinstance(part, dict) else None
                )
                if t:
                    parts.append(t)
            if parts:
                texts.append("\n".join(parts))
    return texts


def _extract_last_assistant_text(response) -> Optional[str]:
    """Backward-compat shim — returns the LAST assistant message text or
    None. New code should use `_assistant_texts(response)` + per-message
    schema validation. See `_assistant_texts` docstring for why the
    trailing-message bias is dangerous for R0/R1 extraction."""
    texts = _assistant_texts(response)
    return texts[-1] if texts else None


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_json_strict(text: str) -> Optional[dict]:
    """Strip markdown fences, find the outermost {...}, parse as JSON."""
    if not text:
        return None
    stripped = _FENCE_RE.sub("", text).strip()
    # First try the whole thing
    try:
        obj = json.loads(stripped)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        pass
    # Fallback: pull the first {...} that parses
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(stripped[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except (ValueError, TypeError):
            return None
    return None


# Per-agent extension fields: when present, must be one of the listed
# values. When ABSENT, the consumer defaults to the first entry
# (EXECUTE / PROCEED). Permissive on missing — the council should never
# block geometry changes because one agent's R0 dropped a field.
REGIME_ACTIONS  = ("EXECUTE", "DEFER_STRUCTURAL", "STAND_DOWN")
GEOMETRY_VETOS  = ("PROCEED", "HOLD_GEOMETRY",   "RISK_BLOCK")


def _validate_r0(parsed: dict, agent_id: str) -> tuple[bool, str]:
    """Validate a Round-0 parsed dict. Returns (ok, error_message).

    Per-agent extension fields (added with the always-R1 synthesis
    architecture) are OPTIONAL — missing fields default permissively
    in resolve_consensus. When present, they must be one of the
    declared values.
    """
    if not isinstance(parsed, dict):
        return False, "not a dict"
    pos = parsed.get("position")
    if not isinstance(pos, str) or not pos:
        return False, "position missing/not a string"
    conv = parsed.get("conviction")
    if not isinstance(conv, (int, float)) or not (0.0 <= float(conv) <= 1.0):
        return False, "conviction missing or out of [0.0, 1.0]"
    evidence = parsed.get("key_evidence")
    if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
        return False, "key_evidence missing or not a list of strings"
    crux = parsed.get("crux")
    if not isinstance(crux, str):
        return False, "crux missing/not a string"
    # Per-agent extension fields — OPTIONAL but enum-constrained when present
    if agent_id == "casper":
        ra = parsed.get("regime_action")
        if ra is not None and ra not in REGIME_ACTIONS:
            return False, f"regime_action={ra!r} not in {REGIME_ACTIONS}"
    elif agent_id == "balthasar":
        gv = parsed.get("geometry_veto")
        if gv is not None and gv not in GEOMETRY_VETOS:
            return False, f"geometry_veto={gv!r} not in {GEOMETRY_VETOS}"
    return True, ""


# Match floats with at least 2 decimal places. Does not include the leading
# sign — negatives are handled by substring-matching the absolute form, since
# world_state serialisation preserves the minus and target.2f preserves it
# too (e.g. evidence "-0.0244" → target "-0.02", stale check against blob).
_FLOAT_RE = re.compile(r'-?\d+\.\d{2,}')

# Evidence items are typically "label: value" strings. If the label half
# mentions a price-derived field, skip extracting numbers from that item —
# prices can drift between world_state build and agent parse.
_PRICE_LABEL_RE = re.compile(r'price', re.IGNORECASE)


def _walk_ws_path_value_pairs(d: Optional[dict]) -> list:
    """Walk a nested world_state dict/list and produce a list of
    (dotted_path, value_str, value_2dp_str) tuples for every scalar leaf.

    `value_2dp_str` is the 2-decimal form for ints and floats; for all
    other scalar types it equals `value_str`. The dotted path uses dot
    separators for dict keys and `[i]` indexing for list/tuple elements,
    e.g. `portfolio.xrp_value_usd` or `open_orders.buys[0].price`.
    """
    pairs: list = []

    def walk(node, prefix):
        if isinstance(node, dict):
            for k, v in node.items():
                new_prefix = f"{prefix}.{k}" if prefix else str(k)
                walk(v, new_prefix)
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, f"{prefix}[{i}]")
        elif isinstance(node, bool):
            s = str(node)
            pairs.append((prefix, s, s))
        elif isinstance(node, (int, float)):
            s = str(node)
            try:
                s2 = f"{float(node):.2f}"
            except (TypeError, ValueError):
                s2 = s
            pairs.append((prefix, s, s2))
        elif node is not None:
            s = str(node)
            pairs.append((prefix, s, s))

    walk(d or {}, "")
    return pairs


def _find_closest_fresh(stale_str: str, ws_pairs: list) -> tuple:
    """Given a stale numeric value string and the path-aware world_state
    pairs from `_walk_ws_path_value_pairs`, return
    (correct_value_2dp_str, dotted_path) for the numerically closest
    candidate. Returns (None, None) when no candidate is within a
    plausible distance — defined as `abs(stale - cand) <= max(|stale|, 1.0)`,
    i.e. roughly within the same order of magnitude OR within 1.0
    absolute units of zero. This guards against claiming a tiny
    `vwap_dev_pct` is "the correct value" for a confabulated price.
    """
    try:
        stale_val = float(stale_str)
    except (TypeError, ValueError):
        return None, None

    best_path = None
    best_val: Optional[float] = None
    best_dist = float('inf')
    for path, val_str, _val_2dp in ws_pairs:
        try:
            cand = float(val_str)
        except (TypeError, ValueError):
            continue
        dist = abs(stale_val - cand)
        if dist < best_dist:
            best_dist = dist
            best_path = path
            best_val = cand

    if best_path is None or best_val is None:
        return None, None
    if best_dist > max(abs(stale_val), 1.0):
        return None, None
    return f"{best_val:.2f}", best_path


def _validate_r0_freshness(agent_id: str, r0_response: Optional[dict],
                            world_state_dict: Optional[dict]) -> dict:
    """Cross-check numeric values in the agent's R0 evidence against the
    current world_state. Returns
        {"stale": bool, "mismatches": [...], "checked": int}
    where each entry in `mismatches` is a 3-tuple
        (stale_value_str, correct_value_str_or_None, field_path_or_None).
    `correct_value_str` and `field_path` are `None` when no plausible
    candidate exists in the current world_state (i.e. the agent
    hallucinated a value with no analog).

    A float counts as fresh if its 2-decimal-precision form (or its raw
    string form) appears as a value anywhere in the world_state. Evidence
    items whose label half contains 'price' are skipped entirely
    (build-vs-parse drift is expected on prices)."""
    if not isinstance(r0_response, dict):
        return {"stale": False, "mismatches": [], "checked": 0}
    evidence_list = r0_response.get("key_evidence") or []
    if not evidence_list or not isinstance(evidence_list, list):
        return {"stale": False, "mismatches": [], "checked": 0}
    if not world_state_dict:
        # No world_state to compare against — can't penalise the agent.
        return {"stale": False, "mismatches": [], "checked": 0}

    ws_pairs = _walk_ws_path_value_pairs(world_state_dict)
    fresh_strs: set = set()
    for _path, val_str, val_2dp_str in ws_pairs:
        if val_str:
            fresh_strs.add(val_str)
        if val_2dp_str:
            fresh_strs.add(val_2dp_str)

    extracted: list = []
    for item in evidence_list:
        if not isinstance(item, str):
            continue
        label, sep, _ = item.partition(":")
        if sep and _PRICE_LABEL_RE.search(label):
            continue
        for m in _FLOAT_RE.findall(item):
            extracted.append(m)

    mismatches: list = []
    for f_str in extracted:
        try:
            f_val = float(f_str)
        except ValueError:
            continue
        target_2dp = f"{f_val:.2f}"
        if target_2dp in fresh_strs or f_str in fresh_strs:
            continue
        correct_val, field_path = _find_closest_fresh(f_str, ws_pairs)
        mismatches.append((f_str, correct_val, field_path))

    return {
        "stale": len(mismatches) > 0,
        "mismatches": mismatches,
        "checked": len(extracted),
    }


def _format_freshness_mismatches_compact(mismatches: list) -> str:
    """Single-line representation of mismatches for log/alert messages.
    Accepts the 3-tuple shape produced by `_validate_r0_freshness`."""
    parts: list = []
    for tup in mismatches or []:
        if isinstance(tup, tuple) and len(tup) == 3:
            stale, correct, path = tup
            if correct is None or path is None:
                parts.append(f"{stale} -> ? (no match)")
            else:
                parts.append(f"{stale} -> {correct} ({path})")
        else:
            parts.append(repr(tup))
    return "[" + ", ".join(parts) + "]"


def _freshness_retry_prompt(mismatches: list) -> str:
    """Build the corrective re-prompt for the freshness-retry path.

    `mismatches` is the 3-tuple shape produced by
    `_validate_r0_freshness`: each entry is
    (stale_value_str, correct_value_str_or_None, field_path_or_None).
    The prompt frames the correction as an update to context (the
    world_state moved between the agent's reasoning step and its output)
    rather than as the agent having been wrong, and ends with an explicit
    restatement of the JSON-only output contract — models under
    correction pressure routinely exit the JSON contract and reply
    conversationally, which trips SAFE_DEFAULTS. The restatement is the
    belt-and-suspenders that keeps the parser happy.
    """
    bullet_lines: list = []
    for tup in mismatches or []:
        if isinstance(tup, tuple) and len(tup) == 3:
            stale, correct, path = tup
        else:
            stale, correct, path = (str(tup), None, None)
        if correct is None or path is None:
            bullet_lines.append(
                f"- Your prior response cited {stale} in key_evidence; no "
                f"matching field exists in the current world_state. Re-vote "
                f"citing only values present in the world_state block."
            )
        else:
            bullet_lines.append(
                f"- Your prior response cited {stale} in key_evidence. The "
                f"current world_state has {correct} at {path}."
            )
    bullets = "\n".join(bullet_lines) if bullet_lines else (
        "- (no fields available; re-read your world_state memory block "
        "and cite only values present there)"
    )
    return (
        "The world_state was updated between your reasoning step and "
        "your output, so a few of the numbers you cited are no longer "
        "current. Here are the current values for the fields you "
        "referenced:\n"
        f"{bullets}\n\n"
        "Please re-emit your R0 vote using the current values. Respond "
        "with a single JSON object on one line matching the schema "
        '{"position", "conviction", "key_evidence", "crux"} — no prose, '
        "no markdown fencing, no preamble, no closing summary."
    )


def _format_triggers_section(triggers: Optional[list]) -> str:
    """Render the TRIGGERS section that precedes the rest of the R0
    prompt. Identical text across all three agents.

    `triggers` is the world_state["triggers_since_last_cycle"] list:
    [{"trigger_id": "T1", "timestamp": <unix>, "details": {...}}, ...]
    """
    if not triggers:
        return (
            "=== NO TRIGGERS IN CURRENT WINDOW ===\n"
            "No structural events detected since the last cycle. Evaluate "
            "routine state.\n\n"
        )
    from datetime import datetime, timezone
    lines: list = []
    for t in triggers:
        tid = t.get("trigger_id", "?")
        ts = t.get("timestamp")
        details = t.get("details") or {}
        when = "?"
        if isinstance(ts, (int, float)):
            try:
                when = datetime.fromtimestamp(
                    float(ts), tz=timezone.utc
                ).strftime("%H:%M UTC")
            except Exception:
                when = "?"
        # Compact summary string per trigger type. Falls back to a
        # generic key=value dump if the trigger isn't recognised.
        if tid == "T1":
            mp = details.get("max_move_pct")
            summary = (f"Velocity spike: intra-hour |H-L|/L = "
                       f"{float(mp)*100:.2f}% (> 3.0% threshold)"
                       if mp is not None else "Velocity spike")
        elif tid == "T2":
            d = details.get("direction") or "?"
            n = details.get("consecutive") or 0
            summary = (f"Grid level breach: {n} consecutive 1h closes "
                       f"{d} the outer grid level")
        elif tid == "T3":
            c = details.get("crossed_count")
            summary = (f"Rapid level traversal: 1h candle crossed "
                       f"{c} grid level lines (>= 4)"
                       if c is not None else "Rapid level traversal")
        elif tid == "T4":
            ch = details.get("current_hours")
            summary = (f"Fill drought crossed 24h: now "
                       f"{float(ch):.1f}h since last fill"
                       if ch is not None else "Fill drought crossed 24h")
        elif tid == "T6":
            r1 = details.get("rank1")
            dp = details.get("deployed_pnl_pct")
            r1p = details.get("rank1_pnl_pct")
            improvement = details.get("improvement_pct")
            imp_str = (f", PnL +{float(improvement)*100:.0f}%"
                       if improvement is not None else "")
            summary = (
                f"Scorer rank-1 stable improvement: rank-1={r1} "
                f"(pnl={r1p}) vs deployed pnl={dp}{imp_str}"
            )
        elif tid == "T7":
            summary = "Scorer acceptability returned after prior stand-down"
        elif tid == "T11":
            summary = (f"Vol regime transition: "
                       f"{details.get('prior')} → {details.get('current')}")
        elif tid == "T12":
            summary = (f"ADX threshold cross "
                       f"({details.get('direction')}): "
                       f"{details.get('prior_adx')} → "
                       f"{details.get('current_adx')}")
        elif tid == "T13":
            summary = (f"VWAP deviation crossed "
                       f"{details.get('direction')}: "
                       f"{details.get('prior_vwap_dev_pct')}% → "
                       f"{details.get('current_vwap_dev_pct')}%")
        else:
            summary = json.dumps(details, default=str)[:200]
        lines.append(f"- [{tid} @ {when}] {summary}")
    body = "\n".join(lines)
    return (
        "=== TRIGGERS IN CURRENT WINDOW ===\n"
        "The following structural events occurred since the last cycle:\n"
        f"{body}\n"
        "Evaluate the current state with these events in mind, alongside "
        "the world_state below.\n\n"
    )


def _extension_field_clause(agent_id: str) -> str:
    """Per-agent extra field appended to the R0/R1 JSON schema for the
    council's two-stage synthesis architecture. Casper emits
    regime_action; Balthasar emits geometry_veto. Melchior unchanged
    (his geometry field is already in the appendix path)."""
    if agent_id == "casper":
        return (
            ', "regime_action": "<EXECUTE | DEFER_STRUCTURAL | STAND_DOWN — '
            'whether the regime supports executing structural changes this '
            'cycle>"'
        )
    if agent_id == "balthasar":
        return (
            ', "geometry_veto": "<PROCEED | HOLD_GEOMETRY | RISK_BLOCK — '
            'whether risk conditions permit Melchior to change grid '
            'geometry this cycle>"'
        )
    return ""


def _r0_prompt(cycle_id: str, triggers: Optional[list] = None,
               agent_id: Optional[str] = None) -> str:
    ext = _extension_field_clause(agent_id or "")
    return (
        _format_triggers_section(triggers)
        + f"Cycle {cycle_id}. World state has been updated in your context "
        f"window.\n\n"
        f"BEFORE DECIDING: read your self_model block.\n\n"
        f"If your self_model entry says you have been wrong about this kind "
        f"of call in the past, your DEFAULT must be to revise away from "
        f"that prior failure mode. To override the self_model warning and "
        f"vote the same way again, you MUST cite a specific world_state "
        f"field name and value that meaningfully differentiates today from "
        f"the conditions the self_model describes — for example, 'roc_6h "
        f"has flipped to +0.4 vs the prior negative regime', not 'momentum "
        f"is different'. Naming the self_model conflict in key_evidence "
        f"without resolving it (either by revising your vote or by citing "
        f"a concrete differentiating datum) is not acceptable and will be "
        f"treated as a non-response.\n\n"
        f"If your self_model entry supports your call, cite it briefly in "
        f"key_evidence prefixed with 'self_model:'. If self_model is empty "
        f"or no entry applies, proceed normally — do not invent a "
        f"reflection just to satisfy this rule.\n\n"
        f"Respond ONLY with a single JSON object on one line, no preamble, "
        f"no markdown fences: "
        f'{{"position": "<one of your valid actions>", '
        f'"conviction": <float 0.0-1.0>, '
        f'"key_evidence": [<3-5 short strings citing specific indicators/data '
        f'from world_state; prefix any self_model citation with '
        f"'self_model:'; if you are overriding a self_model warning, one "
        f"evidence entry must name the specific world_state field and "
        f"value that justifies the override>], "
        f'"crux": "<one sentence: the single thing that would change your '
        f'mind>"'
        f"{ext}"
        f'}}. After responding, you may use core_memory tools to '
        f"append a new observation to your self_model block if this cycle "
        f"taught you something worth recording."
    )


def _r0_retry_prompt(cycle_id: str) -> str:
    return (
        f"Cycle {cycle_id}: your previous response could not be parsed as "
        f"JSON. Respond again with ONLY the single JSON object — no preamble, "
        f"no fences, no commentary — fields: position, conviction, "
        f"key_evidence (list of strings), crux."
    )


_R1_FRAMING_PER_AGENT = {
    "casper": (
        "You're Casper. Given Melchior's proposed grid_action and "
        "Balthasar's risk read below, refine your regime classification "
        "AND your regime_action verdict. Does the proposed action align "
        "with the regime you classified in R0? If Balthasar identifies "
        "risk you didn't see (open position, exhausted buffer, "
        "concentrated skew), does that shift your read? Your regime_action "
        "(EXECUTE/DEFER_STRUCTURAL/STAND_DOWN) is the lever the engine "
        "reads — set it deliberately based on whether the regime supports "
        "executing structural changes this cycle."
    ),
    "melchior": (
        "You're Melchior. Given Casper's regime read and Balthasar's "
        "risk read below, refine your grid_action and geometry. If Casper "
        "says DEFER_STRUCTURAL or STAND_DOWN, your structural changes "
        "won't execute this cycle — consider holding or revising. If "
        "Balthasar says HOLD_GEOMETRY or RISK_BLOCK, what specifically "
        "is he concerned about — open round-trip, recent rebuild, "
        "buffer exhaustion? Re-emit your full R0 schema including "
        "geometry if you still want a rebuild."
    ),
    "balthasar": (
        "You're Balthasar. Given Melchior's specific proposed grid_action "
        "and geometry, and Casper's regime read below, refine your "
        "risk_action AND your geometry_veto verdict. Does Melchior's "
        "specific RECENTRE/TIGHTEN/WIDEN create risk that warrants "
        "HOLD_GEOMETRY (defer this cycle) or RISK_BLOCK (forbid the "
        "change)? Does the regime affect your assessment? Your "
        "geometry_veto is the lever the engine reads — set it "
        "deliberately based on whether risk conditions permit the "
        "specific geometry change proposed."
    ),
}


def _format_peer_r0(peer_id: str, r0: dict) -> str:
    """Render one peer's R0 output as a compact block for inclusion
    in another agent's R1 user message. Strips conviction (R1 must
    not anchor on authority) and any per-agent extension field that
    only matters to the peer's own role."""
    if not isinstance(r0, dict):
        return f"[{peer_id} R0: missing]"
    pos = r0.get("position", "?")
    crux = r0.get("crux", "")
    ev = r0.get("key_evidence") or []
    ev_str = "; ".join(str(e) for e in ev[:5])
    extras = []
    if peer_id == "casper" and r0.get("regime_action"):
        extras.append(f"regime_action={r0.get('regime_action')}")
    if peer_id == "balthasar" and r0.get("geometry_veto"):
        extras.append(f"geometry_veto={r0.get('geometry_veto')}")
    if peer_id == "melchior":
        geom = r0.get("geometry") or {}
        ts = geom.get("target_spacing_pct")
        tl = geom.get("target_levels")
        if ts is not None or tl is not None:
            extras.append(f"geometry=target_spacing_pct={ts} target_levels={tl}")
    ext_str = (" | " + " ".join(extras)) if extras else ""
    return (
        f"[{peer_id} R0] position={pos}{ext_str}\n"
        f"  key_evidence: {ev_str}\n"
        f"  crux: {crux}"
    )


def _r1_prompt(cycle_id: str, agent_id: str,
                self_r0: Optional[dict],
                peer_r0s: dict) -> str:
    """Build the R1 synthesis prompt for one agent. Peer R0 content is
    pasted explicitly in the user message — no reliance on Letta memory
    tool reads — so the agent definitely sees what the others said.

    `peer_r0s` is a dict {peer_agent_id: peer_r0_dict} of the OTHER two
    agents' R0 outputs.
    """
    framing = _R1_FRAMING_PER_AGENT.get(agent_id) or (
        "Refine your R0 position in light of peer outputs below."
    )
    self_pos = (self_r0 or {}).get("position", "?")
    self_crux = (self_r0 or {}).get("crux", "")
    self_ev = (self_r0 or {}).get("key_evidence") or []
    self_ev_str = "; ".join(str(e) for e in self_ev[:5])
    peer_block = "\n\n".join(
        _format_peer_r0(pid, pr0) for pid, pr0 in peer_r0s.items()
    )
    ext = _extension_field_clause(agent_id)
    return (
        f"Cycle {cycle_id} — ROUND 1 SYNTHESIS.\n\n"
        f"{framing}\n\n"
        f"=== YOUR ROUND 0 ===\n"
        f"position={self_pos}\n"
        f"key_evidence: {self_ev_str}\n"
        f"crux: {self_crux}\n\n"
        f"=== PEER ROUND 0 OUTPUTS ===\n"
        f"{peer_block}\n\n"
        f"Respond ONLY with a single JSON object on one line — same "
        f"schema as your R0, no markdown fences, no preamble. If your "
        f"R1 position holds, restate your R0 fields and use key_evidence "
        f"to engage peer concerns explicitly. If you revise, the new "
        f"position is your final vote.\n"
        f'{{"position": "<one of your valid actions>", '
        f'"conviction": <float 0.0-1.0>, '
        f'"key_evidence": [<3-5 short strings; reference peer reasoning '
        f'where it affected your call>], '
        f'"crux": "<one sentence>"'
        f"{ext}"
        f"}}"
    )


# --- Public API ---

def update_world_state(world_state: dict) -> None:
    """
    Write the orchestrator-built world_state dict into the shared
    'world_state' Letta block (visible to all three agents).
    """
    block_id = _get_shared_block_id("world_state")
    # Also recoverable from any agent's registry row; cache + label lookup is
    # the same source of truth (provisioned blocks are uniquely labelled).
    payload = json.dumps(world_state, indent=2, default=str)
    client.blocks.update(block_id, value=payload)
    log.debug("world_state block updated (%d chars)", len(payload))


def set_cycle_phase(phase: str) -> None:
    """Set the shared cycle_phase block. phase ∈ {'round_0', 'round_1'}."""
    if phase not in ("round_0", "round_1"):
        raise ValueError(f"cycle_phase must be round_0 or round_1, got {phase!r}")
    block_id = _get_shared_block_id("cycle_phase")
    client.blocks.update(block_id, value=phase)
    log.debug("cycle_phase block set to %s", phase)


def send_round_0(agent_id: str, cycle_id: str,
                  world_state: Optional[dict] = None) -> dict:
    """
    Send the Round-0 prompt to one agent, parse the response, update that
    agent's shared r0_output block (with conviction stripped — peers can't
    see conviction), and return the parsed dict (conviction included, used
    by the council internally).

    On parse failure: retry once with a stricter reminder. On second
    failure: return the per-agent safe default with an 'error' flag.

    When `world_state` is provided, the parsed R0 evidence is run through
    `_validate_r0_freshness`. On a stale evidence list, a SINGLE corrective
    re-prompt is sent inline; the re-prompt inlines the correct values for
    each stale citation so the model has a fresh anchor without needing to
    re-introspect the memory block under correction pressure. If the
    re-prompt response parses as a valid R0, it replaces the original
    parse. If the re-prompt response is unparseable, the SAFE_DEFAULTS
    path is taken (a known-stale vote is treated as worse than no vote)
    AND a `category='freshness_retry_failed'` magi_alerts row is written
    so the confabulation pattern is surfaced even when the contingency
    layer absorbs it silently. The returned dict always carries
    `freshness_retry` (bool) and `freshness_mismatches` (list of 3-tuples).
    """
    letta_id = get_letta_agent_id(agent_id)
    if not letta_id:
        raise RuntimeError(
            f"agent_id={agent_id!r} not in agent_registry — has "
            "provision_agents.py been run?"
        )

    parsed: Optional[dict] = None
    last_error = ""
    freshness_retry: bool = False
    freshness_mismatches: list = []
    # Triggers from world_state are rendered into the R0 prompt's leading
    # section. Same list, same framing, for all three agents. Empty list
    # yields the "NO TRIGGERS" framing; non-empty yields per-trigger
    # bullet lines. Threaded only through attempt 1 — the retry prompt
    # is intentionally minimal (re-state JSON contract only).
    triggers_list = (world_state or {}).get("triggers_since_last_cycle") or []
    for attempt in (1, 2):
        prompt = (_r0_prompt(cycle_id, triggers_list, agent_id=agent_id)
                  if attempt == 1
                  else _r0_retry_prompt(cycle_id))
        try:
            response = client.agents.messages.create(
                letta_id,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            last_error = f"transport error: {e!r}"
            log.warning("[%s] R0 attempt %d transport failed: %s", agent_id, attempt, e)
            _alert_exception(agent_id, e, phase=f"R0 attempt {attempt}")
            continue

        # Live Steps-API check: even on a non-raising call, the step's
        # stop_reason may indicate insufficient_credits / llm_api_error etc.
        _check_steps_for_alerts(agent_id, response, phase=f"R0 attempt {attempt}")
        # Token accounting — recorded even for parse-failure responses since
        # the API was billed for them. Uses agent_registry as model source.
        _record_token_usage_from_response(agent_id, response, phase="R0")

        # Scan ALL assistant messages and accept the first one that parses
        # to a valid R0 schema. Trailing chat-style messages emitted after
        # core_memory.append must not clobber the structured vote.
        texts = _assistant_texts(response)
        if not texts:
            last_error = "no assistant_message in response"
            log.warning("[%s] R0 attempt %d had no assistant text", agent_id, attempt)
            continue

        candidate = None
        last_parse_error = ""
        for text in texts:
            obj = _parse_json_strict(text)
            if not isinstance(obj, dict):
                last_parse_error = f"unparseable: {text[:200]!r}"
                continue
            ok, err = _validate_r0(obj, agent_id)
            if not ok:
                last_parse_error = f"validation: {err} in {text[:200]!r}"
                continue
            candidate = obj
            break

        if candidate is None:
            last_error = last_parse_error or "no parseable R0 JSON in any assistant message"
            log.warning("[%s] R0 attempt %d: %s", agent_id, attempt, last_error)
            continue

        parsed = candidate
        break

    # Freshness validator — runs only when a valid parse exists and the
    # caller supplied world_state. Retry is capped at exactly one extra
    # messages.create() call; if the corrected response is unparseable,
    # we drop to SAFE_DEFAULTS (a known-stale vote is worse than no vote
    # since the degraded-mode hard rule can handle the latter cleanly).
    if parsed is not None and world_state is not None:
        check = _validate_r0_freshness(agent_id, parsed, world_state)
        if check.get("stale") and check.get("mismatches"):
            freshness_mismatches = list(check["mismatches"])
            log.warning(
                "[FRESHNESS_FAIL] agent=%s mismatches=%s",
                agent_id,
                _format_freshness_mismatches_compact(freshness_mismatches),
            )
            freshness_retry = True
            try:
                retry_response = client.agents.messages.create(
                    letta_id,
                    messages=[{
                        "role": "user",
                        "content": _freshness_retry_prompt(freshness_mismatches),
                    }],
                )
                _check_steps_for_alerts(agent_id, retry_response,
                                         phase="R0 freshness retry")
                _record_token_usage_from_response(agent_id, retry_response,
                                                   phase="R0")
                retry_texts = _assistant_texts(retry_response)
                retry_parsed: Optional[dict] = None
                retry_failure_reason: str = ""
                if not retry_texts:
                    retry_failure_reason = "no assistant_message in retry response"
                else:
                    for text in retry_texts:
                        obj = _parse_json_strict(text)
                        if not isinstance(obj, dict):
                            retry_failure_reason = (
                                f"retry response was prose, not JSON: "
                                f"{text[:120]!r}"
                            )
                            continue
                        ok, err = _validate_r0(obj, agent_id)
                        if not ok:
                            retry_failure_reason = (
                                f"retry validation failed: {err}"
                            )
                            continue
                        retry_parsed = obj
                        retry_failure_reason = ""
                        break
                if retry_parsed is not None:
                    log.info(
                        "[FRESHNESS_RETRY_OK] agent=%s — replacing R0 with "
                        "corrected response", agent_id,
                    )
                    parsed = retry_parsed
                else:
                    log.warning(
                        "[FRESHNESS_RETRY_FAIL] agent=%s — corrected response "
                        "unparseable; dropping to SAFE_DEFAULTS (%s)",
                        agent_id, retry_failure_reason,
                    )
                    # Surface the confabulation pattern even when the
                    # contingency layer absorbs the SAFE_DEFAULTS silently.
                    # 60-min dedup on (category, agent_id) is built into
                    # insert_alert — repeated failures inside an hour
                    # collapse to one row, which is the right cadence for
                    # an observability signal.
                    try:
                        insert_alert(
                            severity='warn',
                            category='freshness_retry_failed',
                            agent_id=agent_id,
                            message=(
                                f"cycle={cycle_id} agent={agent_id}; "
                                f"{retry_failure_reason or 'retry response unparseable'}; "
                                f"mismatches: "
                                f"{_format_freshness_mismatches_compact(freshness_mismatches)}"
                            ),
                        )
                    except Exception as alert_err:
                        log.warning(
                            "[%s] insert_alert(freshness_retry_failed) "
                            "failed: %r", agent_id, alert_err,
                        )
                    last_error = "freshness retry response unparseable"
                    parsed = None
            except Exception as e:
                log.warning(
                    "[FRESHNESS_RETRY_ERROR] agent=%s transport error: %s",
                    agent_id, e,
                )
                _alert_exception(agent_id, e, phase="R0 freshness retry")
                last_error = f"freshness retry transport error: {e!r}"
                parsed = None

    if parsed is None:
        log.error(
            "[%s] R0 failed after retry — falling back to safe default. "
            "Last error: %s", agent_id, last_error
        )
        safe = dict(SAFE_DEFAULTS[agent_id])
        safe["error"] = last_error
        safe["freshness_retry"] = freshness_retry
        safe["freshness_mismatches"] = freshness_mismatches
        # Still publish the safe default to the peer block so downstream
        # agents see SOMETHING and don't see a stale value from a prior cycle.
        peer_payload = {
            "position":     safe["position"],
            "key_evidence": safe["key_evidence"],
            "crux":         safe["crux"],
        }
        try:
            client.blocks.update(
                _get_shared_block_id(f"{agent_id}_r0_output"),
                value=json.dumps(peer_payload),
            )
        except Exception as e:
            log.error("[%s] failed to publish safe r0_output block: %s", agent_id, e)
        return safe

    # Publish to peer block — strip conviction (peers must not see it)
    peer_payload = {
        "position":     parsed["position"],
        "key_evidence": parsed["key_evidence"],
        "crux":         parsed["crux"],
    }
    client.blocks.update(
        _get_shared_block_id(f"{agent_id}_r0_output"),
        value=json.dumps(peer_payload),
    )
    parsed["freshness_retry"] = freshness_retry
    parsed["freshness_mismatches"] = freshness_mismatches
    return parsed


def run_round_0_parallel(cycle_id: str,
                          world_state: Optional[dict] = None) -> dict:
    """
    Fan out Round-0 to all three agents in parallel. Each agent's slot in the
    returned dict is either its parsed response (with conviction) or a safe
    default carrying an 'error' key. The cycle_phase block is set to
    'round_0' before fan-out so agents see the correct phase.

    When `world_state` is supplied, it is threaded into `send_round_0` so
    the freshness validator can cross-check each agent's evidence against
    the current world_state values. Backward-compatible: callers that omit
    `world_state` get the prior (no-validation) behaviour unchanged.
    """
    set_cycle_phase("round_0")

    agents = ("casper", "melchior", "balthasar")
    results: dict = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(send_round_0, a, cycle_id, world_state): a
                   for a in agents}
        for fut, a in futures.items():
            try:
                results[a] = fut.result()
            except Exception as e:
                log.exception("[%s] R0 future raised: %s", a, e)
                safe = dict(SAFE_DEFAULTS[a])
                safe["error"] = f"executor exception: {e!r}"
                safe["freshness_retry"] = False
                safe["freshness_mismatches"] = []
                results[a] = safe
    return results


def detect_conflict(round_0: dict, world_state: Optional[dict] = None) -> Optional[dict]:
    """
    Walk CONFLICT_MATRIX against the round_0 positions and world_state.
    Returns None if no rule matched, otherwise the matched rule with the
    highest combined conviction of the two named agents, as
    {'agents': [a, b], 'reason': str}.

    world_state is optional for backward compatibility, but the grid-state
    rules require it to evaluate. Pass it from orchestrator.run_cycle.
    """
    casper_pos    = round_0.get("casper",    {}).get("position")
    melchior_pos  = round_0.get("melchior",  {}).get("position")
    balthasar_pos = round_0.get("balthasar", {}).get("position")

    def _match(rule_val: str, actual_val) -> bool:
        return rule_val == "*" or rule_val == actual_val

    matches = []
    for regime_rule, grid_rule, risk_rule, predicate, agents, reason in CONFLICT_MATRIX:
        if not _match(regime_rule, casper_pos):
            continue
        if not _match(grid_rule, melchior_pos):
            continue
        if not _match(risk_rule, balthasar_pos):
            continue
        if predicate is not None:
            try:
                if not predicate(round_0, world_state):
                    continue
            except Exception as e:
                log.warning("conflict predicate raised: %s", e)
                continue

        a, b = agents
        combined = float(round_0.get(a, {}).get("conviction") or 0.0) + \
                   float(round_0.get(b, {}).get("conviction") or 0.0)
        matches.append((combined, {"agents": list(agents), "reason": reason}))

    if not matches:
        return None
    # Highest combined conviction wins
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def send_round_1_synthesis(agent_id: str, cycle_id: str,
                             self_r0: dict, peer_r0s: dict) -> dict:
    """Always-fires R1 synthesis call for one agent. The R1 user message
    explicitly pastes peer R0 outputs (position, key_evidence, crux,
    extension fields) — no reliance on Letta memory-tool reads.

    Returns the parsed R1 dict (same schema as R0 plus the agent's
    extension field), with a `_r1_parse_error` slot populated if
    parsing failed. On failure, callers should fall back to the
    agent's R0 for the final consensus — permissive degradation.
    """
    letta_id = get_letta_agent_id(agent_id)
    if not letta_id:
        raise RuntimeError(f"agent_id={agent_id!r} not in agent_registry")

    prompt = _r1_prompt(cycle_id, agent_id, self_r0, peer_r0s)

    try:
        response = client.agents.messages.create(
            letta_id,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        log.exception("[%s] R1 transport failed: %s", agent_id, e)
        _alert_exception(agent_id, e, phase="R1")
        return {"_r1_parse_error": f"transport: {e!r}"}

    _check_steps_for_alerts(agent_id, response, phase="R1")
    _record_token_usage_from_response(agent_id, response, phase="R1")

    texts = _assistant_texts(response)
    parsed: Optional[dict] = None
    last_parse_error = ""
    for text in texts:
        obj = _parse_json_strict(text)
        if not isinstance(obj, dict):
            last_parse_error = f"unparseable: {text[:200]!r}"
            continue
        ok, err = _validate_r0(obj, agent_id)
        if not ok:
            last_parse_error = f"validation: {err} in {text[:200]!r}"
            continue
        parsed = obj
        break

    if parsed is None:
        log.warning("[%s] R1 synthesis response unparseable: %s",
                    agent_id, last_parse_error)
        return {"_r1_parse_error": last_parse_error
                or "no parseable R0-schema JSON in R1 response"}
    return parsed


def run_round_1(round_0: dict, cycle_id: str) -> dict:
    """Always-fires synthesis: send R1 to ALL three agents in parallel.
    Each agent's R1 user message pastes the OTHER two agents' R0
    outputs explicitly. Returns {agent_id: parsed_r1_or_error_marker}.

    Signature is intentionally simpler than the old conflict-driven
    version; orchestrator.run_cycle calls this unconditionally."""
    set_cycle_phase("round_1")
    agents = ("casper", "melchior", "balthasar")
    results: dict = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        for a in agents:
            self_r0 = round_0.get(a) or {}
            peers = {p: round_0.get(p) or {} for p in agents if p != a}
            futures[pool.submit(send_round_1_synthesis, a, cycle_id,
                                self_r0, peers)] = a
        for fut, a in futures.items():
            try:
                results[a] = fut.result()
            except Exception as e:
                log.exception("[%s] R1 future raised: %s", a, e)
                results[a] = {"_r1_parse_error": f"executor: {e!r}"}
    return results


_NUM_RE  = re.compile(r"\b\d+\.?\d*\b")
_WORD_RE = re.compile(r"[A-Za-z]+")


def validate_revision(round_0_evidence: list, revision_evidence: str) -> tuple[bool, str]:
    """
    Decide whether a Round-1 revision is "real" or capitulation.

    Valid when:
      - revision_evidence is ≥ 20 chars, AND
      - contains a numeric value not present in round_0_evidence joined string,
        OR contains ≥ 3 words of length > 4 not present in round_0_evidence
        joined string (case-insensitive)
    """
    if not isinstance(revision_evidence, str) or len(revision_evidence) < 20:
        return False, "revision_evidence shorter than 20 chars"

    joined_r0 = " ".join(round_0_evidence or []) if round_0_evidence else ""
    joined_r0_lower = joined_r0.lower()

    # Numeric novelty
    r0_nums = set(_NUM_RE.findall(joined_r0))
    rev_nums = set(_NUM_RE.findall(revision_evidence))
    novel_nums = rev_nums - r0_nums
    if novel_nums:
        return True, f"novel numeric values: {sorted(novel_nums)}"

    # Word novelty (length > 4, case-insensitive)
    rev_words = [w.lower() for w in _WORD_RE.findall(revision_evidence) if len(w) > 4]
    r0_words = {w.lower() for w in _WORD_RE.findall(joined_r0_lower) if len(w) > 4}
    novel_words = [w for w in rev_words if w not in r0_words]
    # Distinct novel words
    distinct_novel = list(dict.fromkeys(novel_words))
    if len(distinct_novel) >= 3:
        return True, f"novel words: {distinct_novel[:5]}"

    return False, "no novel numeric values and < 3 novel long words (capitulation)"


def _most_conservative_risk(positions: list) -> str:
    """
    Pick the most conservative balthasar risk action from a list of candidates.
    Order: HALT > PAUSE_LONGS / PAUSE_SHORTS > CLEAR.
    Ties between PAUSE_LONGS and PAUSE_SHORTS resolved by first-seen.
    """
    best = None
    best_rank = -1
    for p in positions:
        rank = _RISK_CONSERVATISM_ORDER.get(p, -1)
        if rank > best_rank:
            best_rank = rank
            best = p
    return best or "CLEAR"


def _final_per_agent(round_0: dict, round_1: dict, agent_id: str) -> dict:
    """Return the agent's FINAL R1-or-R0 vote dict. Prefers R1 when
    parseable; falls back to R0 on R1 parse failure. Permissive on
    missing extension fields — caller applies defaults."""
    r1 = (round_1 or {}).get(agent_id) or {}
    if r1 and not r1.get("_r1_parse_error"):
        return r1
    return (round_0 or {}).get(agent_id) or {}


def _safe_extension(agent_final: dict, key: str,
                     allowed: tuple, default: str) -> str:
    """Read an extension field permissively. Missing or unknown -> default."""
    v = agent_final.get(key)
    if isinstance(v, str) and v in allowed:
        return v
    return default


def resolve_consensus(round_0: dict, round_1: Optional[dict],
                      conflict: Optional[dict] = None) -> dict:
    """Always-R1 synthesis consensus.

    Each agent's FINAL vote = their R1 if parseable, else their R0
    (permissive fallback — R1 parse failure does not freeze the cycle).
    The `conflict` argument is retained for backward signature
    compatibility but is unused — CONFLICT_MATRIX retired with the
    R1-always-fires architecture.

    Adds two new fields to the consensus dict, both consumed by the
    engine downstream of enforce_hard_rules:
      - regime_action ∈ {EXECUTE, DEFER_STRUCTURAL, STAND_DOWN}
        (from Casper's final vote; defaults EXECUTE)
      - geometry_veto ∈ {PROCEED, HOLD_GEOMETRY, RISK_BLOCK}
        (from Balthasar's final vote; defaults PROCEED)

    debate_triggered (returned alongside) is True when ANY agent's
    R1 position differs from their R0 position — synthesis caused
    a vote shift.
    """
    casper_final    = _final_per_agent(round_0, round_1 or {}, "casper")
    melchior_final  = _final_per_agent(round_0, round_1 or {}, "melchior")
    balthasar_final = _final_per_agent(round_0, round_1 or {}, "balthasar")

    grid_action = melchior_final.get("position") or "MAINTAIN"
    risk_action = balthasar_final.get("position") or "CLEAR"
    regime      = casper_final.get("position") or "UNCERTAIN"
    regime_action = _safe_extension(
        casper_final, "regime_action", REGIME_ACTIONS, "EXECUTE",
    )
    geometry_veto = _safe_extension(
        balthasar_final, "geometry_veto", GEOMETRY_VETOS, "PROCEED",
    )

    # debate_triggered: did synthesis change anyone's position?
    r1_shifts = []
    for a in ("casper", "melchior", "balthasar"):
        r0_pos = (round_0.get(a) or {}).get("position")
        r1_pos = ((round_1 or {}).get(a) or {}).get("position")
        if (r1_pos and r0_pos and r1_pos != r0_pos):
            r1_shifts.append(f"{a}: {r0_pos}->{r1_pos}")
    debate_triggered = bool(r1_shifts)

    # R1 parse error tracking — surface for debate_records / observability
    r1_errors = {
        a: ((round_1 or {}).get(a) or {}).get("_r1_parse_error")
        for a in ("casper", "melchior", "balthasar")
    }
    r1_errors = {k: v for k, v in r1_errors.items() if v}

    if r1_shifts:
        reasoning = "R1 synthesis shifted: " + "; ".join(r1_shifts)
    else:
        reasoning = "R1 synthesis — all positions held"
    if r1_errors:
        reasoning += (f" (R1 parse errors: {list(r1_errors)} — "
                       f"fell back to R0 for those)")

    return {
        "grid_action":    grid_action,
        "risk_action":    risk_action,
        "regime":         regime,
        "regime_action":  regime_action,
        "geometry_veto":  geometry_veto,
        "debate_triggered": debate_triggered,
        "deadlock":       False,  # legacy field; synthesis architecture
                                  # has no deadlock concept
        "reasoning":      reasoning,
    }


def emit_human_alert(cycle_id: str, reason: str) -> None:
    """
    Stub: log a HUMAN_ALERT line. Future: SMS / email / webhook.
    """
    log.warning("[HUMAN_ALERT] cycle=%s reason=%s", cycle_id, reason)

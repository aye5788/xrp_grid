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

from database import get_agent_registry_row, get_letta_agent_id


log = logging.getLogger(__name__)


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


def _extract_last_assistant_text(response) -> Optional[str]:
    """
    Return the text content of the LAST assistant_message in response.messages,
    or None if there isn't one. Defensively handles content as either str or
    a list of content parts.
    """
    last_text: Optional[str] = None
    for msg in getattr(response, "messages", []) or []:
        mtype = getattr(msg, "message_type", None)
        role = getattr(msg, "role", None)
        if mtype != "assistant_message" and role != "assistant":
            continue
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            last_text = content
        elif isinstance(content, list) and content:
            parts = []
            for part in content:
                t = getattr(part, "text", None) or (
                    part.get("text") if isinstance(part, dict) else None
                )
                if t:
                    parts.append(t)
            if parts:
                last_text = "\n".join(parts)
    return last_text


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


def _validate_r0(parsed: dict, agent_id: str) -> tuple[bool, str]:
    """Validate a Round-0 parsed dict. Returns (ok, error_message)."""
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
    return True, ""


def _r0_prompt(cycle_id: str) -> str:
    return (
        f"Cycle {cycle_id}. World state has been updated in your context "
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
        f'mind>"}}. After responding, you may use core_memory tools to '
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


def _r1_prompt(cycle_id: str, peer_agents: list) -> str:
    return (
        f"Cycle {cycle_id} Round 1. The cycle_phase block is now round_1. "
        f"Your Round 0 position conflicts with: {', '.join(peer_agents)}. "
        f"Their Round 0 outputs are visible in your context window via the "
        f"corresponding _r0_output blocks. Respond ONLY with a JSON object "
        f'on one line: either {{"hold": true, "challenge": "<specific '
        f'rebuttal citing your strongest evidence>"}} OR {{"hold": false, '
        f'"revised_position": "<new position>", "revision_evidence": '
        f'"<specific data point NOT in your Round 0 key_evidence>"}}. Hidden '
        f"from your view: peer conviction scores. Do not anchor to authority "
        f"— reason from evidence only."
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


def send_round_0(agent_id: str, cycle_id: str) -> dict:
    """
    Send the Round-0 prompt to one agent, parse the response, update that
    agent's shared r0_output block (with conviction stripped — peers can't
    see conviction), and return the parsed dict (conviction included, used
    by the council internally).

    On parse failure: retry once with a stricter reminder. On second
    failure: return the per-agent safe default with an 'error' flag.
    """
    letta_id = get_letta_agent_id(agent_id)
    if not letta_id:
        raise RuntimeError(
            f"agent_id={agent_id!r} not in agent_registry — has "
            "provision_agents.py been run?"
        )

    parsed: Optional[dict] = None
    last_error = ""
    for attempt in (1, 2):
        prompt = _r0_prompt(cycle_id) if attempt == 1 else _r0_retry_prompt(cycle_id)
        try:
            response = client.agents.messages.create(
                letta_id,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            last_error = f"transport error: {e!r}"
            log.warning("[%s] R0 attempt %d transport failed: %s", agent_id, attempt, e)
            continue

        text = _extract_last_assistant_text(response)
        if not text:
            last_error = "no assistant_message in response"
            log.warning("[%s] R0 attempt %d had no assistant text", agent_id, attempt)
            continue

        candidate = _parse_json_strict(text)
        if candidate is None:
            last_error = f"unparseable response: {text[:200]!r}"
            log.warning("[%s] R0 attempt %d unparseable: %s", agent_id, attempt, text[:200])
            continue

        ok, err = _validate_r0(candidate, agent_id)
        if not ok:
            last_error = f"validation: {err}"
            log.warning("[%s] R0 attempt %d invalid: %s", agent_id, attempt, err)
            continue

        parsed = candidate
        break

    if parsed is None:
        log.error(
            "[%s] R0 failed after retry — falling back to safe default. "
            "Last error: %s", agent_id, last_error
        )
        safe = dict(SAFE_DEFAULTS[agent_id])
        safe["error"] = last_error
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
    return parsed


def run_round_0_parallel(cycle_id: str) -> dict:
    """
    Fan out Round-0 to all three agents in parallel. Each agent's slot in the
    returned dict is either its parsed response (with conviction) or a safe
    default carrying an 'error' key. The cycle_phase block is set to
    'round_0' before fan-out so agents see the correct phase.
    """
    set_cycle_phase("round_0")

    agents = ("casper", "melchior", "balthasar")
    results: dict = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(send_round_0, a, cycle_id): a for a in agents}
        for fut, a in futures.items():
            try:
                results[a] = fut.result()
            except Exception as e:
                log.exception("[%s] R0 future raised: %s", a, e)
                safe = dict(SAFE_DEFAULTS[a])
                safe["error"] = f"executor exception: {e!r}"
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


def send_round_1_challenge(agent_id: str, peer_agents: list, cycle_id: str) -> dict:
    """
    Send the Round-1 challenge to one agent. Peers' r0_output blocks are
    already in this agent's context window — no tool call needed.

    Returns a normalised dict:
      {"held": bool, "text": str,
       "revised_position": str|None, "revision_evidence": str|None,
       "error": str|None}
    """
    letta_id = get_letta_agent_id(agent_id)
    if not letta_id:
        raise RuntimeError(f"agent_id={agent_id!r} not in agent_registry")

    prompt = _r1_prompt(cycle_id, peer_agents)

    try:
        response = client.agents.messages.create(
            letta_id,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        log.exception("[%s] R1 transport failed: %s", agent_id, e)
        return {"held": True, "text": "", "revised_position": None,
                "revision_evidence": None, "error": f"transport: {e!r}"}

    text = _extract_last_assistant_text(response) or ""
    parsed = _parse_json_strict(text)

    if not isinstance(parsed, dict) or "hold" not in parsed:
        log.warning("[%s] R1 response unparseable / missing 'hold': %s",
                    agent_id, text[:200])
        # Treat unparseable as a hold (no revision) — preserves caller intent
        return {"held": True, "text": text,
                "revised_position": None, "revision_evidence": None,
                "error": "unparseable"}

    held = bool(parsed["hold"])
    if held:
        return {
            "held": True,
            "text": str(parsed.get("challenge", "")),
            "revised_position": None,
            "revision_evidence": None,
            "error": None,
        }

    return {
        "held": False,
        "text": str(parsed.get("revision_evidence", "")),
        "revised_position": parsed.get("revised_position"),
        "revision_evidence": parsed.get("revision_evidence"),
        "error": None,
    }


def run_round_1(conflict: dict, cycle_id: str) -> dict:
    """
    Send Round-1 challenges in parallel to the two agents named in
    conflict['agents']. The third agent does not participate.
    Returns {agent_id: dict-from-send_round_1_challenge}.
    """
    set_cycle_phase("round_1")

    agents_in_conflict = list(conflict["agents"])
    peers_map = {
        a: [p for p in agents_in_conflict if p != a]
        for a in agents_in_conflict
    }

    results: dict = {}
    with ThreadPoolExecutor(max_workers=len(agents_in_conflict)) as pool:
        futures = {
            pool.submit(send_round_1_challenge, a, peers_map[a], cycle_id): a
            for a in agents_in_conflict
        }
        for fut, a in futures.items():
            try:
                results[a] = fut.result()
            except Exception as e:
                log.exception("[%s] R1 future raised: %s", a, e)
                results[a] = {
                    "held": True, "text": "", "revised_position": None,
                    "revision_evidence": None, "error": f"executor: {e!r}",
                }
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


def resolve_consensus(round_0: dict, round_1: Optional[dict],
                      conflict: Optional[dict]) -> dict:
    """
    Final consensus rules:
      - regime = casper's final position (r0 unless validly revised in r1)
      - No conflict: grid_action = melchior.r0, risk_action = balthasar.r0
      - Conflict + at least one valid revision: apply each agent's final
        position into their slot
      - Conflict + all-held / all-invalid: deadlock, grid_action='MAINTAIN',
        risk_action=most conservative of proposals seen
    """
    final = {
        "casper":    round_0.get("casper",    {}).get("position"),
        "melchior":  round_0.get("melchior",  {}).get("position"),
        "balthasar": round_0.get("balthasar", {}).get("position"),
    }

    deadlock = False
    revision_notes: list = []

    if conflict and round_1:
        validated_count = 0
        for agent in conflict["agents"]:
            r1 = round_1.get(agent, {})
            if r1.get("held"):
                continue
            revised = r1.get("revised_position")
            rev_ev = r1.get("revision_evidence") or ""
            if not revised:
                continue
            r0_evidence = round_0.get(agent, {}).get("key_evidence", []) or []
            is_valid, reason = validate_revision(r0_evidence, rev_ev)
            if is_valid:
                old = final[agent]
                final[agent] = revised
                validated_count += 1
                revision_notes.append(
                    f"{agent} revised from {old} to {revised} citing "
                    f"{rev_ev[:200]!r}"
                )
            else:
                log.info(
                    "[%s] R1 revision rejected as capitulation: %s",
                    agent, reason
                )

        if validated_count == 0:
            deadlock = True

    regime = final["casper"] or "UNCERTAIN"

    if deadlock:
        grid_action = "MAINTAIN"
        # Most conservative of every balthasar proposal we have on record
        proposed_risks = [round_0.get("balthasar", {}).get("position")]
        if round_1 and isinstance(round_1.get("balthasar"), dict):
            r1_b = round_1["balthasar"]
            if r1_b.get("revised_position"):
                proposed_risks.append(r1_b["revised_position"])
        proposed_risks = [p for p in proposed_risks if p]
        risk_action = _most_conservative_risk(proposed_risks)
        reasoning = (
            f"DEADLOCK: {conflict['reason']} Round 1 produced no valid revision."
        )
    elif conflict:
        grid_action = final["melchior"]
        risk_action = final["balthasar"]
        if revision_notes:
            reasoning = "Conflict resolved in Round 1: " + "; ".join(revision_notes)
        else:
            # Shouldn't happen — guarded by validated_count==0 → deadlock above
            reasoning = "Conflict resolved with no revision notes (defensive fallback)"
    else:
        grid_action = final["melchior"]
        risk_action = final["balthasar"]
        reasoning = "No conflict — consensus from Round 0"

    return {
        "grid_action": grid_action,
        "risk_action": risk_action,
        "regime":      regime,
        "deadlock":    deadlock,
        "reasoning":   reasoning,
    }


def emit_human_alert(cycle_id: str, reason: str) -> None:
    """
    Stub: log a HUMAN_ALERT line. Future: SMS / email / webhook.
    """
    log.warning("[HUMAN_ALERT] cycle=%s reason=%s", cycle_id, reason)

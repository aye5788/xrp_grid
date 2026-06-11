"""council_v2.py — the Stage-3 hand-rolled ARBITER council for MAGI.

Replaces the ADK parallel-R0 / conditional-R1 engine (magi/council.py) with a
SEQUENTIAL six-call choreography over the three proven standalone seat-callers
(magi/agents/{casper_gemini,melchior_deepseek,balthasar_claude}.py). Direct
vendor SDKs only — NOT CrewAI, NOT an ADK framework, NOT LiteLlm.

Choreography (one cycle = six model calls):
  Phase A — openings, strictly sequential:
    a. Casper  (regime)    — no predecessor context.
    b. Melchior(grid econ) — premise: Casper's regime + regime_action as a GIVEN
       FACT, framed orthogonally (label only — no Casper conviction/crux/evidence,
       to avoid anchoring/deference).
    c. Balthasar(risk)     — sees Casper's + Melchior's FULL openings; this is his
       opening risk read.
  Phase B — rebuttal, ALWAYS runs, Casper + Melchior only, against a FROZEN
    snapshot of all three openings (neither rebutter sees the other's rebuttal):
    each may revise or hold; holding REQUIRES a crux stating why they hold against
    the strongest opposing point (no silent assent).
  Phase C — synthesis, Balthasar: sees the three openings + both rebuttals. His
    returned RiskVote IS the council's final risk call.

Public entry:
    run_council(world_state: dict, cycle_id: str) -> (round_0, round_1, cons)

returning the EXACT shapes orchestrator._build_debate_record and (the cons
contract) enforce_hard_rules consume today. The council emits Melchior's verdict
UNFLATTENED as cons['grid_verdict']; the orchestrator owns verdict -> grid_action.

Key downstream contracts this module honours (verified against the live code):
  * round_0['melchior'] carries 'verdict' (NOT 'position') as its primary; its
    'geometry' slot is set to Melchior's POST-REBUTTAL (final) geometry so the
    engine builds to the final call, while 'verdict' stays the OPENING verdict for
    the flight-recorder column. cons['grid_verdict'] is the post-rebuttal verdict.
  * round_0['balthasar']['position'] = his OPENING risk_action (the record builder
    reads 'position'); his synthesis risk_action/geometry_veto go to cons.
  * cons keys mirror resolve_consensus: grid_verdict, risk_action, regime,
    regime_action, geometry_veto, debate_triggered, deadlock, reasoning — PLUS
    'trace_id' (Langfuse id captured inside trace_cycle; enforce_hard_rules does
    cons = dict(consensus), so the key survives to _build_debate_record) and, on a
    stand-down, 'council_error'.

Tracing: this module OWNS Langfuse tracing. The whole run is wrapped in
trace_cycle(cycle_id); each of the six calls is wrapped in trace_seat(...) with
EXPLICIT per-seat model/vendor attribution (Melchior is deepseek, never claude,
even though it speaks the Anthropic SDK), and each generation is updated with the
vote output + usage_details INCLUDING cached-token counts so caching is
observable, not assumed.

Fail-safe: every seat call is wrapped. A vendor error (missing key, balance
exhaustion, API/validation error after the seat-caller's own retry) does NOT
crash. On any seat failure the council STANDS DOWN to a safe hold — grid_verdict=
THESIS_HOLDS (-> MAINTAIN), risk_action=CLEAR, permissive regime_action/
geometry_veto (no fabricated veto) — never a fabricated agent vote; missing seats
are filled with the SAFE_DEFAULTS degradation fingerprint so the existing
council-degradation detector still trips on a sustained outage.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

from magi.agents.casper_gemini import run_casper_with_meta
from magi.agents.melchior_deepseek import run_melchior_with_meta
from magi.agents.balthasar_claude import run_balthasar_with_meta
from magi.agents.personas import load_persona
from magi.agents import tracing
from database import get_agent_recall

log = logging.getLogger(__name__)

# Explicit per-seat attribution for the trace. Model strings match the seat
# callers' defaults; vendor is what the bytes ACTUALLY hit (Melchior speaks the
# Anthropic SDK to DeepSeek's endpoint, so vendor='deepseek', never 'anthropic').
_CASPER_MODEL = "gemini-2.5-flash"
_MELCHIOR_MODEL = "deepseek-v4-pro"
_BALTHASAR_MODEL = "claude-sonnet-4-6"

# Stage-4 item-1 config fingerprint: where the structural council veto is enforced.
# As of item 2a the veto lives IN the arbiter's synthesis (Balthasar's geometry_veto
# decides RECONFIGURE vs. hold in-council, below), NOT in the removed orchestrator
# hard rule 0d. The literal is part of the config fingerprint, so this flip
# (hard_rule_0d -> in_debate) intentionally changes config_version: the behavioral
# placement of the veto moved.
_VETO_MODE = "in_debate"


def _fp_hash(text: str) -> str:
    """Short, stable content hash of a persona text for the config fingerprint.
    Fine-grained: any edit to the persona registers as a new hash."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def current_config_council_half() -> dict:
    """The deterministic COUNCIL HALF of the config fingerprint, computed with NO
    vendor call. current_config_fingerprint() folds this through
    orchestrator._compose_config_fingerprint to derive the CURRENT config_version —
    the contamination boundary the per-agent recall Journal filters by.

    Identical in shape to the council_half run_council assembles on the live write
    path: the per-seat models are the CONFIGURED handles (_CASPER_MODEL /
    _MELCHIOR_MODEL / _BALTHASAR_MODEL) — which is exactly what config_version hashes
    (operator decision: config_version describes the configured setup, never the
    served id). Because the hash uses configured handles on BOTH the write path and
    here, the version computed here equals the version stamped on rows — so recall's
    config filter matches stored config_version with no served-model caveat. persona
    hashes are read from the SAME .md files the seats load; veto_mode is the live
    _VETO_MODE constant; served_models / casper_model_version_observed are health
    metadata, excluded from the hash, so they are omitted here (None on the write
    path's pre-call state)."""
    persona_hashes = {}
    for name in ("casper", "melchior", "balthasar"):
        try:
            persona_hashes[name] = _fp_hash(load_persona(name))
        except Exception as e:  # noqa: BLE001 - best-effort; null hash mirrors write path
            log.warning("[council_v2] current-config persona read failed for %s: %r",
                        name, e)
            persona_hashes[name] = None
    return {
        "persona_hashes": persona_hashes,
        "models": {
            "casper": _CASPER_MODEL,
            "melchior": _MELCHIOR_MODEL,
            "balthasar": _BALTHASAR_MODEL,
        },
        "casper_model_version_observed": None,  # soft metadata; never in the hash
        "veto_mode": _VETO_MODE,
    }


def current_config_fingerprint() -> tuple[Optional[str], Optional[dict]]:
    """The full (config_version, config_snapshot) for the live configured setup,
    computed OFFLINE with no vendor call. Folds current_config_council_half() through
    orchestrator._compose_config_fingerprint — the SAME hasher the write path uses —
    so the version returned here equals the version stamped on debate_records rows.

    The orchestrator import is LAZY (inside the function) to break the
    orchestrator<->council_v2 module cycle: orchestrator imports run_council at module
    load, so council_v2 cannot import orchestrator at its top; at call time the
    orchestrator module is already imported. Used by run_council to (1) bound recall
    and (2) stamp the trace. Never raises here — run_council wraps the call and falls
    back to (None, None)."""
    from magi.orchestrator import _compose_config_fingerprint  # lazy: break import cycle
    return _compose_config_fingerprint(
        {"_fingerprint_council_half": current_config_council_half()})


def _with_recall(recall_block: Optional[str], extra_context: Optional[str]) -> Optional[str]:
    """Prepend a seat's private recall block to its per-call extra_context. Recall
    rides in extra_context, which every seat-caller places AFTER its cache breakpoint
    (Balthasar's ephemeral cache_control is on the persona+world_state block;
    Melchior/Casper render extra_context as a trailing block) — so injecting recall
    here never busts the cached stable prefix. Computed once per cycle and reused
    across a seat's opening + rebuttal/synthesis, so the recall text is identical
    within the cycle (only the openings/rebuttal/synthesis tail differs, as before)."""
    if not recall_block:
        return extra_context
    if extra_context:
        return f"{recall_block}\n\n{extra_context}"
    return recall_block

# Per-seat usage capture for the standalone --cache-debug diagnostic. Cleared at
# the start of every run_council, appended to by _seat_call. The live path never
# reads it (it just gets overwritten each cycle); it exists so cache tokens are
# observable from the runner without a Langfuse round-trip.
_LAST_RUN_USAGE: list = []

# SAFE_DEFAULTS fingerprint — mirrors magi/council.py:SAFE_DEFAULTS so the
# degradation detector (orchestrator._check_council_degradation, which keys on
# conviction==0.0 AND crux LIKE '(no response)%') still trips on a real outage.
# Used ONLY to fill seats we never got a vote from on a stand-down — never to
# fabricate a plausible vote.
_SAFE_DEFAULT_R0 = {
    "casper": {"position": "UNCERTAIN", "conviction": 0.0,
               "key_evidence": [], "crux": "(no response)"},
    "melchior": {"verdict": "THESIS_HOLDS", "conviction": 0.0,
                 "key_evidence": [], "crux": "(no response)", "geometry": None},
    "balthasar": {"position": "CLEAR", "conviction": 0.0,
                  "key_evidence": [], "crux": "(no response)", "stance": "HOLD"},
}

_REBUTTAL_INSTRUCTION = (
    "=== REBUTTAL ROUND ===\n"
    "The three Round-0 openings above are FROZEN — they will not change underneath "
    "you, and your peers do NOT see your rebuttal. Re-emit your full vote: revise "
    "if a peer's point genuinely moves you, or hold. If you HOLD your opening call, "
    "you MUST state in your crux WHY you hold against the strongest opposing point. "
    "Silent assent is not allowed."
)

_SYNTHESIS_INSTRUCTION = (
    "=== SYNTHESIS — YOU ARE THE ARBITER ===\n"
    "Weigh the openings and the rebuttals above and emit your FINAL risk vote. Your "
    "stance, risk_action and geometry_veto are the council's binding outputs for this "
    "cycle; the grid economics stand or fall on Melchior's post-rebuttal verdict.\n"
    "YOUR stance IS THE COUNCIL'S CAPITAL MANDATE — it is enforced exactly as "
    "voted: DEPLOY runs Melchior's verdict unchanged; HOLD blocks any rebuild "
    "(nothing new is deployed, resting orders stay); STAND_ASIDE cancels buys and "
    "keeps sells working inventory off. Vote the stance from the market evidence "
    "(regime, tape verdict, drawdown, exposure-cap streak), not from habit — a "
    "stance held while conditions move is graded as wrong, the same as one "
    "flipped without cause.\n"
    "YOUR geometry_veto IS THE STRUCTURAL VETO. If Melchior's verdict is RECONFIGURE "
    "and you judge the rebuild unsafe, set geometry_veto=HOLD_GEOMETRY or RISK_BLOCK "
    "and the grid will hold (no rebuild this cycle). If you set geometry_veto=PROCEED "
    "on a RECONFIGURE while Casper's regime read objects (regime_action "
    "DEFER_STRUCTURAL or STAND_DOWN), you MUST fill override_justification, engaging "
    "Casper's cited reason on its merits — an un-justified proceed over a live "
    "objection is not honored and the grid holds. Leave override_justification null "
    "in every other case."
)


# --- native-vocabulary renderers (peer transcripts) ---

def _ev(vote: Any) -> str:
    return "; ".join(str(e) for e in (getattr(vote, "key_evidence", None) or [])[:5])


def _geo_str(geometry: Any) -> str:
    if geometry is None:
        return "none"
    return f"spacing:{geometry.target_spacing_pct},levels:{geometry.target_levels}"


def _fmt_casper(v: Any) -> str:
    return (f"[casper] regime={v.position} regime_action={v.regime_action} "
            f"conviction={v.conviction:.2f}\n  key_evidence: {_ev(v)}\n  crux: {v.crux}")


def _fmt_melchior(v: Any) -> str:
    return (f"[melchior] verdict={v.verdict} geometry={_geo_str(v.geometry)} "
            f"conviction={v.conviction:.2f}\n  key_evidence: {_ev(v)}\n  crux: {v.crux}")


def _fmt_balthasar(v: Any) -> str:
    return (f"[balthasar] stance={getattr(v, 'stance', 'HOLD')} "
            f"risk_action={v.risk_action} geometry_veto={v.geometry_veto} "
            f"conviction={v.conviction:.2f}\n  key_evidence: {_ev(v)}\n  crux: {v.crux}")


def _melchior_premise(cv: Any) -> str:
    """Casper's regime as a GIVEN FACT, label-only (no conviction/crux/evidence)."""
    return (
        f"The regime read for this cycle is {cv.position}/{cv.regime_action} "
        f"(treat it as a given fact for this turn). Given that regime, judge the "
        f"grid economics on their own merits — do not re-litigate the regime call."
    )


def _openings_for_balthasar(cv: Any, mv: Any) -> str:
    return (
        "=== ROUND-0 OPENINGS (your peers) ===\n"
        f"{_fmt_casper(cv)}\n\n{_fmt_melchior(mv)}\n\n"
        "Render your OPENING risk/survival read in light of these."
    )


def _frozen_transcript(cv: Any, mv: Any, bo: Any) -> str:
    return (
        "=== FROZEN ROUND-0 OPENINGS (all three council members) ===\n"
        f"{_fmt_casper(cv)}\n\n{_fmt_melchior(mv)}\n\n{_fmt_balthasar(bo)}"
    )


def _full_record(cv: Any, mv: Any, bo: Any, cr: Any, mr: Any) -> str:
    return (
        "=== ROUND-0 OPENINGS ===\n"
        f"{_fmt_casper(cv)}\n\n{_fmt_melchior(mv)}\n\n{_fmt_balthasar(bo)}\n\n"
        "=== ROUND-1 REBUTTALS ===\n"
        f"[casper rebuttal] regime={cr.position} regime_action={cr.regime_action}\n"
        f"  crux: {cr.crux}\n\n"
        f"[melchior rebuttal] verdict={mr.verdict} geometry={_geo_str(mr.geometry)}\n"
        f"  crux: {mr.crux}"
    )


# --- usage extraction (cached-token-aware) ---

def _usage_anthropic(response: Any) -> dict:
    """usage_details from an Anthropic-format response (Claude AND DeepSeek via the
    Anthropic-compat endpoint). Reads input/output and every cached-token field we
    might see — Anthropic's cache_read/cache_creation_input_tokens, and DeepSeek's
    prompt_cache_hit_tokens — so caching is observable for both Melchior and
    Balthasar."""
    u = getattr(response, "usage", None)
    if u is None:
        return {}

    def g(name: str) -> Optional[int]:
        v = getattr(u, name, None)
        return int(v) if isinstance(v, (int, float)) else None

    out: dict = {}
    for src, dst in (
        ("input_tokens", "input"),
        ("output_tokens", "output"),
        ("cache_read_input_tokens", "cache_read_input_tokens"),
        ("cache_creation_input_tokens", "cache_creation_input_tokens"),
        ("prompt_cache_hit_tokens", "cache_read_input_tokens"),
        ("prompt_cache_miss_tokens", "cache_miss_input_tokens"),
    ):
        val = g(src)
        if val is not None:
            out[dst] = val
    return out


def _usage_adk(event: Any) -> dict:
    """usage_details from a Casper ADK final event (best-effort — Gemini caching is
    deliberately OFF, so cached fields are typically absent/zero)."""
    um = getattr(event, "usage_metadata", None)
    if um is None:
        return {}

    def g(name: str) -> Optional[int]:
        v = getattr(um, name, None)
        return int(v) if isinstance(v, (int, float)) else None

    out: dict = {}
    for src, dst in (
        ("prompt_token_count", "input"),
        ("candidates_token_count", "output"),
        ("cached_content_token_count", "cache_read_input_tokens"),
    ):
        val = g(src)
        if val is not None:
            out[dst] = val
    return out


def _model_anthropic(response: Any) -> Optional[str]:
    """ACTUAL served model id from an Anthropic-format response (Claude AND DeepSeek
    via the Anthropic-compat endpoint). `.model` is what the provider billed/served —
    for DeepSeek this is the field _warn_if_fallback reads to catch a silent
    v4-pro -> v4-flash downgrade. Reused here so Melchior/Balthasar fingerprint on the
    real served model, not the configured default."""
    m = getattr(response, "model", None)
    return str(m) if m else None


def _model_adk(event: Any) -> Optional[str]:
    """Observed served-version string from a Casper ADK final event
    (LlmResponse.model_version, populated from Gemini's
    generate_content_response.model_version). Nullable and a version alias, so it is
    captured as SOFT snapshot metadata only — the hash uses the configured
    _CASPER_MODEL, never this."""
    mv = getattr(event, "model_version", None)
    return str(mv) if mv else None


def _dump(vote: Any) -> dict:
    try:
        return vote.model_dump()
    except Exception:  # noqa: BLE001
        return {"repr": repr(vote)[:500]}


# --- one traced + fail-safe seat call ---

def _seat_call(label: str, model: str, vendor: str, payload: dict,
               fn, usage_fn, model_fn,
               ) -> tuple[Optional[Any], Optional[str], Optional[BaseException]]:
    """Run one seat call inside its own trace_seat generation. Returns
    (vote, served_model, None) on success or (None, None, exc) on failure — never
    raises. On success the generation is updated with the vote output +
    usage_details (incl. cached tokens); on failure the error is recorded to the
    trace and returned.

    served_model is the ACTUAL model the provider served, pulled from the raw
    response by model_fn (best-effort — never raises; None if unavailable). This is
    additive capture of a value already in hand: it changes no behavior, only lets
    run_council fingerprint on the per-seat served model."""
    with tracing.trace_seat(label, model, vendor, payload) as gen:
        try:
            vote, raw = fn()
        except BaseException as e:  # noqa: BLE001 - fail-safe boundary
            log.warning("[council_v2] seat %s failed: %r", label, e)
            _LAST_RUN_USAGE.append(
                {"seat": label, "model": model, "vendor": vendor, "error": repr(e)[:200]})
            if gen is not None:
                try:
                    gen.update(level="ERROR", status_message=str(e)[:500])
                except Exception:  # noqa: BLE001
                    pass
            return None, None, e
        # Compute usage unconditionally (cheap attribute reads) so the cache-debug
        # breakdown works even when tracing is unavailable.
        usage = usage_fn(raw)
        # Served-model capture — best-effort, never breaks the call.
        try:
            served_model = model_fn(raw)
        except Exception as me:  # noqa: BLE001 - fingerprint capture is best-effort
            log.debug("[council_v2] served-model capture failed for %s: %r", label, me)
            served_model = None
        _LAST_RUN_USAGE.append(
            {"seat": label, "model": model, "vendor": vendor,
             "served_model": served_model, "usage": usage})
        if gen is not None:
            try:
                gen.update(output=_dump(vote), usage_details=usage)
            except Exception as ue:  # noqa: BLE001 - tracing never breaks the call
                log.debug("[council_v2] trace update failed for %s: %r", label, ue)
        return vote, served_model, None


# --- round_0 translators (parsed-vote dict shapes the orchestrator consumes) ---

def _geom_dict(geometry: Any) -> Optional[dict]:
    if geometry is None:
        return None
    return {"target_spacing_pct": geometry.target_spacing_pct,
            "target_levels": geometry.target_levels}


def _casper_r0(v: Any) -> dict:
    return {"position": v.position, "conviction": float(v.conviction),
            "key_evidence": list(v.key_evidence or []), "crux": v.crux,
            "regime_action": v.regime_action}


def _melchior_r0(v: Any) -> dict:
    # 'verdict' is the OPENING verdict (flight-recorder column). 'geometry' is set
    # here to the opening geometry and OVERWRITTEN with the post-rebuttal geometry
    # after Phase B, so the engine builds to Melchior's final call.
    return {"verdict": v.verdict, "conviction": float(v.conviction),
            "key_evidence": list(v.key_evidence or []), "crux": v.crux,
            "geometry": _geom_dict(v.geometry)}


def _balthasar_r0(v: Any) -> dict:
    # OPENING risk read. risk_action -> 'position' (the record builder reads
    # 'position' for balthasar). Synthesis values go to cons, not here.
    return {"position": v.risk_action, "conviction": float(v.conviction),
            "key_evidence": list(v.key_evidence or []), "crux": v.crux,
            "geometry_veto": v.geometry_veto,
            "stance": getattr(v, "stance", "HOLD")}


# --- stand-down (fail-safe) consensus ---

def _sanitize(text: Any) -> str:
    """Drop square brackets so a crux can't inject a spurious [HARD_RULE_TAG] into
    cons['reasoning'] (the dashboard parses notes with re.findall(r'\\[([A-Z_]+)\\]'))."""
    return str(text or "").replace("[", " ").replace("]", " ")


def _safe_hold_cons(trace_id: Optional[str], reason: str,
                    council_half: Optional[dict] = None) -> dict:
    """Consensus that resolves to a SAFE HOLD: THESIS_HOLDS -> MAINTAIN, CLEAR risk,
    permissive regime_action/geometry_veto (no fabricated veto). 'council_error' is
    a flag run_cycle can log; it is NOT a debate_records column, so _build_debate_record
    (which copies named keys only) never tries to insert it.

    '_fingerprint_council_half' carries whatever fingerprint inputs were assembled
    before stand-down (may be partial/None). The orchestrator reads it, folds in the
    floor half, and pops it before _build_debate_record — so it, too, never reaches a
    column."""
    return {
        "grid_verdict": "THESIS_HOLDS",
        # Stand-down stance is HOLD, not DEPLOY: a council that failed to
        # convene must not authorize new capital deployment — keep what is
        # resting, change nothing (matches the MAINTAIN safe hold).
        "stance": "HOLD",
        "risk_action": "CLEAR",
        "regime": "UNCERTAIN",
        "regime_action": "EXECUTE",
        "geometry_veto": "PROCEED",
        "debate_triggered": False,
        "deadlock": False,
        # No structural reconfigure on a stand-down, so there is nothing to veto or
        # justify; carried as None for column-shape symmetry with the live path.
        "override_justification": None,
        "reasoning": (
            "council stood down on a seat failure — safe hold (MAINTAIN/CLEAR), "
            "no structural change, no veto fabricated. " + _sanitize(reason)
        ),
        "trace_id": trace_id,
        "council_error": str(reason)[:500],
        "_fingerprint_council_half": council_half,
    }


def _bail(round_0: dict, round_1: dict, trace_id: Optional[str],
          where: str, err: BaseException,
          council_half: Optional[dict] = None) -> tuple[dict, dict, dict]:
    log.error("[council_v2] standing down at %s: %r", where, err)
    for a in ("casper", "melchior", "balthasar"):
        round_0.setdefault(a, dict(_SAFE_DEFAULT_R0[a]))
    return round_0, round_1, _safe_hold_cons(
        trace_id, f"{where}: {err!r}", council_half)


# --- public entry ---

def run_council(world_state: dict, cycle_id: str,
                trigger: str | None = None) -> tuple[dict, dict, dict]:
    """Convene the arbiter council over a frozen world_state. Returns
    (round_0, round_1, cons). Never raises — any seat failure resolves to a safe
    hold. See module docstring for the choreography and the downstream contracts.

    `trigger` (optional) is the convene reason ('scheduled' / 'startup' /
    'gate_wake:T16' / ...) — stamped into the trace metadata so Langfuse can
    slice gate-triggered cycles from clock cycles without a DB join."""
    round_0: dict = {}
    round_1: dict = {}
    _LAST_RUN_USAGE.clear()  # fresh per-seat usage capture for --cache-debug

    # This cycle's config fingerprint (offline, no vendor call). Two uses: (1) stamp
    # the trace root span so traces self-partition by config without a DB join; (2)
    # bound the per-agent recall Journal to the current config. Best-effort — a
    # failure here must never block the convene, so fall back to (None, None) (recall
    # then renders the empty sentinel, the trace carries a null version).
    try:
        cfg_version, cfg_snapshot = current_config_fingerprint()
    except Exception as e:  # noqa: BLE001 - fingerprint is best-effort context
        log.warning("[council_v2] config fingerprint compute failed: %r", e)
        cfg_version, cfg_snapshot = None, None

    with tracing.trace_cycle(cycle_id, metadata={
            "config_version": cfg_version, "config_snapshot": cfg_snapshot,
            "trigger": trigger or "unknown",
            "gate_triggered": bool(trigger and trigger.startswith("gate_wake"))}):
        trace_id = tracing.current_trace_id()
        # Mirror the trigger as a trace TAG — tags are the only trace field the
        # Metrics API can GROUP scores by (metadata is filter-only), so this is
        # what lets dashboard widgets slice council_changed/pnl by trigger class.
        tracing.set_trace_tags(trace_id, [f"trigger:{trigger or 'unknown'}"])

        # Melchior's persona is loaded HERE — inside the trace context, BEFORE any
        # vendor call. The asymmetry vs. Casper/Balthasar is deliberate: their
        # seat-callers fall back to load_persona(...) when handed persona=None, so a
        # None still resolves to the full .md. Melchior's seat-caller instead falls
        # back to a THIN _DEFAULT_PERSONA stub. So a Melchior persona-load failure
        # must be a HARD STAND-DOWN — the council does not convene this cycle — never
        # a silent degrade onto that stub. Mirror the blanked-key fail-safe exactly:
        # fill the three seats with SAFE_DEFAULTS (so the council-degradation detector
        # still trips on a sustained outage), keep round_1 empty, and return the
        # safe-hold cons with a council_error, BEFORE burning a single vendor call.
        try:
            melchior_persona = load_persona("melchior")
        except Exception as e:  # noqa: BLE001 - persona load failure => stand-down
            log.error(
                "[council_v2] melchior persona load failed (%r) — standing down this "
                "cycle (no thin-stub fallback in the council path)", e)
            for a in ("casper", "melchior", "balthasar"):
                round_0.setdefault(a, dict(_SAFE_DEFAULT_R0[a]))
            return round_0, round_1, _safe_hold_cons(
                trace_id, f"persona_load_failed:melchior:{e!r}")

        # ---- Stage-4 item-1 config fingerprint: COUNCIL HALF ----
        # Re-read Casper's and Balthasar's personas purely to HASH them. This is a
        # read of the SAME .md files their seat-callers load; the loaded text is NOT
        # passed into the seat-callers, so every byte each seat receives is
        # unchanged (they still self-load via their own persona=None fallback).
        # Best-effort: a failure here records a null hash and must NOT change
        # stand-down behavior — a real Casper/Balthasar persona failure still
        # surfaces inside its seat-caller and flows through _seat_call -> _bail
        # exactly as before. Melchior's text is already in hand (melchior_persona).
        persona_texts = {"melchior": melchior_persona}
        for _pname in ("casper", "balthasar"):
            try:
                persona_texts[_pname] = load_persona(_pname)
            except Exception as e:  # noqa: BLE001 - fingerprint read is best-effort
                log.warning(
                    "[council_v2] fingerprint persona re-read failed for %s: %r",
                    _pname, e)
                persona_texts[_pname] = None

        # council_half accumulates the fingerprint inputs council_v2 owns. The
        # orchestrator folds in the floor half (HARD_RULES + spacing/fee constants)
        # after run_council returns and composes the final hash + snapshot. Mutated
        # in place as seats succeed; passed to _bail so a stand-down still carries
        # whatever was assembled.
        council_half = {
            "persona_hashes": {
                n: (_fp_hash(t) if t else None) for n, t in persona_texts.items()
            },
            # config_version hashes the CONFIGURED model handles (operator decision):
            # config_version describes the configured setup, not what the provider
            # happened to serve. All three are the configured constants and never
            # change within a cycle.
            "models": {
                "casper": _CASPER_MODEL,
                "melchior": _MELCHIOR_MODEL,
                "balthasar": _BALTHASAR_MODEL,
            },
            # Health/observability only — the ACTUAL model each provider served this
            # cycle, filled per successful seat call. NEVER enters config_version (the
            # silent-downgrade signal must not churn the config hash); _warn_if_fallback
            # in the seat-callers is the live alarm, this is the recorded evidence.
            "served_models": {"casper": None, "melchior": None, "balthasar": None},
            "casper_model_version_observed": None,  # soft metadata; never in the hash
            "veto_mode": _VETO_MODE,
        }

        # ---- per-seat private recall (the "Journal") ----
        # Deterministic SQLite read (no model call, no vendor cost). Each seat sees
        # ONLY its own past calls scored by its own metric, injected as prompt text —
        # statelessness preserved (this is input, not agent-held memory). Computed
        # ONCE here and reused across that seat's opening + rebuttal/synthesis via
        # _with_recall, so the block is identical within the cycle and rides in
        # extra_context (AFTER each seat's cache breakpoint) — the cached stable
        # prefix is untouched. Best-effort: a recall failure degrades to no block for
        # that seat (a null suffix), never a stand-down.
        recall_block = {}
        for _seat in ("casper", "melchior", "balthasar"):
            try:
                recall_block[_seat] = get_agent_recall(_seat, cfg_version).get("block")
            except Exception as e:  # noqa: BLE001 - recall is best-effort context
                log.warning("[council_v2] recall read failed for %s: %r", _seat, e)
                recall_block[_seat] = None

        # ---- Phase A: openings (sequential) ----
        casper_open_ctx = _with_recall(recall_block["casper"], None)
        cv, casper_mv, err = _seat_call(
            "casper", _CASPER_MODEL, "google",
            {"world_state": world_state, "extra_context": casper_open_ctx},
            lambda: run_casper_with_meta(world_state, extra_context=casper_open_ctx),
            _usage_adk, _model_adk,
        )
        if err:
            return _bail(round_0, round_1, trace_id, "casper-open", err, council_half)
        round_0["casper"] = _casper_r0(cv)
        # Casper's served version is health metadata only (nullable/aliased); the hash
        # uses the configured _CASPER_MODEL already in council_half["models"].
        council_half["casper_model_version_observed"] = casper_mv
        council_half["served_models"]["casper"] = casper_mv

        premise = _melchior_premise(cv)
        melchior_open_ctx = _with_recall(recall_block["melchior"], premise)
        mv, melchior_served, err = _seat_call(
            "melchior", _MELCHIOR_MODEL, "deepseek",
            {"world_state": world_state, "extra_context": melchior_open_ctx},
            lambda: run_melchior_with_meta(
                world_state, persona=melchior_persona, extra_context=melchior_open_ctx),
            _usage_anthropic, _model_anthropic,
        )
        if err:
            return _bail(round_0, round_1, trace_id, "melchior-open", err, council_half)
        round_0["melchior"] = _melchior_r0(mv)
        council_half["served_models"]["melchior"] = melchior_served  # health only — NOT in config_version

        openings_b = _openings_for_balthasar(cv, mv)
        balthasar_open_ctx = _with_recall(recall_block["balthasar"], openings_b)
        bo, balthasar_served, err = _seat_call(
            "balthasar", _BALTHASAR_MODEL, "anthropic",
            {"world_state": world_state, "extra_context": balthasar_open_ctx},
            lambda: run_balthasar_with_meta(world_state, extra_context=balthasar_open_ctx),
            _usage_anthropic, _model_anthropic,
        )
        if err:
            return _bail(round_0, round_1, trace_id, "balthasar-open", err, council_half)
        round_0["balthasar"] = _balthasar_r0(bo)
        council_half["served_models"]["balthasar"] = balthasar_served  # health only — NOT in config_version

        # ---- Phase B: rebuttal (Casper + Melchior, vs the FROZEN openings) ----
        frozen = _frozen_transcript(cv, mv, bo)
        reb_ctx = frozen + "\n\n" + _REBUTTAL_INSTRUCTION
        casper_reb_ctx = _with_recall(recall_block["casper"], reb_ctx)
        melchior_reb_ctx = _with_recall(recall_block["melchior"], reb_ctx)

        cr, _cr_served, err = _seat_call(
            "casper:rebuttal", _CASPER_MODEL, "google",
            {"world_state": world_state, "extra_context": casper_reb_ctx, "phase": "rebuttal"},
            lambda: run_casper_with_meta(world_state, extra_context=casper_reb_ctx),
            _usage_adk, _model_adk,
        )
        if err:
            return _bail(round_0, round_1, trace_id, "casper-rebuttal", err, council_half)
        round_1["casper"] = {"position": cr.position, "crux": cr.crux}

        mr, _mr_served, err = _seat_call(
            "melchior:rebuttal", _MELCHIOR_MODEL, "deepseek",
            {"world_state": world_state, "extra_context": melchior_reb_ctx, "phase": "rebuttal"},
            lambda: run_melchior_with_meta(
                world_state, persona=melchior_persona, extra_context=melchior_reb_ctx),
            _usage_anthropic, _model_anthropic,
        )
        if err:
            return _bail(round_0, round_1, trace_id, "melchior-rebuttal", err, council_half)
        # 'position' holds the rebuttal LABEL (verdict) for both rebutters — the
        # record builder writes {agent}_r1_position from round_1[agent]['position'].
        round_1["melchior"] = {"position": mr.verdict, "crux": mr.crux}

        # Geometry follows the POST-REBUTTAL verdict: overwrite the opening geometry
        # so _final_consensus / hard-rule #8 build to Melchior's FINAL call. The
        # GridVote model_validator guarantees geometry-present-iff-RECONFIGURE on the
        # returned object, and the seat-caller retried once on any violation, so this
        # is internally consistent by construction (RECONFIGURE => geometry not None).
        round_0["melchior"]["geometry"] = _geom_dict(mr.geometry)

        # ---- Phase C: synthesis (Balthasar) — his RiskVote IS the final call ----
        synth_ctx = _full_record(cv, mv, bo, cr, mr) + "\n\n" + _SYNTHESIS_INSTRUCTION
        balthasar_synth_ctx = _with_recall(recall_block["balthasar"], synth_ctx)
        bs, _bs_served, err = _seat_call(
            "balthasar:synthesis", _BALTHASAR_MODEL, "anthropic",
            {"world_state": world_state, "extra_context": balthasar_synth_ctx, "phase": "synthesis"},
            lambda: run_balthasar_with_meta(world_state, extra_context=balthasar_synth_ctx),
            _usage_anthropic, _model_anthropic,
        )
        if err:
            return _bail(round_0, round_1, trace_id, "balthasar-synthesis", err, council_half)

        # ---- consensus ----
        casper_changed = cr.position != cv.position
        melchior_changed = mr.verdict != mv.verdict
        debate_triggered = bool(casper_changed or melchior_changed)
        shift = ("rebuttal shifted a stance" if debate_triggered
                 else "all stances held through rebuttal")

        # ---- in-council STRUCTURAL VETO (Stage-4 item 2a) ----
        # Balthasar is the arbiter; his synthesis geometry_veto now CARRIES the
        # structural veto that used to live in orchestrator hard-rule 0d. The veto
        # only bites a RECONFIGURE (the lone structural verdict — THESIS_HOLDS and
        # NO_PROFITABLE_GRID change no geometry). Three cases:
        #   * geometry_veto HOLD_GEOMETRY / RISK_BLOCK  -> arbiter holds the
        #     reconfigure: emit THESIS_HOLDS so the orchestrator maps it to MAINTAIN
        #     (no rebuild this cycle). This is the old rule-0d MAINTAIN coercion,
        #     decided in-council instead of after the fact.
        #   * geometry_veto PROCEED, Casper objects (regime_action DEFER_STRUCTURAL
        #     / STAND_DOWN) -> the arbiter is overriding a live regime objection and
        #     MUST justify it (override_justification). A justified proceed stands;
        #     an UN-justified proceed does not clear the bar — the objection stands
        #     and we hold (THESIS_HOLDS). That fallback is the conservative old
        #     rule-0d outcome, so removing rule 0d never loosens safety.
        #   * geometry_veto PROCEED, no Casper objection -> reconfigure stands, no
        #     justification needed.
        # The flight recorder is untouched: round_0['melchior'] still records what
        # Melchior actually wanted (RECONFIGURE + geometry); the veto outcome shows
        # up in grid_verdict / geometry_veto / final_grid_action.
        effective_verdict = mr.verdict
        override_justification: Optional[str] = None
        veto_note = ""
        casper_objects = cr.regime_action in ("DEFER_STRUCTURAL", "STAND_DOWN")
        if mr.verdict == "RECONFIGURE":
            if bs.geometry_veto in ("HOLD_GEOMETRY", "RISK_BLOCK"):
                effective_verdict = "THESIS_HOLDS"
                veto_note = (
                    f" arbiter veto: geometry_veto={bs.geometry_veto} holds the "
                    f"reconfigure (grid stays MAINTAIN)."
                )
                log.info("[council_v2] arbiter veto held RECONFIGURE via %s",
                         bs.geometry_veto)
            elif casper_objects:
                oj = (getattr(bs, "override_justification", None) or "").strip()
                if oj:
                    override_justification = _sanitize(oj)[:500]
                    veto_note = (
                        f" arbiter proceeds over Casper {cr.regime_action} with "
                        f"justification."
                    )
                    log.info(
                        "[council_v2] arbiter PROCEEDed over Casper %s with "
                        "justification — reconfigure stands", cr.regime_action)
                else:
                    effective_verdict = "THESIS_HOLDS"
                    veto_note = (
                        f" arbiter proceeded over Casper {cr.regime_action} WITHOUT "
                        f"justification — override not honored, reconfigure held."
                    )
                    log.warning(
                        "[council_v2] arbiter PROCEEDed over Casper %s with NO "
                        "override_justification — holding reconfigure (THESIS_HOLDS)",
                        cr.regime_action)

        cons = {
            "grid_verdict": effective_verdict,    # POST-REBUTTAL verdict, AFTER veto
            # The arbiter's capital mandate (Fix 3). Translated
            # deterministically by enforce_hard_rules: DEPLOY -> verdict
            # pipeline unchanged; HOLD -> no rebuild; STAND_ASIDE -> no buys,
            # keep sells (risk_action floored at PAUSE_LONGS).
            "stance": getattr(bs, "stance", "HOLD"),
            "risk_action": bs.risk_action,        # Balthasar synthesis
            "regime": cr.position,                # Casper POST-REBUTTAL
            "regime_action": cr.regime_action,    # Casper POST-REBUTTAL (record-only)
            "geometry_veto": bs.geometry_veto,    # Balthasar synthesis (record-only)
            # The arbiter's justification for proceeding over a live regime
            # objection (None otherwise). The orchestrator copies it to the
            # debate_records override_justification column.
            "override_justification": override_justification,
            "debate_triggered": debate_triggered,
            "deadlock": False,
            "reasoning": (
                f"arbiter synthesis — stance={getattr(bs, 'stance', 'HOLD')}, "
                f"regime={cr.position}/{cr.regime_action}, "
                f"grid_verdict={mr.verdict}->{effective_verdict}, "
                f"risk={bs.risk_action}/{bs.geometry_veto}; "
                f"{shift}.{veto_note} {_sanitize(bs.crux)}"
            ),
            "trace_id": trace_id,
            # Stage-4 item-1: council half of the config fingerprint (persona
            # hashes, per-seat served models, veto mode). The orchestrator folds in
            # the floor half, composes config_version/config_snapshot, then pops
            # this key before _build_debate_record — so it never reaches a column.
            "_fingerprint_council_half": council_half,
        }
        log.info(
            "[council_v2] %s: stance=%s regime=%s/%s grid=%s(eff=%s) risk=%s/%s debate=%s",
            cycle_id, getattr(bs, "stance", "HOLD"), cr.position, cr.regime_action,
            mr.verdict, effective_verdict,
            bs.risk_action, bs.geometry_veto, debate_triggered,
        )
        return round_0, round_1, cons


# --- standalone runner (no service restart needed) ---

if __name__ == "__main__":
    import argparse
    import json as _json

    from dotenv import load_dotenv

    load_dotenv()  # one .env load for Melchior/Balthasar (os.environ) + Langfuse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Run the arbiter council against a frozen or freshly-built "
                    "world_state — the test path that needs no service restart.")
    parser.add_argument(
        "--json", help="path to a world_state JSON file (else build one once)")
    parser.add_argument("--cycle-id", default="cyc_standalone")
    parser.add_argument(
        "--cache-debug", action="store_true",
        help="ON-DEMAND cache diagnostics: print the per-seat cached-token "
             "breakdown (cache_creation/cache_read) returned by each call. Not "
             "wired into the live path. Deeper byte-level cache inspection can be "
             "had by adding the Anthropic prompt-caching beta header to the "
             "Balthasar caller, but that stays off by default.")
    args = parser.parse_args()

    if args.json:
        with open(args.json) as f:
            ws = _json.load(f)
    else:
        # Local import — orchestrator imports council_v2, so importing it at module
        # scope would be circular. Only the standalone path needs it.
        from magi.orchestrator import build_world_state
        ws = build_world_state()

    if args.cache_debug:
        # Guard the cache-positive path the live run relies on: the Balthasar tool
        # schema must serialize deterministically, or a phantom tools_changed would
        # silently cost the cache. Confirm two serializations are byte-identical.
        from magi.agents.schema_tools import schema_for_tool
        from magi.agents.schemas import RiskVote
        a = _json.dumps(schema_for_tool(RiskVote), sort_keys=True)
        b = _json.dumps(schema_for_tool(RiskVote), sort_keys=True)
        print(f"[cache-debug] RiskVote tool-schema serialization stable: {a == b}")

    r0, r1, cons = run_council(ws, args.cycle_id)
    print(_json.dumps({"round_0": r0, "round_1": r1, "cons": cons},
                      indent=2, default=str))

    # Stage-4 item-1: show the council half of the config fingerprint that
    # run_council now rides out on cons (the orchestrator folds in the floor half +
    # composes config_version/config_snapshot — that step does NOT run on this
    # standalone path, so config_version is expected absent here).
    half = cons.get("_fingerprint_council_half") or {}
    print("\n[fingerprint] council-half carried on cons (orchestrator folds in the "
          "floor half + hash downstream):")
    print(f"  persona_hashes: {half.get('persona_hashes')}")
    print(f"  models (CONFIGURED handles — what config_version hashes): "
          f"{half.get('models')}")
    print(f"  served_models (health only, snapshot-only, NOT in the hash): "
          f"{half.get('served_models')}")
    print(f"  casper_model_version_observed (soft, snapshot-only): "
          f"{half.get('casper_model_version_observed')}")
    print(f"  veto_mode: {half.get('veto_mode')}")

    if args.cache_debug:
        print("\n[cache-debug] per-seat usage (cache_creation / cache_read are the "
              "cache-positive signals); served_model = ACTUAL model the provider "
              "served this call:")
        for rec in _LAST_RUN_USAGE:
            if "error" in rec:
                print(f"  {rec['seat']:22s} [{rec['vendor']}/{rec['model']}] ERROR: {rec['error']}")
                continue
            u = rec.get("usage") or {}
            cc = u.get("cache_creation_input_tokens")
            cr = u.get("cache_read_input_tokens")
            print(f"  {rec['seat']:22s} [{rec['vendor']}/{rec['model']}] "
                  f"served={rec.get('served_model')} "
                  f"input={u.get('input')} output={u.get('output')} "
                  f"cache_creation={cc} cache_read={cr}")

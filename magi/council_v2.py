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

import logging
from typing import Any, Optional

from magi.agents.casper_gemini import run_casper_with_meta
from magi.agents.melchior_deepseek import run_melchior_with_meta
from magi.agents.balthasar_claude import run_balthasar_with_meta
from magi.agents.personas import load_persona
from magi.agents import tracing

log = logging.getLogger(__name__)

# Explicit per-seat attribution for the trace. Model strings match the seat
# callers' defaults; vendor is what the bytes ACTUALLY hit (Melchior speaks the
# Anthropic SDK to DeepSeek's endpoint, so vendor='deepseek', never 'anthropic').
_CASPER_MODEL = "gemini-2.5-flash"
_MELCHIOR_MODEL = "deepseek-v4-pro"
_BALTHASAR_MODEL = "claude-sonnet-4-6"

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
                  "key_evidence": [], "crux": "(no response)"},
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
    "risk_action and geometry_veto are the council's binding risk outputs for this "
    "cycle; the grid economics stand or fall on Melchior's post-rebuttal verdict."
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
    return (f"[balthasar] risk_action={v.risk_action} geometry_veto={v.geometry_veto} "
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


def _dump(vote: Any) -> dict:
    try:
        return vote.model_dump()
    except Exception:  # noqa: BLE001
        return {"repr": repr(vote)[:500]}


# --- one traced + fail-safe seat call ---

def _seat_call(label: str, model: str, vendor: str, payload: dict,
               fn, usage_fn) -> tuple[Optional[Any], Optional[BaseException]]:
    """Run one seat call inside its own trace_seat generation. Returns
    (vote, None) on success or (None, exc) on failure — never raises. On success
    the generation is updated with the vote output + usage_details (incl. cached
    tokens); on failure the error is recorded to the trace and returned."""
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
            return None, e
        # Compute usage unconditionally (cheap attribute reads) so the cache-debug
        # breakdown works even when tracing is unavailable.
        usage = usage_fn(raw)
        _LAST_RUN_USAGE.append(
            {"seat": label, "model": model, "vendor": vendor, "usage": usage})
        if gen is not None:
            try:
                gen.update(output=_dump(vote), usage_details=usage)
            except Exception as ue:  # noqa: BLE001 - tracing never breaks the call
                log.debug("[council_v2] trace update failed for %s: %r", label, ue)
        return vote, None


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
            "geometry_veto": v.geometry_veto}


# --- stand-down (fail-safe) consensus ---

def _sanitize(text: Any) -> str:
    """Drop square brackets so a crux can't inject a spurious [HARD_RULE_TAG] into
    cons['reasoning'] (the dashboard parses notes with re.findall(r'\\[([A-Z_]+)\\]'))."""
    return str(text or "").replace("[", " ").replace("]", " ")


def _safe_hold_cons(trace_id: Optional[str], reason: str) -> dict:
    """Consensus that resolves to a SAFE HOLD: THESIS_HOLDS -> MAINTAIN, CLEAR risk,
    permissive regime_action/geometry_veto (no fabricated veto). 'council_error' is
    a flag run_cycle can log; it is NOT a debate_records column, so _build_debate_record
    (which copies named keys only) never tries to insert it."""
    return {
        "grid_verdict": "THESIS_HOLDS",
        "risk_action": "CLEAR",
        "regime": "UNCERTAIN",
        "regime_action": "EXECUTE",
        "geometry_veto": "PROCEED",
        "debate_triggered": False,
        "deadlock": False,
        "reasoning": (
            "council stood down on a seat failure — safe hold (MAINTAIN/CLEAR), "
            "no structural change, no veto fabricated. " + _sanitize(reason)
        ),
        "trace_id": trace_id,
        "council_error": str(reason)[:500],
    }


def _bail(round_0: dict, round_1: dict, trace_id: Optional[str],
          where: str, err: BaseException) -> tuple[dict, dict, dict]:
    log.error("[council_v2] standing down at %s: %r", where, err)
    for a in ("casper", "melchior", "balthasar"):
        round_0.setdefault(a, dict(_SAFE_DEFAULT_R0[a]))
    return round_0, round_1, _safe_hold_cons(trace_id, f"{where}: {err!r}")


# --- public entry ---

def run_council(world_state: dict, cycle_id: str) -> tuple[dict, dict, dict]:
    """Convene the arbiter council over a frozen world_state. Returns
    (round_0, round_1, cons). Never raises — any seat failure resolves to a safe
    hold. See module docstring for the choreography and the downstream contracts."""
    round_0: dict = {}
    round_1: dict = {}
    _LAST_RUN_USAGE.clear()  # fresh per-seat usage capture for --cache-debug

    with tracing.trace_cycle(cycle_id):
        trace_id = tracing.current_trace_id()

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

        # ---- Phase A: openings (sequential) ----
        cv, err = _seat_call(
            "casper", _CASPER_MODEL, "google",
            {"world_state": world_state, "extra_context": None},
            lambda: run_casper_with_meta(world_state),
            _usage_adk,
        )
        if err:
            return _bail(round_0, round_1, trace_id, "casper-open", err)
        round_0["casper"] = _casper_r0(cv)

        premise = _melchior_premise(cv)
        mv, err = _seat_call(
            "melchior", _MELCHIOR_MODEL, "deepseek",
            {"world_state": world_state, "extra_context": premise},
            lambda: run_melchior_with_meta(
                world_state, persona=melchior_persona, extra_context=premise),
            _usage_anthropic,
        )
        if err:
            return _bail(round_0, round_1, trace_id, "melchior-open", err)
        round_0["melchior"] = _melchior_r0(mv)

        openings_b = _openings_for_balthasar(cv, mv)
        bo, err = _seat_call(
            "balthasar", _BALTHASAR_MODEL, "anthropic",
            {"world_state": world_state, "extra_context": openings_b},
            lambda: run_balthasar_with_meta(world_state, extra_context=openings_b),
            _usage_anthropic,
        )
        if err:
            return _bail(round_0, round_1, trace_id, "balthasar-open", err)
        round_0["balthasar"] = _balthasar_r0(bo)

        # ---- Phase B: rebuttal (Casper + Melchior, vs the FROZEN openings) ----
        frozen = _frozen_transcript(cv, mv, bo)
        reb_ctx = frozen + "\n\n" + _REBUTTAL_INSTRUCTION

        cr, err = _seat_call(
            "casper:rebuttal", _CASPER_MODEL, "google",
            {"world_state": world_state, "extra_context": reb_ctx, "phase": "rebuttal"},
            lambda: run_casper_with_meta(world_state, extra_context=reb_ctx),
            _usage_adk,
        )
        if err:
            return _bail(round_0, round_1, trace_id, "casper-rebuttal", err)
        round_1["casper"] = {"position": cr.position, "crux": cr.crux}

        mr, err = _seat_call(
            "melchior:rebuttal", _MELCHIOR_MODEL, "deepseek",
            {"world_state": world_state, "extra_context": reb_ctx, "phase": "rebuttal"},
            lambda: run_melchior_with_meta(
                world_state, persona=melchior_persona, extra_context=reb_ctx),
            _usage_anthropic,
        )
        if err:
            return _bail(round_0, round_1, trace_id, "melchior-rebuttal", err)
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
        bs, err = _seat_call(
            "balthasar:synthesis", _BALTHASAR_MODEL, "anthropic",
            {"world_state": world_state, "extra_context": synth_ctx, "phase": "synthesis"},
            lambda: run_balthasar_with_meta(world_state, extra_context=synth_ctx),
            _usage_anthropic,
        )
        if err:
            return _bail(round_0, round_1, trace_id, "balthasar-synthesis", err)

        # ---- consensus ----
        casper_changed = cr.position != cv.position
        melchior_changed = mr.verdict != mv.verdict
        debate_triggered = bool(casper_changed or melchior_changed)
        shift = ("rebuttal shifted a stance" if debate_triggered
                 else "all stances held through rebuttal")
        cons = {
            "grid_verdict": mr.verdict,          # Melchior POST-REBUTTAL verdict
            "risk_action": bs.risk_action,        # Balthasar synthesis
            "regime": cr.position,                # Casper POST-REBUTTAL
            "regime_action": cr.regime_action,    # Casper POST-REBUTTAL
            "geometry_veto": bs.geometry_veto,    # Balthasar synthesis
            "debate_triggered": debate_triggered,
            "deadlock": False,
            "reasoning": (
                f"arbiter synthesis — regime={cr.position}/{cr.regime_action}, "
                f"grid_verdict={mr.verdict}, risk={bs.risk_action}/{bs.geometry_veto}; "
                f"{shift}. {_sanitize(bs.crux)}"
            ),
            "trace_id": trace_id,
        }
        log.info(
            "[council_v2] %s: regime=%s/%s grid=%s risk=%s/%s debate=%s",
            cycle_id, cr.position, cr.regime_action, mr.verdict,
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

    if args.cache_debug:
        print("\n[cache-debug] per-seat usage (cache_creation / cache_read are the "
              "cache-positive signals):")
        for rec in _LAST_RUN_USAGE:
            if "error" in rec:
                print(f"  {rec['seat']:22s} [{rec['vendor']}/{rec['model']}] ERROR: {rec['error']}")
                continue
            u = rec.get("usage") or {}
            cc = u.get("cache_creation_input_tokens")
            cr = u.get("cache_read_input_tokens")
            print(f"  {rec['seat']:22s} [{rec['vendor']}/{rec['model']}] "
                  f"input={u.get('input')} output={u.get('output')} "
                  f"cache_creation={cc} cache_read={cr}")

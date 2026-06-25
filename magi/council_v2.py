"""council_v2.py — the BLIND-REVIEW council for MAGI (redesign 2026-06-24).

Replaces the six-call arbiter relay with a flat, equal-seats blind review. The
three seats are EQUALS (governing principle P1): no arbiter, no privileged seat,
none that sees more or decides more than the others. The decision is the council's
own — a deterministic social-choice aggregate of independent proposals, never a
rule's pick.

One cycle:
  Phase 1 — Isolated proposal (3 parallel). Each seat gets the IDENTICAL scaffold
    (persona as a reasoning LENS only, rendered world_state, shared council-ledger
    block) and NO peer context, and returns one CandidateDecision over the single
    unified action space.
  Phase 2 — Anonymized cross-review (3 parallel). Authorship is stripped and every
    candidate normalized to one template; the set is shuffled to labels A/B/C under
    a per-cycle seed. Each seat returns a Ranking of A/B/C. No seat sees another
    seat's ranking.
  Phase 3 — Aggregate (deterministic, no LLM): Condorcet check, Borda fallback,
    flat (conviction recorded, never weighted). A clear winner IS the decision.
  Reconciliation — if no stable winner, ONE more round: show each seat (authorship
    hidden) that the council split and let it revise its candidate; re-aggregate.
  NO_CONSENSUS — if still no winner, the decision is NO_CONSENSUS: the council
    declines to mandate a change, so nothing changes. A first-class decision value,
    never an error path and never a fabricated pick.

Public entry:
    run_council(world_state, cycle_id, trigger=None) -> (round_0, round_1, cons)

returning the EXACT shapes orchestrator._build_debate_record and enforce_hard_rules
consume. The single winning action is translated into the THREE cons axes the
contract gates on (grid_verdict / stance / risk_action); the old record-only axes
(regime, regime_action, geometry_veto, override_justification) are emitted as None
and their columns read NULL — the council no longer outputs a regime, and there is
no arbiter veto. The aggregation detail (decision, authorship-free vote multiset,
consensus class, reconciled flag) lives in cons['council_json'], NOT in the gating
contract — it is the council's OWN memory, written to the additive council_json
column and read back by get_council_ledger.

Seat-failure handling is minimal and honest: each seat call retries once; a seat
that still fails is a non-responder (NO fabricated vote, NO degradation sentinel).
If a clear tally remains from the responders, the council proceeds; otherwise the
decision is NO_CONSENSUS. round_1 = {} (blind review has no rebuttal round).

Tracing: this module owns the Langfuse trace. The cycle is wrapped in
trace_cycle(cycle_id); each seat call is recorded as a trace_seat generation with
EXPLICIT per-seat model/vendor attribution (Melchior is deepseek even though it
speaks the Anthropic SDK).
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from magi.agents import aggregate as agg
from magi.agents import tracing
from magi.agents.anonymize import anonymize
from magi.agents.personas import load_persona
from magi.agents.seats import MODELS, VENDORS, propose, review
from database import get_council_ledger

log = logging.getLogger(__name__)

# Per-seat model handles — the SINGLE source of truth is seats.MODELS (used for both
# the live calls and the config fingerprint, so the two can never drift). Balthasar
# is claude-haiku-4-5 in the redesign: with no synthesizer/arbiter seat the premium
# tier is not justified (cost-matched equals).
_CASPER_MODEL = MODELS["casper"]
_MELCHIOR_MODEL = MODELS["melchior"]
_BALTHASAR_MODEL = MODELS["balthasar"]

# Config-fingerprint marker for the council mode. Changing this value (from the
# arbiter relay's "in_debate") deliberately bumps config_version at cutover, so
# pre/post-redesign history partitions cleanly and the ledger never recalls a
# pre-redesign cycle as if it were a blind-review one.
_VETO_MODE = "none_blind_review"


def _fp_hash(text: str) -> str:
    """Short, stable content hash of a persona text for the config fingerprint."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def current_config_council_half() -> dict:
    """The deterministic COUNCIL HALF of the config fingerprint, computed with NO
    vendor call. _compose_config_fingerprint folds this with the floor half to derive
    config_version — the boundary the council ledger filters by. Identical in shape to
    the half run_council assembles on the write path: configured model handles (NOT
    served ids), persona hashes from the SAME .md files the seats load, and the
    council-mode marker."""
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
        "models": {"casper": _CASPER_MODEL, "melchior": _MELCHIOR_MODEL,
                   "balthasar": _BALTHASAR_MODEL},
        "casper_model_version_observed": None,  # soft metadata; never in the hash
        "veto_mode": _VETO_MODE,
    }


def current_config_fingerprint() -> tuple[Optional[str], Optional[dict]]:
    """The full (config_version, config_snapshot) for the live configured setup,
    computed OFFLINE. Folds current_config_council_half() through the orchestrator's
    _compose_config_fingerprint (the SAME hasher the write path uses), so the version
    here equals the version stamped on rows. The orchestrator import is LAZY to break
    the orchestrator<->council_v2 cycle. Never raises here — callers wrap it."""
    from magi.orchestrator import _compose_config_fingerprint  # lazy: break import cycle
    return _compose_config_fingerprint(
        {"_fingerprint_council_half": current_config_council_half()})


# --- usage / served-model extraction (cached-token-aware), for tracing ---

def _usage_anthropic(response: Any) -> dict:
    """usage_details from an Anthropic-format response (Claude AND DeepSeek via the
    Anthropic-compat endpoint), including cached-token fields for both vendors."""
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
    """usage_details from a Casper ADK final event (Gemini caching is off, so cached
    fields are typically absent)."""
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
    m = getattr(response, "model", None)
    return str(m) if m else None


def _model_adk(event: Any) -> Optional[str]:
    mv = getattr(event, "model_version", None)
    return str(mv) if mv else None


def _usage_for(seat: str, raw: Any) -> dict:
    return _usage_adk(raw) if seat == "casper" else _usage_anthropic(raw)


def _served_for(seat: str, raw: Any) -> Optional[str]:
    try:
        return _model_adk(raw) if seat == "casper" else _model_anthropic(raw)
    except Exception:  # noqa: BLE001 - best-effort
        return None


def _dump(obj: Any) -> dict:
    try:
        return obj.model_dump()
    except Exception:  # noqa: BLE001
        return {"repr": repr(obj)[:500]}


def _sanitize(text: Any) -> str:
    """Drop square brackets so a rationale can't inject a spurious [HARD_RULE_TAG]
    into cons['reasoning'] (the dashboard parses notes with re.findall(r'\\[([A-Z_]+)\\]'))."""
    return str(text or "").replace("[", " ").replace("]", " ")


_SEATS = ("casper", "melchior", "balthasar")


# --- parallel seat execution (Phase 1 / Phase 2), with one retry + tracing ---

def _gather(call, phase_label: str) -> tuple[dict, dict]:
    """Run all three seats CONCURRENTLY (P1: equal, simultaneous — no sequencing that
    could privilege a seat). `call(seat)` returns (validated_obj, raw). Each seat
    retries ONCE on any failure; a seat that still fails is a non-responder (no
    fabricated vote). Returns (objs, raws) keyed by seat for the responders only, and
    records each seat's outcome as a trace_seat generation in the main thread (correct
    nesting under trace_cycle)."""
    objs: dict[str, Any] = {}
    raws: dict[str, Any] = {}
    errs: dict[str, BaseException] = {}

    def work(seat: str):
        try:
            return ("ok", call(seat))
        except BaseException as e1:  # noqa: BLE001 - retry-once boundary
            log.warning("[council_v2] %s seat %s attempt 1 failed: %r — retrying once",
                        phase_label, seat, e1)
            try:
                return ("ok", call(seat))
            except BaseException as e2:  # noqa: BLE001
                log.warning("[council_v2] %s seat %s failed twice: %r — non-responder",
                            phase_label, seat, e2)
                return ("err", e2)

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(work, s): s for s in _SEATS}
        for fut in as_completed(futs):
            seat = futs[fut]
            status, payload = fut.result()
            if status == "ok":
                objs[seat], raws[seat] = payload
            else:
                errs[seat] = payload

    for seat in _SEATS:
        _trace_seat_post(seat, phase_label, objs.get(seat), raws.get(seat), errs.get(seat))
    return objs, raws


def _trace_seat_post(seat: str, phase_label: str, obj: Any, raw: Any,
                     err: Optional[BaseException]) -> None:
    """Emit one seat's generation span after the parallel call returns. Fire-and-forget
    — tracing never breaks the council."""
    try:
        with tracing.trace_seat(seat, MODELS[seat], VENDORS[seat],
                                {"phase": phase_label}) as gen:
            if gen is None:
                return
            try:
                if obj is not None:
                    gen.update(output=_dump(obj), usage_details=_usage_for(seat, raw))
                elif err is not None:
                    gen.update(level="ERROR", status_message=str(err)[:500])
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 - tracing must never raise into the council
        pass


# --- Phase 2 + 3: rank the anonymized candidates and aggregate ---

def _decide(world_state: dict, candidates: dict[str, Any],
            ledger_block: Optional[str], cycle_id: str, suffix: str) -> Optional[dict]:
    """Anonymize `candidates`, run Phase-2 cross-review, aggregate. Returns the
    aggregate result dict (winner not None) on a clear/Borda decision, or None when
    no stable winner exists (cycle / tie / too few rankings) — the caller reconciles
    or declares NO_CONSENSUS. No deterministic tiebreak is applied here (P3)."""
    anon_block, label_to_seat = anonymize(candidates, cycle_id + suffix)
    label_to_candidate = {label: candidates[seat] for label, seat in label_to_seat.items()}

    rankings_by_seat, _raws = _gather(
        lambda seat: review(seat, world_state, anon_block, ledger_block),
        phase_label="review" + suffix)
    rankings = list(rankings_by_seat.values())
    if len(rankings) < 2:
        log.warning("[council_v2] only %d ranking(s) returned%s — no tally", len(rankings), suffix)
        return None

    result = agg.aggregate(rankings, label_to_candidate)
    if result["winner"] is None:
        log.info("[council_v2] no stable winner%s (method=%s)", suffix, result["method"])
        return None
    result["label_to_seat"] = label_to_seat
    return result


# --- round_0 projection + cons construction ---

def _own_r0(candidates: dict[str, Any]) -> dict[str, dict]:
    """Record each responding seat's OWN Phase-1 proposal on its legacy flight-recorder
    axis (honest spread; the authorship-free multiset lives in council_json):
      * casper    -> position = its proposed action
      * melchior  -> verdict  = its action's grid_verdict; geometry None for now
      * balthasar -> position = its action's risk_action
    A non-responding seat is simply absent (its columns read NULL — no sentinel)."""
    r0: dict[str, dict] = {}
    for seat, c in candidates.items():
        # `action` is each seat's RAW proposed action — the lossless record the
        # symmetric forward-realized seat grader needs (the verdict/risk projections
        # below are lossy). Recorded per responding seat only; a non-responder is
        # absent and its *_r0_action column reads NULL (ungraded, not wrong).
        base = {"conviction": float(c.conviction),
                "key_evidence": list(c.key_evidence or []), "crux": c.rationale,
                "action": c.action}
        if seat == "melchior":
            r0["melchior"] = {"verdict": agg.verdict_for(c.action), "geometry": None, **base}
        elif seat == "casper":
            r0["casper"] = {"position": c.action, **base}
        elif seat == "balthasar":
            r0["balthasar"] = {"position": agg.risk_for(c.action), **base}
    return r0


def _project_round0(candidates: dict[str, Any], winner: Any) -> dict[str, dict]:
    """round_0 for a decided cycle: each seat's own proposal, PLUS the engine's
    geometry channel. The engine builds from round_0['melchior']['geometry'], so the
    WINNER's build geometry must land there regardless of which seat authored the win
    (mirrors how hard-rule 8 already overwrites that slot from the scorer). If Melchior
    did not respond but the winner is RECONFIGURE, a minimal Melchior entry is created
    to carry the build geometry."""
    r0 = _own_r0(candidates)
    win_geo = agg.geom_dict(getattr(winner, "geometry", None))
    if "melchior" in r0:
        r0["melchior"]["geometry"] = win_geo
    else:
        r0["melchior"] = {
            "verdict": agg.verdict_for(winner.action), "geometry": win_geo,
            "conviction": float(winner.conviction),
            "key_evidence": list(winner.key_evidence or []), "crux": winner.rationale,
        }
    return r0


def _base_cons(grid_verdict: str, stance: str, risk_action: str,
               council_json: dict, reasoning: str, deadlock: bool,
               reconciled: bool, trace_id: Optional[str],
               council_half: dict, council_error: Optional[str] = None) -> dict:
    """Assemble the cons dict. Only grid_verdict / stance / risk_action gate behavior;
    the record-only legacy axes are None (the council no longer produces a regime, and
    there is no arbiter veto), so their columns read NULL. council_json carries the
    council's own memory; _fingerprint_council_half is folded + popped by the
    orchestrator before _build_debate_record."""
    cons = {
        "grid_verdict": grid_verdict,
        "stance": stance,
        "risk_action": risk_action,
        # record-only legacy axes — emitted None, columns read NULL harmlessly
        "regime": None,
        "regime_action": None,
        "geometry_veto": None,
        "override_justification": None,
        # honest: "debate" now means the council had to deliberate a second round
        "debate_triggered": bool(reconciled),
        "deadlock": bool(deadlock),
        "hard_rule_overrides": [],
        "reasoning": reasoning,
        "trace_id": trace_id,
        "council_json": json.dumps(council_json),
        "_fingerprint_council_half": council_half,
    }
    if council_error is not None:
        # Only set on a genuine convene CRASH, so the orchestrator skips stance-persist
        # ("a crashed council is not a stance decision"). NOT set for a designed
        # NO_CONSENSUS — that is a real decision and DOES set the standing stance.
        cons["council_error"] = str(council_error)[:500]
    return cons


def _winner_cons(candidates: dict[str, Any], winner: Any, method: str,
                 multiset: str, reconciled: bool, trace_id: Optional[str],
                 council_half: dict) -> dict:
    f = agg.consensus_fields(winner)
    council_json = {
        "decision": winner.action,
        "vote_multiset": multiset,
        "consensus": "reconciled" if reconciled else "clear",
        "reconciled": bool(reconciled),
    }
    reasoning = (
        f"blind-review council: decision={winner.action} via {method}; "
        f"votes [{multiset}]; consensus={'reconciled' if reconciled else 'clear'}. "
        + _sanitize(winner.rationale)
    )
    return _base_cons(f["grid_verdict"], f["stance"], f["risk_action"], council_json,
                      reasoning, deadlock=False, reconciled=reconciled,
                      trace_id=trace_id, council_half=council_half)


def _no_consensus_cons(candidates: dict[str, Any], multiset: str, reconciled: bool,
                       reason: str, trace_id: Optional[str], council_half: dict,
                       council_error: Optional[str] = None) -> dict:
    f = agg.no_consensus_fields()
    council_json = {
        "decision": agg.DECISION_NO_CONSENSUS,
        "vote_multiset": multiset,
        "consensus": "none",
        "reconciled": bool(reconciled),
    }
    reasoning = (
        f"blind-review council: NO_CONSENSUS — {_sanitize(reason)}. "
        f"The council declines to mandate a change; nothing changes. "
        f"votes [{multiset}]."
    )
    return _base_cons(f["grid_verdict"], f["stance"], f["risk_action"], council_json,
                      reasoning, deadlock=True, reconciled=reconciled,
                      trace_id=trace_id, council_half=council_half,
                      council_error=council_error)


# --- public entry ---

def run_council(world_state: dict, cycle_id: str,
                trigger: str | None = None) -> tuple[dict, dict, dict]:
    """Convene the blind-review council over a frozen world_state. Returns
    (round_0, round_1, cons). Never raises — any failure resolves to NO_CONSENSUS.
    round_1 is always {} (blind review has no rebuttal round)."""
    round_1: dict = {}

    try:
        cfg_version, cfg_snapshot = current_config_fingerprint()
    except Exception as e:  # noqa: BLE001 - fingerprint is best-effort context
        log.warning("[council_v2] config fingerprint compute failed: %r", e)
        cfg_version, cfg_snapshot = None, None

    council_half = current_config_council_half()

    with tracing.trace_cycle(cycle_id, metadata={
            "config_version": cfg_version, "config_snapshot": cfg_snapshot,
            "trigger": trigger or "unknown",
            "gate_triggered": bool(trigger and trigger.startswith("gate_wake"))}):
        trace_id = tracing.current_trace_id()
        tracing.set_trace_tags(trace_id, [f"trigger:{trigger or 'unknown'}"])
        # B4: group cycles into a Langfuse session — one per paper run (the natural
        # run boundary: a book reset starts a new paper_run_started_utc -> new
        # session). Falls back to config_version, then 'ungrouped', so a trace
        # always lands in some session rather than floating loose.
        try:
            from database import get_system_state
            _prs = get_system_state('paper_run_started_utc', default='') or ''
        except Exception:
            _prs = ''
        _session = (f"paper-run:{_prs}" if _prs
                    else f"config:{cfg_version}" if cfg_version else "ungrouped")
        tracing.set_trace_session(trace_id, _session)

        # Shared council ledger — the council's OWN recent decisions + matured outcomes,
        # authorship-free, injected IDENTICALLY to all three seats (not per-seat recall).
        # config_version-filtered + replay-safe. Best-effort: a failure degrades to no
        # ledger block, never a stand-down.
        try:
            ledger_block = (get_council_ledger(cfg_version) or {}).get("block")
        except Exception as e:  # noqa: BLE001 - ledger is best-effort context
            log.warning("[council_v2] council ledger read failed: %r", e)
            ledger_block = None

        try:
            return _convene(world_state, cycle_id, ledger_block, trace_id, council_half)
        except Exception as e:  # noqa: BLE001 - convene must never raise into the engine
            log.error("[council_v2] %s convene crashed: %r — NO_CONSENSUS (stance not "
                      "persisted)", cycle_id, e)
            cons = _no_consensus_cons(
                {}, multiset="", reconciled=False,
                reason=f"convene crash: {e!r}", trace_id=trace_id,
                council_half=council_half, council_error=repr(e))
            return {}, round_1, cons


def _convene(world_state: dict, cycle_id: str, ledger_block: Optional[str],
             trace_id: Optional[str], council_half: dict) -> tuple[dict, dict, dict]:
    """Phases 1-3 + reconciliation. Returns (round_0, round_1={}, cons)."""
    round_1: dict = {}

    # ---- Phase 1: isolated proposals (3 parallel) ----
    candidates, _raws = _gather(
        lambda seat: propose(seat, world_state, ledger_block, None),
        phase_label="propose")
    if len(candidates) < 2:
        log.warning("[council_v2] %s: only %d seat(s) proposed — NO_CONSENSUS",
                    cycle_id, len(candidates))
        multiset = agg.vote_multiset(candidates) if candidates else ""
        cons = _no_consensus_cons(candidates, multiset, reconciled=False,
                                  reason=f"only {len(candidates)} seat(s) proposed",
                                  trace_id=trace_id, council_half=council_half)
        return _own_r0(candidates), round_1, cons

    # ---- Phases 2-3: decide ----
    result = _decide(world_state, candidates, ledger_block, cycle_id, suffix="")
    reconciliation_ran = False   # a second deliberation round fired this cycle
    won_via_recon = False        # the winner came FROM that second round
    decided_candidates = candidates

    # ---- Reconciliation: one more round on no stable winner ----
    if result is None:
        log.info("[council_v2] %s: no stable winner — reconciliation round", cycle_id)
        reconciliation_ran = True
        split_block, _ = anonymize(candidates, cycle_id)  # authorship-free split shown back
        candidates2, _raws2 = _gather(
            lambda seat: propose(seat, world_state, ledger_block, split_block),
            phase_label="reconcile")
        if len(candidates2) >= 2:
            decided_candidates = candidates2  # record the revised proposals
            result2 = _decide(world_state, candidates2, ledger_block, cycle_id, suffix=":recon")
            if result2 is not None:
                result = result2
                won_via_recon = True

    multiset = agg.vote_multiset(decided_candidates)

    if result is None:
        log.info("[council_v2] %s: NO_CONSENSUS after reconciliation", cycle_id)
        cons = _no_consensus_cons(
            decided_candidates, multiset, reconciled=reconciliation_ran,
            reason="no stable winner after one reconciliation round",
            trace_id=trace_id, council_half=council_half)
        return _own_r0(decided_candidates), round_1, cons

    winner = result["winner"]
    method = result["method"]
    round_0 = _project_round0(decided_candidates, winner)
    cons = _winner_cons(decided_candidates, winner, method, multiset, won_via_recon,
                        trace_id, council_half)
    log.info("[council_v2] %s: decision=%s via %s votes[%s] consensus=%s",
             cycle_id, winner.action, method, multiset,
             "reconciled" if won_via_recon else "clear")
    return round_0, round_1, cons


# --- standalone runner (no service restart needed) ---

if __name__ == "__main__":
    import argparse

    from dotenv import load_dotenv

    load_dotenv()  # one .env load for the seat vendors + Langfuse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s")

    parser = argparse.ArgumentParser(
        description="Run the blind-review council against a frozen or freshly-built "
                    "world_state — the test path that needs no service restart.")
    parser.add_argument("--json", help="path to a world_state JSON file (else build one)")
    parser.add_argument("--cycle-id", default="cyc_standalone")
    args = parser.parse_args()

    if args.json:
        with open(args.json) as f:
            ws = json.load(f)
    else:
        from magi.orchestrator import build_world_state  # local: orchestrator imports us
        ws = build_world_state()

    r0, r1, cons = run_council(ws, args.cycle_id)
    print(json.dumps({"round_0": r0, "round_1": r1, "cons": cons}, indent=2, default=str))

    half = cons.get("_fingerprint_council_half") or {}
    print("\n[fingerprint] council-half carried on cons (orchestrator folds in the "
          "floor half + hashes downstream):")
    print(f"  persona_hashes: {half.get('persona_hashes')}")
    print(f"  models (CONFIGURED handles — what config_version hashes): {half.get('models')}")
    print(f"  veto_mode: {half.get('veto_mode')}")
    print(f"\n[council_json] {cons.get('council_json')}")

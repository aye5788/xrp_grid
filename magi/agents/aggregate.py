"""aggregate.py — deterministic social-choice aggregation for the blind-review
council's Phase 3. Pure stdlib, NO vendor call, NO LLM, byte-identical on replay.

Input: the seats' Phase-2 Rankings over the anonymized labels (A/B/C) plus the
label->CandidateDecision map. Output: the winning candidate, OR the signal that no
stable winner exists.

Method (per the redesign's GOVERNING PRINCIPLES):
  1. Condorcet check — a candidate that beats every other pairwise wins outright.
  2. Borda fallback — no Condorcet winner: the UNIQUE highest Borda score wins.
  3. No stable winner — a Condorcet cycle or a Borda TIE returns winner=None. There
     is NO deterministic tiebreak and NO external action-picker (P3): non-consensus
     is the COUNCIL'S output, not a rule's. run_council responds to winner=None by
     running ONE reconciliation round and, failing that, emitting NO_CONSENSUS — a
     first-class decision, never a fabricated pick made here.

Rankings are FLAT: conviction is recorded on the candidate but NEVER weights the
tally, so no seat gains disproportionate pull.

Contract translation (verified against orchestrator.enforce_hard_rules /
_build_debate_record, 2026-06-24): the single winning `action` is expanded into the
three cons axes the downstream contract GATES on — grid_verdict, stance,
risk_action. The old record-only axes (regime, regime_action, geometry_veto,
override_justification) are gone: the council no longer outputs a regime, and the
in-council arbiter veto is removed (there is no arbiter). run_council sets those
cons keys to None so their debate_records columns read NULL harmlessly.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger('magi.aggregate')

# The decision-level value the council emits when no stable winner survives even
# reconciliation. It is NOT a CandidateDecision.action (a single seat cannot propose
# "no consensus" — consensus is a property of the group); it is the council's own
# choice to decline mandating a change. See run_council for its cons translation.
DECISION_NO_CONSENSUS = "NO_CONSENSUS"

# action -> the THREE cons axes the downstream contract gates on. The single shared
# action is expanded into grid_verdict / stance / risk_action:
#   grid_verdict -> orchestrator._VERDICT_TO_GRID_ACTION (THESIS_HOLDS->MAINTAIN,
#                   RECONFIGURE->RECENTRE, NO_PROFITABLE_GRID->GRID_PAUSE)
#   stance       -> enforce_hard_rules step 0-pre (HOLD blocks rebuild,
#                   STAND_ASIDE floors risk to PAUSE_LONGS)
#   risk_action  -> the cycle's risk posture
# A one-sided PAUSE rides on risk_action with the grid otherwise DEPLOYed and
# MAINTAINed; STAND_ASIDE / HALT are the capital-erosion / stand-down postures.
_ACTION_TO_CONS: dict[str, dict[str, str]] = {
    "MAINTAIN":     {"grid_verdict": "THESIS_HOLDS",       "stance": "DEPLOY",      "risk_action": "CLEAR"},
    "RECONFIGURE":  {"grid_verdict": "RECONFIGURE",        "stance": "DEPLOY",      "risk_action": "CLEAR"},
    "PAUSE_LONGS":  {"grid_verdict": "THESIS_HOLDS",       "stance": "DEPLOY",      "risk_action": "PAUSE_LONGS"},
    "PAUSE_SHORTS": {"grid_verdict": "THESIS_HOLDS",       "stance": "DEPLOY",      "risk_action": "PAUSE_SHORTS"},
    "STAND_ASIDE":  {"grid_verdict": "THESIS_HOLDS",       "stance": "STAND_ASIDE", "risk_action": "PAUSE_LONGS"},
    "HALT":         {"grid_verdict": "NO_PROFITABLE_GRID", "stance": "STAND_ASIDE", "risk_action": "HALT"},
}

# NO_CONSENSUS -> "nothing changes": keep what is resting, commit no new capital, do
# not rebuild, do not tear down. THESIS_HOLDS maps to MAINTAIN; HOLD blocks any
# rebuild; CLEAR leaves risk untouched. This is the faithful thin translation of
# "the council declines to mandate a change" into the existing cons contract.
_NO_CONSENSUS_CONS: dict[str, str] = {
    "grid_verdict": "THESIS_HOLDS", "stance": "HOLD", "risk_action": "CLEAR",
}


def _sanitize_rankings(rankings: list[Any], labels: list[str]) -> list[Any]:
    """Ballot well-formedness guard (2026-07-02). The Ranking schema asks for
    every presented label exactly once, but nothing enforced it: a duplicated
    label silently double-scores in Borda (k inflates and the dup gets two
    point grants) and flips its pairwise position (dict keeps the LAST index),
    distorting the tally away from what the seat actually expressed.

    Repairs vs exclusions — chosen to preserve council signal, never invent it:
      * out-of-set labels dropped, duplicate labels keep their FIRST occurrence
        (unambiguous — the seat's intent is clear) -> ballot REPAIRED, counted;
      * a ballot that then does not cover every presented label exactly once is
        EXCLUDED from the tally (ranking the omitted labels for the seat would
        be inventing preferences) — same treatment as a non-responding seat.
    Exclusions raise a dashboard-only 'ranking_ballot_excluded' warn alert so a
    seat that repeatedly emits malformed ballots is visible, not silent. This
    guards HOW ballots are counted only — it never touches what any seat chose
    (no bypass; the malformed tally was the thing overriding seats' judgment).
    """
    label_set = set(labels)
    clean: list[Any] = []
    for r in rankings:
        raw = list(getattr(r, "order", []) or [])
        seen: set = set()
        order = []
        for lb in raw:
            if lb in label_set and lb not in seen:
                seen.add(lb)
                order.append(lb)
        if len(order) == len(label_set):
            if order != raw:
                log.warning(
                    "[aggregate] ballot repaired (dup/foreign labels): %r -> %r",
                    raw, order)
                from types import SimpleNamespace
                r = SimpleNamespace(order=order)
            clean.append(r)
        else:
            log.warning(
                "[aggregate] ballot EXCLUDED — not a permutation of %r "
                "after repair: %r", labels, raw)
            try:
                from database import insert_alert
                insert_alert(
                    'warn', 'ranking_ballot_excluded',
                    f"Phase-2 ranking excluded from tally: order={raw!r} is not "
                    f"a permutation of presented labels {labels!r} after "
                    f"dedup/foreign-label repair. Counted like a non-responding "
                    f"seat this cycle.")
            except Exception:
                pass  # alerting must never break the tally
    return clean


def _pairwise_beats(rankings: list[Any], labels: list[str]) -> dict[tuple[str, str], int]:
    """For every ordered pair (x, y), count how many rankings place x BEFORE y."""
    counts: dict[tuple[str, str], int] = {}
    for x in labels:
        for y in labels:
            if x == y:
                continue
            counts[(x, y)] = 0
    for r in rankings:
        order = list(getattr(r, "order", []) or [])
        pos = {label: i for i, label in enumerate(order)}
        for x in labels:
            for y in labels:
                if x == y or x not in pos or y not in pos:
                    continue
                if pos[x] < pos[y]:
                    counts[(x, y)] += 1
    return counts


def _condorcet_winner(labels: list[str],
                      pw: dict[tuple[str, str], int]) -> Optional[str]:
    """A label that STRICTLY beats every other label pairwise (more rankings place it
    ahead than behind). Returns None if no such label exists (a cycle or a tie)."""
    for x in labels:
        if all(pw[(x, y)] > pw[(y, x)] for y in labels if y != x):
            return x
    return None


def _borda_scores(rankings: list[Any], labels: list[str]) -> dict[str, int]:
    """Borda count: in a ranking of k labels, the top gets k-1 points, the next k-2,
    down to 0 for last. Summed across rankings. Flat — no conviction weighting."""
    scores = {label: 0 for label in labels}
    for r in rankings:
        order = [label for label in (getattr(r, "order", []) or []) if label in scores]
        k = len(order)
        for i, label in enumerate(order):
            scores[label] += (k - 1 - i)
    return scores


def aggregate(rankings: list[Any],
              label_to_candidate: dict[str, Any]) -> dict[str, Any]:
    """Combine the seats' rankings into a single winning candidate, OR report that no
    stable winner exists.

    Returns a dict:
      * winner_label / winner   — the chosen label and its CandidateDecision, or
                                  (None, None) when no stable winner exists
      * method                  — 'sole_candidate' | 'condorcet' | 'borda' | None
                                  (None iff no stable winner)
      * borda / pairwise        — the raw tallies (for the reasoning trace)

    A Condorcet cycle or a Borda tie yields winner=None: there is NO most-reversible
    tiebreak and NO deterministic action-picker (governing principle P3). The caller
    (run_council) turns winner=None into a reconciliation round and, failing that,
    the NO_CONSENSUS decision — the council's own output, not a rule's.
    """
    labels = sorted(label_to_candidate.keys())
    if not labels:
        return {"winner_label": None, "winner": None, "method": None,
                "borda": {}, "pairwise": {}}
    if len(labels) == 1:
        only = labels[0]
        return {"winner_label": only, "winner": label_to_candidate[only],
                "method": "sole_candidate", "borda": {only: 0}, "pairwise": {}}

    rankings = _sanitize_rankings(rankings, labels)
    if len(rankings) < 2:
        # Mirrors run_council's own >=2-rankings threshold: a tally over a
        # single surviving ballot would let one seat decide alone. winner=None
        # is the existing contract — the caller reconciles or declares
        # NO_CONSENSUS (the council's own output, P3).
        log.warning(
            "[aggregate] only %d well-formed ballot(s) after sanitization — "
            "no tally", len(rankings))
        return {"winner_label": None, "winner": None, "method": None,
                "borda": {}, "pairwise": {}}

    pw = _pairwise_beats(rankings, labels)
    borda = _borda_scores(rankings, labels)

    winner = _condorcet_winner(labels, pw)
    method: Optional[str]
    if winner is not None:
        method = "condorcet"
    else:
        top = max(borda.values())
        leaders = [label for label in labels if borda[label] == top]
        if len(leaders) == 1:
            winner, method = leaders[0], "borda"
        else:
            # No stable winner — a Condorcet cycle or a Borda tie. Do NOT pick:
            # return None and let the council reconcile / declare NO_CONSENSUS.
            winner, method = None, None

    return {
        "winner_label": winner,
        "winner": label_to_candidate[winner] if winner is not None else None,
        "method": method,
        "borda": borda,
        "pairwise": pw,
    }


# --- deterministic translation: winning candidate -> legacy contract shapes ---

def geom_dict(geometry: Any) -> Optional[dict]:
    """Geometry object -> the {target_spacing_pct, target_levels} dict the engine
    reads off round_0['melchior']['geometry']. None passes through."""
    if geometry is None:
        return None
    return {"target_spacing_pct": geometry.target_spacing_pct,
            "target_levels": geometry.target_levels}


def consensus_fields(winner: Any) -> dict[str, str]:
    """The three gating cons axes (grid_verdict / stance / risk_action) for a winning
    candidate's action. There are no record-only axes to carry: regime / regime_action
    / geometry_veto are removed from the council's output."""
    return dict(_ACTION_TO_CONS[winner.action])


def no_consensus_fields() -> dict[str, str]:
    """The three gating cons axes for the NO_CONSENSUS decision — 'nothing changes'."""
    return dict(_NO_CONSENSUS_CONS)


def verdict_for(action: str) -> str:
    """grid_verdict for a concrete action (used to record each seat's own proposal on
    the flight-recorder verdict axis)."""
    return _ACTION_TO_CONS[action]["grid_verdict"]


def risk_for(action: str) -> str:
    """risk_action for a concrete action (used to record a seat's own proposal on the
    flight-recorder risk axis)."""
    return _ACTION_TO_CONS[action]["risk_action"]


def vote_multiset(candidates_by_seat: dict[str, Any]) -> str:
    """Authorship-free tally of the Phase-1 actions, e.g. '2x MAINTAIN, 1x RECONFIGURE'.
    Sorted by (descending count, action name) so it is deterministic and carries no
    seat identity — exactly what the council's own ledger recalls."""
    counts: dict[str, int] = {}
    for cand in candidates_by_seat.values():
        counts[cand.action] = counts.get(cand.action, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{n}x {action}" for action, n in ordered)

"""anonymize.py — deterministic, authorship-stripping anonymizer for the
blind-review council's Phase 2 (anonymized cross-review).

Pure stdlib, no vendor call. Given the three seats' Phase-1 CandidateDecisions,
it (1) normalizes each to a uniform template so writing-style cues cannot leak a
seat's identity, (2) maps the seats to labels A/B/C under a per-cycle SEEDED
shuffle, and (3) returns the anonymized block handed IDENTICALLY to every seat in
Phase 2 plus the private label->seat de-anon map (used for logging only).

Determinism is load-bearing: the shuffle is seeded on the cycle_id, so the same
(candidates, cycle_id) always produce the same labeling — byte-identical on
replay, no model call, no hidden state. Anchoring on a seat's own past identity is
structurally impossible because the normalized template carries no authorship and
the seat cannot tell which label is its own (beyond chance).
"""

from __future__ import annotations

import hashlib
import random
import re
from typing import Any

# Canonical seat order — the shuffle permutes a subset of these (only seats that
# actually produced a candidate this cycle are labeled).
_SEATS = ("casper", "melchior", "balthasar")
_LABELS = ("A", "B", "C")

# Defense-in-depth: scrub any whole-word seat name from a candidate's free text
# before it is shown to the ranking phase. The structural anonymization (uniform
# template, style normalization, seeded shuffle) is what defeats style/ordering
# leaks; this only closes the edge case of a seat naming ITSELF in its rationale or
# evidence. Seats are blind to peers in Phase 1, so they have no legitimate reason
# to reference one of these names — replacing with a neutral token costs nothing.
_SEAT_NAME_RE = re.compile(r"\b(" + "|".join(_SEATS) + r")\b", re.IGNORECASE)


def _scrub_seat_names(text: str) -> str:
    return _SEAT_NAME_RE.sub("a seat", text)


def _geometry_str(candidate: Any) -> str:
    """Style-neutral geometry rendering. 'none' unless the candidate RECONFIGUREs."""
    geo = getattr(candidate, "geometry", None)
    if geo is None:
        return "none"
    return f"spacing={geo.target_spacing_pct}, levels={geo.target_levels}"


def normalize_candidate(candidate: Any) -> str:
    """Render one CandidateDecision to a FIXED template with stable field order.

    Authorship leaks through prose style, not content — so every candidate is
    flattened to the same labeled fields in the same order. key_evidence is capped
    at the first five entries (matching the council's native-vocabulary renderers)
    and joined deterministically. Regime is NOT rendered: it is a shared world_state
    INPUT every seat already sees (not a per-candidate output), so it carries no
    discriminating content and would only add noise to the blind comparison.
    """
    evidence = "; ".join(str(e) for e in (candidate.key_evidence or [])[:5])
    rendered = (
        f"action: {candidate.action}\n"
        f"geometry: {_geometry_str(candidate)}\n"
        f"key_evidence: {evidence}\n"
        f"rationale: {candidate.rationale}"
    )
    return _scrub_seat_names(rendered)


def _seed(cycle_id: str) -> int:
    """Stable integer seed from the cycle_id (sha256 -> first 64 bits). Replaces a
    wall-clock/random seed so the labeling is replay-identical."""
    return int(hashlib.sha256(cycle_id.encode("utf-8")).hexdigest()[:16], 16)


def anonymize(candidates: dict[str, Any],
              cycle_id: str) -> tuple[str, dict[str, str]]:
    """Anonymize the Phase-1 candidates for blind cross-review.

    `candidates` maps seat name -> CandidateDecision (only seats that produced one;
    a stood-down seat is simply absent). Returns:
      * block: the anonymized, normalized candidate set as one text block, in label
        order (CANDIDATE A, then B, ...), handed identically to every seat.
      * label_to_seat: the private de-anon map {label: seat} — for logging and the
        post-aggregation lookup ONLY; it is never shown to a seat.

    The seats present are shuffled under the cycle-seeded RNG and assigned labels in
    that shuffled order, so label<->seat is unpredictable to a seat but deterministic
    to the system.
    """
    present = [s for s in _SEATS if s in candidates]
    rng = random.Random(_seed(cycle_id))
    shuffled = list(present)
    rng.shuffle(shuffled)

    labels = _LABELS[:len(shuffled)]
    label_to_seat = {label: seat for label, seat in zip(labels, shuffled)}

    parts = []
    for label in labels:
        seat = label_to_seat[label]
        parts.append(
            f"=== CANDIDATE {label} ===\n{normalize_candidate(candidates[seat])}"
        )
    block = "\n\n".join(parts)
    return block, label_to_seat

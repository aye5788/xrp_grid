"""Locked cross-boundary vote schemas for the blind-review MAGI council.

The blind-review council (2026-06-25 redesign) uses exactly TWO seat-authored
schemas: every seat authors ONE complete CandidateDecision in isolation
(Phase 1), then every seat ranks the anonymized candidate set with a Ranking
(Phase 2). Geometry is the shared nested object a RECONFIGURE candidate
carries. Seats are built in code (magi/agents/seats.py) with these as forced
tool schemas via schema_for_tool.

The ARBITER-ERA per-seat split schemas (RegimeVote -> Casper, GridVote ->
Melchior, RiskVote -> Balthasar) were DELETED 2026-07-05 — they had no live
caller since the redesign collapsed their authority into CandidateDecision's
single `action`. RegimeVote survives only in the offline tuning scaffold that
still evals it (optimize/casper/agent.py, where it now lives).

conviction is a float 0.0-1.0 on every schema. Everything here uses
extra="ignore", NOT "forbid": every seat authors these, including Casper on
the native Gemini API, whose response_schema 400s (INVALID_ARGUMENT) on the
`additionalProperties: false` that extra="forbid" emits. The Anthropic-routed
seats reach these through schema_for_tool, which strips additionalProperties
centrally, so extra="ignore" is safe there too.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Geometry(BaseModel):
    """Chosen grid geometry — populated only on a RECONFIGURE CandidateDecision.

    Values are unbounded here by design; spacing/level range limits are enforced in
    the deterministic hard-rule layer, not in this schema.
    """

    # extra="ignore", NOT "forbid": Geometry is nested inside CandidateDecision, which
    # Casper authors on the native Gemini API via ADK output_schema (which mirrors the
    # pydantic schema and does NOT route through schema_for_tool's central
    # additionalProperties strip). extra="forbid" emits `additionalProperties: false`
    # on this nested object, which native Gemini 400s (INVALID_ARGUMENT) — the same
    # constraint that forces CandidateDecision/Ranking to extra="ignore". The Anthropic
    # seats reach this through schema_for_tool, which strips the key regardless, so
    # extra="ignore" is safe there too.
    model_config = ConfigDict(extra="ignore")

    target_spacing_pct: float = Field(
        description=(
            "Chosen variant's spacing between grid levels, as a decimal "
            "(e.g. 0.0075 = 0.75%)."
        )
    )
    target_levels: int = Field(
        description="Chosen variant's total grid level count."
    )


# ---------------------------------------------------------------------------
# Blind-review council (council redesign) — the SHARED candidate + ranking
# ---------------------------------------------------------------------------
# The three seats are co-equal: each authors ONE complete CandidateDecision in
# isolation (Phase 1), then every seat ranks the anonymized candidate set
# (Phase 2). The authority that used to live across the three arbiter-era
# split schemas (Melchior's verdict+geometry, Balthasar's stance/risk/veto,
# Casper's regime_action) has COLLAPSED into the single `action` of the SHARED
# candidate that any seat can author and all can rank. Regime itself is no
# longer an OUTPUT of any seat — it is an INPUT carried in world_state that
# every seat reads; the Casper regime grader retired with it.


class CandidateDecision(BaseModel):
    """A complete, self-contained council decision authored by ONE seat in Phase 1
    (isolated proposal) and ranked blind by all seats in Phase 2.

    Each seat — equal to the others, no privileged seat — commits ONE position over
    the single unified action space, and (only on RECONFIGURE) the geometry. There
    is no separate regime/grid/risk vote to merge: the old split (regime_action,
    grid verdict + geometry, risk stance/action/geometry_veto) has collapsed into
    this one `action`. REGIME IS NOT A FIELD A SEAT OUTPUTS — it is an INPUT the
    seats read from world_state. The deterministic aggregator translates the WINNING
    candidate's action into the legacy `cons` keys via the action->cons table.

    `action` is the CONCRETE action set only. NO_CONSENSUS is NOT proposable by a
    seat (consensus is a property of the group, not of one proposal): it is a
    decision-level outcome the aggregator/run_council emit when no winner survives
    even reconciliation. See magi/agents/aggregate.py:DECISION_NO_CONSENSUS.
    """

    model_config = ConfigDict(extra="ignore")

    action: Literal[
        "MAINTAIN", "RECONFIGURE", "PAUSE_LONGS",
        "PAUSE_SHORTS", "STAND_ASIDE", "HALT",
    ] = Field(
        description=(
            "The single final action over the shared action space. MAINTAIN: keep "
            "the live grid as-is. RECONFIGURE: rebuild to a better geometry (carries "
            "geometry). PAUSE_LONGS / PAUSE_SHORTS: hold one side off. STAND_ASIDE: "
            "structural downtrend / capital-erosion risk — cancel buys and ACTIVELY "
            "work inventory off: the engine maintains a sells-only resting ladder "
            "above market (down to the XRP buffer floor) for as long as this stance "
            "stands, so a persisting STAND_ASIDE keeps distributing inventory into "
            "strength (see world_state.workoff). HALT: stand the grid down entirely."
        )
    )
    geometry: Optional[Geometry] = Field(
        default=None,
        description=(
            "Chosen geometry. Present ONLY on RECONFIGURE (target_spacing_pct, "
            "target_levels); null on every other action."
        ),
    )
    key_evidence: list[str] = Field(
        description=(
            "3-5 short strings citing the specific world_state values that drove "
            "this decision."
        )
    )
    rationale: str = Field(
        description="One sentence: why this action over the alternatives."
    )
    conviction: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Confidence in this decision, 0.0-1.0. RECORDED for observability; it is "
            "NOT weighted in the aggregation — the tally is flat by design."
        ),
    )

    @model_validator(mode="after")
    def _geometry_matches_action(self) -> "CandidateDecision":
        """Enforce: geometry present iff action == RECONFIGURE."""
        if self.action == "RECONFIGURE" and self.geometry is None:
            raise ValueError(
                "action=RECONFIGURE requires geometry "
                "(target_spacing_pct, target_levels)."
            )
        if self.action != "RECONFIGURE" and self.geometry is not None:
            raise ValueError(
                f"action={self.action} must not carry geometry; geometry is valid "
                "only on RECONFIGURE."
            )
        return self


class Ranking(BaseModel):
    """One seat's blind ranking of the anonymized candidates in Phase 2.

    `order` lists the labels best->worst; a seat may unknowingly rank its own
    candidate. `why` is a parallel list of one-line justifications aligned to
    `order` (order[i] is justified by why[i]).

    NOTE: `why` is a list[str], NOT the dict[str, str] of the design sketch — a
    dict emits `additionalProperties` into the schema, which the native-Gemini
    response_schema 400s on (the documented invariant), and Casper authors this
    schema on native Gemini. A position-aligned list is style-neutral, carries the
    same information, and stays Gemini-safe.
    """

    model_config = ConfigDict(extra="ignore")

    order: list[Literal["A", "B", "C"]] = Field(
        description=(
            "The candidate labels ranked best to worst. Include every presented "
            "label exactly once."
        )
    )
    why: list[str] = Field(
        description=(
            "One-line justifications aligned to `order`: why[i] explains the "
            "placement of order[i]. Same length as `order`."
        )
    )

    @model_validator(mode="after")
    def _why_aligns_with_order(self) -> "Ranking":
        """Enforce: one justification per ranked label (len(why) == len(order))."""
        if len(self.why) != len(self.order):
            raise ValueError(
                f"why has {len(self.why)} entries but order has {len(self.order)}; "
                "each ranked label needs exactly one justification."
            )
        return self

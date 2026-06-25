"""Locked cross-boundary vote schemas for the MAGI council (ADK output_schema).

Each council member's ADK LlmAgent is constructed with one of these as its
`output_schema`, which forces the model's final response to be JSON conforming to
the model and stores it in session.state under the agent's `output_key`. council.py
reads that structured output back and TRANSLATES it into the parsed-vote dict
shapes orchestrator.py already consumes (see council._translate).

  RegimeVote  -> Casper    -> output_key="casper_r0"
  GridVote    -> Melchior  -> output_key="melchior_r0"
  RiskVote    -> Balthasar -> output_key="balthasar_r0"

ADK ref (adk-docs MCP, agents/llm-agents): output_schema — "If set, the agent's
final response *must* be a JSON string conforming to this schema"; output_key
saves that final response into session state.

conviction is a float 0.0-1.0 on every schema. GridVote/RiskVote use
extra="forbid"; RegimeVote uses extra="ignore" because Casper runs on the native
Gemini API, which 400s (INVALID_ARGUMENT) on the `additionalProperties: false`
that extra="forbid" emits into response_schema. GPT-4o/Claude via LiteLlm accept
it, so Melchior/Balthasar keep extra="forbid".
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RegimeVote(BaseModel):
    """Casper — market-regime classification."""

    # extra="ignore", NOT "forbid": Casper runs on the native Gemini API, whose
    # response_schema rejects the `additionalProperties: false` that extra="forbid"
    # emits — a 400 INVALID_ARGUMENT on every R0 call. Validated against
    # gemini-2.5-flash 2026-06-01 (see optimize/casper smoke run). GridVote /
    # RiskVote keep extra="forbid" — they go through LiteLlm to OpenAI / Anthropic,
    # which accept (OpenAI requires) additionalProperties.
    model_config = ConfigDict(extra="ignore")

    position: Literal["RANGING", "TRENDING", "UNCERTAIN"] = Field(
        description="Casper's regime classification for this cycle."
    )
    conviction: float = Field(
        ge=0.0, le=1.0, description="Confidence in the regime call, 0.0-1.0."
    )
    key_evidence: list[str] = Field(
        description=(
            "3-5 short strings citing the specific world_state indicators/values "
            "that drove the regime call."
        )
    )
    crux: str = Field(
        description="One sentence: the single thing that would change the call."
    )
    regime_action: Literal["EXECUTE", "DEFER_STRUCTURAL", "STAND_DOWN"] = Field(
        description=(
            "Whether the regime supports executing structural grid changes this "
            "cycle. Read by the downstream consensus/hard-rule layer."
        )
    )


class Geometry(BaseModel):
    """Chosen grid geometry — populated only on a RECONFIGURE GridVote verdict.

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


class GridVote(BaseModel):
    """Melchior — grid-economics judgment (verdict, not an action)."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["THESIS_HOLDS", "RECONFIGURE", "NO_PROFITABLE_GRID"] = Field(
        description=(
            "Melchior's economic judgment. THESIS_HOLDS: a grid is live and its "
            "economics remain justified. RECONFIGURE: a better/profitable "
            "configuration is justified (carries geometry). NO_PROFITABLE_GRID: no "
            "candidate clears the acceptable bar."
        )
    )
    conviction: float = Field(
        ge=0.0, le=1.0, description="Confidence in the verdict, 0.0-1.0."
    )
    key_evidence: list[str] = Field(
        description=(
            "3-5 short strings citing specific scored_variants / baseline values "
            "(rank-1 pnl/day, current-config pnl/day, spacing/levels, acceptable "
            "counts, ranks)."
        )
    )
    crux: str = Field(
        description="One sentence: the single thing that would change the verdict."
    )
    geometry: Optional[Geometry] = Field(
        default=None,
        description=(
            "Chosen geometry. Present ONLY on RECONFIGURE (target_spacing_pct, "
            "target_levels from the chosen acceptable variant). Null on "
            "THESIS_HOLDS and NO_PROFITABLE_GRID."
        ),
    )

    @model_validator(mode="after")
    def _geometry_matches_verdict(self) -> "GridVote":
        """Enforce: geometry present iff verdict == RECONFIGURE."""
        if self.verdict == "RECONFIGURE" and self.geometry is None:
            raise ValueError(
                "verdict=RECONFIGURE requires geometry "
                "(target_spacing_pct, target_levels)."
            )
        if self.verdict != "RECONFIGURE" and self.geometry is not None:
            raise ValueError(
                f"verdict={self.verdict} must not carry geometry; geometry is "
                "valid only on RECONFIGURE."
            )
        return self


class RiskVote(BaseModel):
    """Balthasar — survival/risk gating judgment.

    As the synthesis ARBITER, Balthasar's geometry_veto carries the structural
    veto that used to live in orchestrator hard-rule 0d: HOLD_GEOMETRY / RISK_BLOCK
    over a RECONFIGURE holds the grid in-council (the council emits THESIS_HOLDS),
    PROCEED lets the reconfigure stand. override_justification is the conditional
    carrier for the one case the schema alone can't gate (it needs Casper's vote):
    proceeding over a live regime objection. Enforcement lives in
    council_v2.run_council at synthesis, where all three votes are in hand.
    """

    model_config = ConfigDict(extra="forbid")

    stance: Literal["DEPLOY", "HOLD", "STAND_ASIDE"] = Field(
        description=(
            "The council's capital-deployment mandate (Fix 3, 2026-06-11) — as "
            "arbiter you own it. DEPLOY: the market warrants grid capital; "
            "Melchior's verdict pipeline runs unchanged (maintain or rebuild). "
            "HOLD: keep what is already resting but commit NO new capital — a "
            "RECONFIGURE will not rebuild while HOLD stands. STAND_ASIDE: "
            "structural downtrend / capital-erosion risk — buy orders are "
            "cancelled and no buys are placed; resting sells stay to work "
            "inventory off. This is the stance the orchestrator translates "
            "deterministically; it is graded against forward outcomes."
        )
    )
    risk_action: Literal["CLEAR", "PAUSE_LONGS", "PAUSE_SHORTS", "HALT"] = Field(
        description="Balthasar's risk posture for this cycle."
    )
    geometry_veto: Literal["PROCEED", "HOLD_GEOMETRY", "RISK_BLOCK"] = Field(
        description=(
            "Whether risk conditions permit a structural grid change this cycle. "
            "As the arbiter you OWN this veto: HOLD_GEOMETRY / RISK_BLOCK over a "
            "RECONFIGURE holds the grid (no rebuild this cycle); PROCEED lets it "
            "stand. Read by the downstream consensus layer."
        )
    )
    conviction: float = Field(
        ge=0.0, le=1.0, description="Confidence in the vote, 0.0-1.0."
    )
    key_evidence: list[str] = Field(
        description=(
            "3-5 short strings citing specific world_state risk fields and values."
        )
    )
    crux: str = Field(
        description="One sentence: the single thing that would change the verdict."
    )
    override_justification: Optional[str] = Field(
        default=None,
        description=(
            "Fill this ONLY when you set geometry_veto=PROCEED on a structural "
            "reconfigure while Casper's regime read objects (regime_action is "
            "DEFER_STRUCTURAL or STAND_DOWN). State, on the merits, why the "
            "reconfigure should proceed over that specific objection — engage "
            "Casper's cited reason, do not merely assert. Leave null when there is "
            "no live regime objection, or when you are not proceeding (you set "
            "HOLD_GEOMETRY/RISK_BLOCK), or the verdict is not RECONFIGURE. An "
            "un-justified proceed over a live objection is NOT honored: the "
            "objection stands and the grid holds (MAINTAIN)."
        ),
    )


# ---------------------------------------------------------------------------
# Blind-review council (council redesign) — the SHARED candidate + ranking
# ---------------------------------------------------------------------------
# These two schemas replace the per-seat split votes (RegimeVote / GridVote /
# RiskVote) for the blind-review council. In that design the three seats are
# co-equal: each authors ONE complete CandidateDecision in isolation (Phase 1),
# then every seat ranks the anonymized candidate set (Phase 2). The authority
# that used to live across three split schemas (Melchior's verdict+geometry,
# Balthasar's stance/risk/veto, Casper's regime_action) has COLLAPSED into the
# single `action` of the SHARED candidate that any seat can author and all can
# rank. Regime itself is no longer an OUTPUT of any seat — it is an INPUT carried
# in world_state that every seat reads; the Casper regime grader retires with it.
#
# Both use extra="ignore", NOT "forbid": in the symmetric council EVERY seat
# authors BOTH schemas, including Casper on the native Gemini API, whose
# response_schema 400s (INVALID_ARGUMENT) on the `additionalProperties: false`
# that extra="forbid" emits — the same constraint that forces RegimeVote to
# extra="ignore". Melchior/Balthasar reach these through schema_for_tool, which
# strips additionalProperties centrally, so extra="ignore" is safe there too.


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
            "structural downtrend / capital-erosion risk — cancel buys, work "
            "inventory off. HALT: stand the grid down entirely."
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
        """Enforce: geometry present iff action == RECONFIGURE (mirrors GridVote)."""
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

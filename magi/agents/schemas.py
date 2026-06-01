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

    model_config = ConfigDict(extra="forbid")

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
    """Balthasar — survival/risk gating judgment."""

    model_config = ConfigDict(extra="forbid")

    risk_action: Literal["CLEAR", "PAUSE_LONGS", "PAUSE_SHORTS", "HALT"] = Field(
        description="Balthasar's risk posture for this cycle."
    )
    geometry_veto: Literal["PROCEED", "HOLD_GEOMETRY", "RISK_BLOCK"] = Field(
        description=(
            "Whether risk conditions permit a structural grid change this cycle. "
            "Read by the downstream consensus/hard-rule layer."
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

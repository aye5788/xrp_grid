"""Structured-output schema for the standalone Balthasar risk agent (Phase 1).

Decoupled from Letta. This Pydantic model is the single source of truth for the
shape of Balthasar's Round-0 vote. The headless runner converts it to an
Anthropic tool input_schema and forces the model to emit exactly this object.

Output fields (canonical, 2026-05-28 shutdown baseline):
- position        : CLEAR | PAUSE_LONGS | PAUSE_SHORTS | HALT
- geometry_veto   : PROCEED | HOLD_GEOMETRY | RISK_BLOCK
- conviction      : float 0.0-1.0
- key_evidence    : list of short strings citing world_state data
- crux            : one sentence; the single thing that would change the vote
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BalthasarR0(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: Literal["CLEAR", "PAUSE_LONGS", "PAUSE_SHORTS", "HALT"] = Field(
        description="Risk action for this cycle."
    )
    geometry_veto: Literal["PROCEED", "HOLD_GEOMETRY", "RISK_BLOCK"] = Field(
        description="Whether risk conditions permit a grid geometry change this cycle."
    )
    conviction: float = Field(
        ge=0.0, le=1.0, description="Confidence in the vote, 0.0-1.0."
    )
    key_evidence: list[str] = Field(
        description="3-5 short strings citing specific world_state indicators/data."
    )
    crux: str = Field(
        description="One sentence: the single thing that would change the vote."
    )

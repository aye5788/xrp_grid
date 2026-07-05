"""ADK agent module for tuning Casper's persona instruction with `adk optimize`.

Exposes `root_agent`: a stateless RegimeVote LlmAgent — native
gemini-2.5-flash, output_schema=RegimeVote, include_contents="none". The only
thing `adk optimize` rewrites is this agent's `instruction` (i.e. casper.md).

HISTORICAL NOTE (2026-07-05): this mirrored the ARBITER-ERA live Casper seat
(magi.council._build_agent("casper")). The blind-review council redesign
(2026-06-25) retired the per-seat RegimeVote in the live path — the schema now
lives in THIS module (see below) and the scaffold evals a seat shape that no
longer runs live.

Run `adk optimize` from the repo root so `magi.*` and this package both import.
"""

import sys
from pathlib import Path

# Make the repo root importable so `magi.*` resolves regardless of cwd, and the
# `optimize/` dir importable so the custom metric's dotted path
# `casper.metrics.regime_position_match` (sampler_config.json) resolves when
# _CustomMetricEvaluator imports it mid-run — `adk optimize` does not leave
# `optimize/` on sys.path itself.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_OPTIMIZE_DIR = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _OPTIMIZE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv

# Provider keys (GOOGLE_API_KEY) for the Gemini client.
load_dotenv(_REPO_ROOT / ".env")

from typing import Literal

from google.adk.agents import LlmAgent
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from magi.agents.personas import load_persona

# Same model handle as magi.council._CASPER_MODEL.
_CASPER_MODEL = "gemini-2.5-flash"


class RegimeVote(BaseModel):
    """Casper — market-regime classification (ARBITER-ERA schema).

    Moved here 2026-07-05 from magi/agents/schemas.py when the arbiter-era
    seat schemas (RegimeVote/GridVote/RiskVote) were deleted from the live
    package — this scaffold is their last consumer. The blind-review council
    (2026-06-25 redesign) replaced the per-seat split votes with the shared
    CandidateDecision/Ranking pair, so this scaffold now evals a seat shape
    that no longer runs live.

    extra="ignore", NOT "forbid": Casper runs on the native Gemini API, whose
    response_schema rejects the `additionalProperties: false` that
    extra="forbid" emits — a 400 INVALID_ARGUMENT on every call.
    """

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

# Free-tier survival: ride out 429 RESOURCE_EXHAUSTED with client-side backoff
# instead of failing the optimize loop. Per the ADK Gemini-models doc
# (retry_options on generate_content_config). Bump attempts if the free-tier
# RPM ceiling bites during a larger run.
_RETRY = types.GenerateContentConfig(
    http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(initial_delay=2, attempts=5),
    ),
)

root_agent = LlmAgent(
    name="casper",
    model=_CASPER_MODEL,
    instruction=load_persona("casper"),
    include_contents="none",
    output_schema=RegimeVote,
    output_key="casper_r0",
    generate_content_config=_RETRY,
)


# --- Register the custom eval metric into ADK's global registry ---
# `adk eval` registers EvalConfig.custom_metrics into DEFAULT_METRIC_EVALUATOR_
# REGISTRY (cli_tools_click.py); `adk optimize` does NOT, so the LocalEvalService
# raises `NotFoundError: regime_position_match not found in registry` mid-run.
# This module is imported by `adk optimize` when it loads root_agent, so doing
# the registration here (mirroring the cli_eval path) closes that gap. The
# function itself is resolved lazily by _CustomMetricEvaluator via the
# custom_function_path in sampler_config.json.
from google.adk.cli.cli_eval import get_default_metric_info  # noqa: E402
from google.adk.evaluation.custom_metric_evaluator import (  # noqa: E402
    _CustomMetricEvaluator,
)
from google.adk.evaluation.metric_evaluator_registry import (  # noqa: E402
    DEFAULT_METRIC_EVALUATOR_REGISTRY,
)

DEFAULT_METRIC_EVALUATOR_REGISTRY.register_evaluator(
    get_default_metric_info(
        metric_name="regime_position_match",
        description="Casper RegimeVote.position exact match vs ground-truth regime.",
    ),
    _CustomMetricEvaluator,
)

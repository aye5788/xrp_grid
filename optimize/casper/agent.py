"""ADK agent module for tuning Casper's persona instruction with `adk optimize`.

Exposes `root_agent`: the SAME stateless RegimeVote LlmAgent that
magi.council._build_agent("casper") builds in the live path — native
gemini-2.5-flash, output_schema=RegimeVote, include_contents="none". The only
thing `adk optimize` rewrites is this agent's `instruction` (i.e. casper.md).

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

from google.adk.agents import LlmAgent
from google.genai import types

from magi.agents.personas import load_persona
from magi.agents.schemas import RegimeVote

# Same model handle as magi.council._CASPER_MODEL.
_CASPER_MODEL = "gemini-2.5-flash"

# NOTE: RegimeVote is used directly (no Gemini-compat subclass). The live schema
# was fixed 2026-06-01 to use extra="ignore" so it no longer emits the
# `additionalProperties` that native Gemini 400s on — so the optimize agent now
# mirrors the live Casper schema exactly. See magi/agents/schemas.py.

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

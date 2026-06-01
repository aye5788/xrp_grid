"""Custom ADK eval metric: does Casper's RegimeVote.position match ground truth?

Mirrors the live persona-regression grader (evals/common/extractors.r0_position
+ exact_match): parse the agent's final JSON response, pull `position`, compare
to the expected regime carried in the eval case's `final_response`. Returns 1.0
on an exact match else 0.0, averaged across invocations.

Registered in sampler_config.json under `custom_metrics`; the pass threshold
lives in `criteria`.
"""

import json
import re
import statistics
from typing import Optional

from google.adk.evaluation.conversation_scenarios import ConversationScenario
from google.adk.evaluation.eval_case import Invocation
from google.adk.evaluation.eval_metrics import EvalMetric, EvalStatus
from google.adk.evaluation.evaluator import EvaluationResult, PerInvocationResult

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_VALID = {"RANGING", "TRENDING", "UNCERTAIN"}


def _text(inv: Optional[Invocation]) -> str:
    if inv is None or inv.final_response is None:
        return ""
    return "".join(
        p.text or "" for p in inv.final_response.parts if getattr(p, "text", None)
    )


def _position(raw: str) -> str:
    """Extract RegimeVote.position from the agent's final response, '' on failure."""
    s = _FENCE_RE.sub("", raw or "").strip()
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return ""
    pos = str(obj.get("position", "")).strip().upper() if isinstance(obj, dict) else ""
    return pos if pos in _VALID else ""


def regime_position_match(
    eval_metric: EvalMetric,
    actual_invocations: list[Invocation],
    expected_invocations: Optional[list[Invocation]],
    conversation_scenario: Optional[ConversationScenario],
) -> EvaluationResult:
    if not expected_invocations:
        return EvaluationResult(
            overall_score=0.0, overall_eval_status=EvalStatus.NOT_EVALUATED
        )

    per: list[PerInvocationResult] = []
    for actual, expected in zip(actual_invocations, expected_invocations):
        got = _position(_text(actual))
        want = _text(expected).strip().upper()
        score = 1.0 if got and got == want else 0.0
        per.append(
            PerInvocationResult(
                actual_invocation=actual,
                expected_invocation=expected,
                score=score,
                eval_status=EvalStatus.PASSED if score else EvalStatus.FAILED,
            )
        )

    avg = statistics.mean(r.score for r in per) if per else 0.0
    threshold = eval_metric.criterion.threshold
    return EvaluationResult(
        overall_score=avg,
        overall_eval_status=EvalStatus.PASSED if avg >= threshold else EvalStatus.FAILED,
        per_invocation_results=per,
    )

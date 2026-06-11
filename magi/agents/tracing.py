"""Thin, backend-agnostic Langfuse tracing helper for the MAGI council.

This module is scaffolding for the hand-rolled orchestrator (not yet built).
It exposes a tiny surface the future orchestrator will import to trace one
council cycle and each seat call within it. Nothing here wires into a council
loop — it is import-only plumbing plus a one-trace smoke test
(`scratch/langfuse_smoke_test.py`).

Design constraints (operator-stated):
  * MANUAL span attribution only. No OTEL auto-instrumentation library.
    Melchior calls DeepSeek through the Anthropic SDK; auto-detection would
    mislabel that generation as Claude/anthropic. Every generation's `model`
    is therefore set EXPLICITLY by the caller and must never be inferred.
  * Fire-and-forget. A tracing failure must NEVER break a caller. Every
    Langfuse call is wrapped; if the client cannot init we hand back a no-op
    context (yields None) and the trading path proceeds unaffected.

Environment: the Langfuse SDK reads credentials from the process environment
(`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and the host from
`LANGFUSE_BASE_URL` — the current canonical var, which takes precedence over
the deprecated `LANGFUSE_HOST`). This module does NOT load `.env` itself; the
caller (orchestrator at startup, or the smoke test) is responsible for putting
those vars in the environment before the first `get_client()` call.

SDK note (langfuse 4.7.1): there is no `client.update_current_trace(...)` in
the v4 line. The root observation's `name` argument propagates to the trace
name, so naming the cycle's root span ``council-cycle:<id>`` is sufficient to
title the trace — no separate trace-update call is needed or available.
"""

import logging
from contextlib import contextmanager

log = logging.getLogger(__name__)

try:
    from langfuse import get_client
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def get_tracer():
    """Return the process-wide Langfuse client, or None if unavailable.

    Returns None when the SDK is not installed or the client cannot
    initialise (e.g. missing/blank keys). Never raises.
    """
    if not _AVAILABLE:
        return None
    try:
        return get_client()
    except Exception as e:  # noqa: BLE001 - tracing must never break the caller
        log.warning("Langfuse client init failed: %s", e)
        return None


@contextmanager
def trace_cycle(cycle_id: str, metadata: dict | None = None):
    """Open the root trace for one council cycle.

    Names the root observation ``council-cycle:<cycle_id>``; in langfuse 4.x
    that name propagates to the trace, so the trace displays with that title
    without a separate update call. Flushes on exit so a short-lived process
    (or the gate-driven orchestrator between cycles) ships the spans.

    `metadata` (optional) is attached to the ROOT span — used to stamp the
    cycle's config_version + config_snapshot at write time, so traces
    self-partition by config without a DB join. None -> no metadata attached.

    Yields the root observation object (or None if tracing is unavailable).
    A caller exception raised inside the ``with`` block propagates untouched —
    tracing never masks the trading path's own errors.
    """
    client = get_tracer()
    if client is None:
        yield None
        return

    obs_kwargs = {"as_type": "span", "name": f"council-cycle:{cycle_id}"}
    if metadata is not None:
        obs_kwargs["metadata"] = metadata
    try:
        cm = client.start_as_current_observation(**obs_kwargs)
    except Exception as e:  # noqa: BLE001
        log.warning("trace_cycle(%s) init failed: %s", cycle_id, e)
        yield None
        return

    try:
        with cm as root:
            yield root
    finally:
        try:
            client.flush()
        except Exception as e:  # noqa: BLE001
            log.warning("trace_cycle(%s) flush failed: %s", cycle_id, e)


@contextmanager
def trace_seat(seat: str, model: str, vendor: str, request_payload: dict):
    """Open a nested generation observation for one seat's model call.

    The generation is named after the seat, with `model` set EXPLICITLY
    (load-bearing — never inferred, so Melchior-via-Anthropic-SDK still reads
    as deepseek), `vendor` carried in metadata, and the request payload as
    input. Yields the generation object so the caller can, after the model
    returns::

        gen.update(output=<text>, usage_details={"input": ..., "output": ...})

    Yields None if tracing is unavailable. A caller exception inside the
    ``with`` block propagates untouched.
    """
    client = get_tracer()
    if client is None:
        yield None
        return

    try:
        cm = client.start_as_current_observation(
            as_type="generation",
            name=seat,
            model=model,                    # EXPLICIT — never inferred
            input=request_payload,
            metadata={"vendor": vendor},
        )
    except Exception as e:  # noqa: BLE001
        log.warning("trace_seat(%s) init failed: %s", seat, e)
        yield None
        return

    with cm as gen:
        yield gen


def current_trace_id():
    """Return the active trace ID as a string, or None.

    For stamping into `debate_records` later so a stored decision links back
    to its trace. Must be called inside an active `trace_cycle` context to
    return that cycle's id. Never raises.
    """
    client = get_tracer()
    if client is None:
        return None
    try:
        return client.get_current_trace_id()
    except Exception as e:  # noqa: BLE001
        log.warning("current_trace_id failed: %s", e)
        return None


def set_trace_tags(trace_id: str, tags: list):
    """Set tags on an EXISTING trace via the ingestion API (a `trace-create`
    event with the same trace id MERGES into the stored trace — this is the
    documented update path; SDK 4.7.1 has no public trace-update method).

    Tags are the only trace-level field the Metrics API / dashboard widgets
    can GROUP scores by (metadata is filter-only on traces, invisible to the
    scores views), so the cycle trigger is mirrored here as `trigger:<class>`.
    Fire-and-forget like everything in this module.
    """
    if not trace_id or not tags:
        return
    try:
        import os
        import uuid
        from datetime import datetime, timezone
        import requests
        base = (os.environ.get("LANGFUSE_BASE_URL") or "").rstrip("/")
        pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
        sec = os.environ.get("LANGFUSE_SECRET_KEY")
        if not (base and pub and sec):
            return
        now = datetime.now(timezone.utc).isoformat()
        batch = {"batch": [{
            "id": str(uuid.uuid4()),
            "type": "trace-create",
            "timestamp": now,
            "body": {"id": trace_id, "tags": [str(t) for t in tags]},
        }]}
        requests.post(f"{base}/api/public/ingestion", json=batch,
                      auth=(pub, sec), timeout=3)
    except Exception as e:  # noqa: BLE001 - tracing never breaks the caller
        log.warning("set_trace_tags(%s) failed: %s", trace_id, e)


def push_trace_scores(trace_id: str, scores: dict, comment: str | None = None):
    """Attach scores to an EXISTING trace via the public REST API (the SDK's
    score path needs an active span context; the outcome backfill runs hours
    after the trace closed, so REST-by-traceId is the right tool).

    `scores` is {name: value}: bool -> BOOLEAN, int/float -> NUMERIC,
    str -> CATEGORICAL; None values are skipped. Never raises — a Langfuse
    outage must never break the observer loop — but DOES return a delivery
    verdict (2026-06-11): True iff every score POST got HTTP 2xx, False on
    any drop (429/5xx/timeout). Callers that need the mirror to converge
    (observer.push_pending_outcome_scores) persist a receipt only on True
    and retry next pass; the old fire-and-forget ignored the response and
    silently lost scores under Langfuse Hobby-tier 429s.
    Returns True for the no-op cases (no trace_id / no scores / tracing
    unconfigured) — there is nothing to deliver, so nothing to retry.
    """
    if not trace_id or not scores:
        return True
    try:
        import os
        import requests
        base = (os.environ.get("LANGFUSE_BASE_URL") or "").rstrip("/")
        pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
        sec = os.environ.get("LANGFUSE_SECRET_KEY")
        if not (base and pub and sec):
            return True
        delivered = True
        for name, value in scores.items():
            if value is None:
                continue
            body = {"traceId": trace_id, "name": name}
            if isinstance(value, bool):
                body["value"] = 1 if value else 0
                body["dataType"] = "BOOLEAN"
            elif isinstance(value, str):
                body["value"] = value
                body["dataType"] = "CATEGORICAL"
            else:
                body["value"] = float(value)
                body["dataType"] = "NUMERIC"
            if comment:
                body["comment"] = comment
            resp = requests.post(f"{base}/api/public/scores", json=body,
                                 auth=(pub, sec), timeout=3)
            if resp.status_code >= 300:
                delivered = False
                log.warning("push_trace_scores(%s): %s dropped (HTTP %s)",
                            trace_id, name, resp.status_code)
        return delivered
    except Exception as e:  # noqa: BLE001 - tracing never breaks the caller
        log.warning("push_trace_scores(%s) failed: %s", trace_id, e)
        return False

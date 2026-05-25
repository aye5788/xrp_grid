"""ADAM — error capture for the MAGI council and grid bot.

In Neon Genesis Evangelion the MAGI supercomputer (Casper / Melchior /
Balthasar) is the deliberating council. ADAM is the progenitor — the
first being, the source the others descend from. Here the name fits the
role exactly: ADAM is the one module every other long-running service and
one-off script imports first, to wire up unhandled-exception capture
before any business logic runs. Everything else derives its error
visibility from it.

What ADAM is:
  * A thin, error-only wrapper around the Sentry SDK. It catches
    *unhandled* Python exceptions and ships the traceback to Sentry.

What ADAM is NOT (deliberately disabled):
  * No performance monitoring (traces_sample_rate=0.0)
  * No tracing, no profiling (profiles_sample_rate=0.0)
  * No session replay
  * No PII (send_default_pii=False)

What ADAM does NOT replace:
  * Operational alerting stays exactly as it was. `database.insert_alert`
    + `magi/notify.py` (ntfy push) + the dashboard ALERTS / AGENT HEALTH
    panels continue to own operational signalling. ADAM only adds a
    second, independent net for *crashes* — the unhandled exceptions that
    operational alerting was never meant to catch.

Usage:
  * Long-running services (scheduler, observer, dashboard):
        from magi import adam
        adam.init("scheduler")
  * One-off scripts (run, then exit):
        from magi import adam
        adam.init_oneshot("validate_schema")
    init_oneshot adds an atexit flush so events drain before the short-
    lived process exits.

Configuration is read from SENTRY_DSN in the environment (.env). If the
DSN is unset or empty, init() logs a warning and returns silently —
Sentry initialisation must never raise or block service startup.
"""

import atexit
import logging
import os
import socket
import subprocess

import sentry_sdk

# ADAM is imported at process start, often before the entrypoint's own
# load_dotenv() has run (scheduler has none of its own; dashboard loads it
# lazily). Load .env here too so SENTRY_DSN / SENTRY_AVAILABLE resolve
# regardless of caller. override=False (the default) means an explicit
# SENTRY_DSN= in the shell environment still wins — that is what makes the
# "disabled" path testable without editing .env.
try:
    from dotenv import load_dotenv
    load_dotenv("/root/xrp_grid/.env")
except Exception:
    # python-dotenv missing or .env unreadable must not break import.
    pass

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Matches the format used everywhere else in the repo (scheduler.py,
# observer.py) so the guarded basicConfig below is observably identical to
# the entrypoints' own logging setup and never changes their output.
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s — %(message)s"

log = logging.getLogger("magi.adam")

# For callers that want to branch on availability without invoking init().
SENTRY_AVAILABLE = bool(os.environ.get("SENTRY_DSN"))


def _git_release():
    """Return the short git SHA of the working tree, or 'unknown'.

    Best-effort and never raises — release tagging is nice-to-have, not a
    startup precondition.
    """
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).decode().strip()
        return sha or "unknown"
    except Exception:
        return "unknown"


# werkzeug request-parse failures we treat as external-scanner noise.
# werkzeug.serving logs these at ERROR when a client sends bytes that are not
# a valid HTTP request (a TLS ClientHello against the plaintext dev server,
# raw garbage, a truncated request line, etc.). They hit the public dashboard
# pre-auth and carry no operational signal, so they must never become Sentry
# issues. Matched as substrings of the logged message.
_WERKZEUG_NOISE_SUBSTRINGS = (
    "code 400, message Bad request",
    "code 400, message Bad HTTP/0.9 request",
    "Bad request version",
    "Bad request syntax",
)


def _event_message(event):
    """Best-effort extraction of the human-readable message from a Sentry
    event dict.

    The logging integration carries it under event['logentry']['message'];
    some events carry a top-level event['message']. Returns '' if neither is
    present or the event is not a dict.
    """
    if not isinstance(event, dict):
        return ""
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        msg = logentry.get("message")
        if isinstance(msg, str):
            return msg
    msg = event.get("message")
    if isinstance(msg, str):
        return msg
    return ""


def _exc_type_names(event, hint):
    """Collect exception type names from both the live hint exc_info and the
    event's serialized exception values, without importing anything.

    Used to spot socket/werkzeug request timeouts (TimeoutError) by name.
    """
    names = set()
    exc_info = hint.get("exc_info") if hint else None
    if exc_info and exc_info[0] is not None:
        names.add(getattr(exc_info[0], "__name__", ""))
    exception = event.get("exception") if isinstance(event, dict) else None
    if isinstance(exception, dict):
        for val in exception.get("values", []) or []:
            if isinstance(val, dict):
                t = val.get("type")
                if isinstance(t, str):
                    names.add(t)
    return names


def _mentions_werkzeug(event, hint):
    """True if the event's logger is werkzeug, or any frame in the serialized
    stacktrace / live traceback points at werkzeug.

    Inspects only the dicts/traceback Sentry already passes in — adam.py does
    not import werkzeug, so it never grows a hard dependency on it.
    """
    if isinstance(event, dict) and event.get("logger") == "werkzeug":
        return True
    exception = event.get("exception") if isinstance(event, dict) else None
    if isinstance(exception, dict):
        for val in exception.get("values", []) or []:
            stack = val.get("stacktrace") if isinstance(val, dict) else None
            if isinstance(stack, dict):
                for frame in stack.get("frames", []) or []:
                    if isinstance(frame, dict):
                        where = (frame.get("module") or "") + " " + (frame.get("filename") or "")
                        if "werkzeug" in where:
                            return True
    exc_info = hint.get("exc_info") if hint else None
    if exc_info and len(exc_info) >= 3 and exc_info[2] is not None:
        tb = exc_info[2]
        while tb is not None:
            frame = getattr(tb, "tb_frame", None)
            code = getattr(frame, "f_code", None)
            fname = getattr(code, "co_filename", "") or ""
            if "werkzeug" in fname:
                return True
            tb = tb.tb_next
    return False


def _is_dashboard_scanner_noise(event, hint):
    """True when the event is werkzeug request-parsing noise from external
    scanners hitting the public dashboard, and should be dropped.

    Two shapes seen in production:
      (a) werkzeug logs 'code 400, message Bad request version (...)' at
          ERROR for non-HTTP bytes (a TLS handshake on the plaintext port).
      (b) a socket TimeoutError raised inside werkzeug's request handler when
          a scanner opens a connection and never completes a request.
    """
    # Shape (a): werkzeug-logged bad-request lines.
    logger = event.get("logger") if isinstance(event, dict) else None
    if logger == "werkzeug":
        msg = _event_message(event)
        if any(sub in msg for sub in _WERKZEUG_NOISE_SUBSTRINGS):
            return True

    # Shape (b): request timeout originating in werkzeug's request handler.
    type_names = _exc_type_names(event, hint)
    if ("TimeoutError" in type_names or "timeout" in type_names) and _mentions_werkzeug(event, hint):
        return True

    return False


# --- Operational priority classification ------------------------------------
# Every event that survives the drops in before_send is stamped with
# priority ∈ {high, medium, low} so the Sentry issue list can sort/filter by
# operational severity instead of just recency. Plain string matching on
# logger name + exception type + message substring — no ML, no regex, no
# config file. First match wins; retune by amending the tuples below.
_PRIORITY_HIGH_LOGGERS = ("grid.engine",)
_PRIORITY_MEDIUM_LOGGERS = ("magi.council",)
_PRIORITY_MEDIUM_SCHEDULER_EXC = ("TimeoutError", "ConnectionError")

# grid/engine.py logs the invariant with spaces ("ONE_GRID INVARIANT
# VIOLATION at ..."); Sentry renders the issue title with underscores. Match
# both so the real captured event and any title-derived form classify high.
_ONE_GRID_VIOLATION_FORMS = (
    "ONE_GRID INVARIANT VIOLATION",
    "ONE_GRID_INVARIANT_VIOLATION",
)
_PRIORITY_HIGH_SUBSTRINGS = _ONE_GRID_VIOLATION_FORMS + (
    "[COUNCIL_COLLAPSED]",
)
_PRIORITY_MEDIUM_SUBSTRINGS = (
    "SAFE_DEFAULTS",
    "[AGENT_DEGRADED",
    "freshness_retry_failed",
    "rotation_skipped_degraded",
    "backfill_notify_failed",
)
_PRIORITY_HIGH_EXC_TYPES = ("ViolationError",)


def _classify_priority(event, hint):
    """Return 'high' | 'medium' | 'low' for a Sentry event.

    Pure function; reads only the event dict and hint dict that Sentry
    passes in. Rules are applied in order and the first match wins: high
    (real code defects / system-state failures) before medium (degradation /
    retry / fallback signals) before the low default.
    """
    logger = event.get("logger") or ""
    logentry = event.get("logentry") or {}
    message = ""
    if isinstance(logentry, dict):
        message = logentry.get("message") or ""

    # Pull exception type if present (last value in the chain).
    exc_type = ""
    exc_values = (event.get("exception") or {}).get("values") or []
    if exc_values and isinstance(exc_values, list):
        last = exc_values[-1] or {}
        if isinstance(last, dict):
            exc_type = last.get("type") or ""

    # --- HIGH ---
    if exc_type in _PRIORITY_HIGH_EXC_TYPES:
        return "high"
    if any(s in message for s in _PRIORITY_HIGH_SUBSTRINGS):
        return "high"
    if any(logger.startswith(p) for p in _PRIORITY_HIGH_LOGGERS) \
            and any(f in message for f in _ONE_GRID_VIOLATION_FORMS):
        return "high"
    # config_drift at critical severity (severity is carried in event 'level').
    level = event.get("level") or ""
    if "config_drift" in message and level == "critical":
        return "high"

    # --- MEDIUM ---
    if any(s in message for s in _PRIORITY_MEDIUM_SUBSTRINGS):
        return "medium"
    if any(logger.startswith(p) for p in _PRIORITY_MEDIUM_LOGGERS) \
            and "SAFE_DEFAULTS" in message:
        return "medium"
    if logger.startswith("magi.scheduler") and exc_type in _PRIORITY_MEDIUM_SCHEDULER_EXC:
        return "medium"

    # --- LOW (default) ---
    return "low"


def before_send(event, hint):
    """Drop events that are not real, actionable errors.

    1. KeyboardInterrupt (Ctrl-C) and SystemExit (sys.exit / argparse exits)
       are clean process termination, not crashes — they would otherwise
       flood Sentry with noise from every interrupted run.
    2. werkzeug request-parse noise from external scanners hitting the public
       dashboard pre-auth (see _is_dashboard_scanner_noise).

    Returns the event to keep it, or None to drop it. Top-level so it is
    directly unit-testable.
    """
    exc_info = hint.get("exc_info") if hint else None
    if exc_info:
        exc_type = exc_info[0]
        if exc_type is not None and issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return None

    if _is_dashboard_scanner_noise(event, hint):
        return None

    # Stamp an operational priority so the Sentry issue list can sort/filter
    # by severity instead of just recency. Tags are conventionally a list of
    # [key, value] pairs in the SDK event dict. Don't clobber a priority a
    # future caller may have already pre-stamped.
    priority = _classify_priority(event, hint)
    event.setdefault("tags", [])
    existing = [t for t in event["tags"]
                if (isinstance(t, (list, tuple)) and len(t) == 2 and t[0] == "priority")
                or (isinstance(t, dict) and t.get("key") == "priority")]
    if not existing:
        event["tags"].append(["priority", priority])

    return event


def _ensure_logging():
    """Guarantee the init log line is visible without altering existing setup.

    If the root logger already has handlers (every long-running service
    configures logging before calling init), this is a no-op. If nothing
    is configured (a bare `python -c`, or a script with no logging setup),
    configure with the repo-standard format so the confirmation/warning
    line actually surfaces.
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)


def init(service_name: str):
    """Initialise ADAM/Sentry for a long-running service.

    Reads SENTRY_DSN from the environment. If it is unset or empty, logs a
    warning and returns — never raises, so a missing/blanked DSN can never
    block startup. Otherwise initialises Sentry in error-only mode and tags
    every event with service=<service_name>.
    """
    _ensure_logging()

    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        log.warning(
            "ADAM: SENTRY_DSN unset or empty — error tracking disabled for %s",
            service_name,
        )
        return

    try:
        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=0.0,      # NO performance monitoring
            profiles_sample_rate=0.0,    # NO profiling
            sample_rate=1.0,             # capture 100% of exceptions
            send_default_pii=False,      # do NOT send PII
            environment="production",
            release=_git_release(),
            server_name=socket.gethostname(),
            max_breadcrumbs=50,
            attach_stacktrace=True,
            before_send=before_send,
        )
        sentry_sdk.set_tag("service", service_name)
    except Exception as e:
        # Never let Sentry setup block startup. Log it (not silent) so the
        # failure is visible, then carry on with error tracking disabled.
        log.warning("ADAM: Sentry init failed for %s — error tracking disabled (%s)",
                    service_name, e)
        return

    log.info("ADAM initialised for %s", service_name)


def init_oneshot(script_name: str):
    """Initialise ADAM/Sentry for a short-lived one-off script.

    Same as init(), plus an atexit hook that flushes pending events (2s
    budget) so a fast-exiting process does not drop the event it just
    captured.
    """
    init(script_name)
    # Register regardless of init outcome — flush() on an uninitialised
    # client is a harmless no-op.
    atexit.register(sentry_sdk.flush, timeout=2.0)

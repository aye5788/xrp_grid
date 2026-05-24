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


def before_send(event, hint):
    """Drop events that are normal shutdowns, not real errors.

    KeyboardInterrupt (Ctrl-C) and SystemExit (sys.exit / argparse exits)
    are clean process termination, not crashes — they would otherwise
    flood Sentry with noise from every interrupted run. Top-level so it is
    directly unit-testable.

    Returns the event to keep it, or None to drop it.
    """
    exc_info = hint.get("exc_info") if hint else None
    if exc_info:
        exc_type = exc_info[0]
        if exc_type is not None and issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return None
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

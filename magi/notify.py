"""
ntfy.sh push notification layer.

Single entry point: send_ntfy(title, body, severity, agent_id=None, category=None).
Reads NTFY_TOPIC_URL from .env on each call (cheap, allows rotation without
restart). If unset, silently no-ops — the system must function without this layer.

The topic at https://ntfy.sh/<topic> is publicly readable to anyone who guesses
the topic name. Treat the notification body as untrusted. Do NOT include API
keys, balance numbers, raw 402 payloads, or any other operationally sensitive
data. The notification should be enough to tell the operator "open the
dashboard" — not enough to act on directly.

Severity → ntfy priority:
    critical → 5 (max — bypasses iOS DND)
    warning  → 3 (default — buzz, no DND override)
    info     → no fire (returns False)

Failure is silent. Network errors, timeouts, bad responses, or ntfy.sh going
down NEVER propagate to the caller. Alert capture is more important than the
notification.
"""

import logging
import os

import requests
from dotenv import load_dotenv

log = logging.getLogger(__name__)

_NTFY_TIMEOUT_SEC = 3
_BODY_MAX_CHARS = 200

_SEVERITY_PRIORITY = {
    'critical': 5,
    'warning':  3,
    'warn':     3,
}


def send_ntfy(title, body, severity, agent_id=None, category=None):
    """
    Fire a push notification.

    Returns True if a request was sent and accepted (HTTP 2xx), False
    otherwise. Never raises.
    """
    if severity not in _SEVERITY_PRIORITY:
        return False

    load_dotenv('/root/xrp_grid/.env', override=False)
    topic_url = (os.environ.get('NTFY_TOPIC_URL') or '').strip()
    if not topic_url:
        return False

    priority = _SEVERITY_PRIORITY[severity]
    safe_body = (body or '')[:_BODY_MAX_CHARS]
    safe_title = (title or 'MAGI alert')[:120]

    tags = []
    if agent_id:
        tags.append(str(agent_id))
    if category:
        tags.append(str(category))

    headers = {
        'Title':    safe_title,
        'Priority': str(priority),
    }
    if tags:
        headers['Tags'] = ','.join(tags)

    try:
        resp = requests.post(
            topic_url,
            data=safe_body.encode('utf-8'),
            headers=headers,
            timeout=_NTFY_TIMEOUT_SEC,
        )
        if 200 <= resp.status_code < 300:
            return True
        log.warning("ntfy non-2xx: status=%s body=%s",
                    resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.warning("ntfy send failed: %r", e)
        return False

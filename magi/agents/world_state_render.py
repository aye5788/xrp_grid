"""Single shared world_state -> prompt renderer for the council seat-callers.

All three standalone seat-callers (casper_gemini.py, melchior_deepseek.py,
balthasar_claude.py) render the per-cycle world_state through THIS function so the
world_state block they put in front of their model is byte-identical — no
per-seat drift, deterministic for trace/debug legibility.

Pretty JSON (indent=2, sort_keys=True) is used over flattened "key: value" lines
because all three vendors (Gemini, DeepSeek, Claude) parse explicit nested JSON
more reliably than ambiguous flattened pairs, and sort_keys makes the render
order stable across calls.

The function returns ONLY the JSON body. Each caller wraps it with its own
"world_state:\\n" framing / instruction line (kept close to that seat's proven
probe), so the shared, byte-identical part is the world_state itself.
"""

from __future__ import annotations

import json


def render_world_state(world_state: dict) -> str:
    """Render a world_state dict as deterministic pretty JSON for the user turn.

    json.dumps(world_state, indent=2, sort_keys=True), default=str so any
    non-JSON-native values (e.g. datetimes that slipped in) serialize rather than
    raising.
    """
    return json.dumps(world_state, indent=2, sort_keys=True, default=str)

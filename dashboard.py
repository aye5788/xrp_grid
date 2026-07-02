from flask import Flask, jsonify, render_template_string, request, session, redirect, url_for
import logging
import os
from datetime import datetime, timezone, timedelta
from database import (
    get_latest_indicators, get_current_grid_state,
    get_latest_inventory, get_recent_magi_decisions,
    get_recent_grid_orders
)
from grid.engine import GridEngine
from grid.pnl import get_pnl_snapshot
from magi.learning import run_learning_cycle
from guardrails import check_all_guardrails, kill_switch_active
from config import KILL_SWITCH_FILE, MAX_INVENTORY_USD

log = logging.getLogger('dashboard')
app = Flask(__name__)

# All stored timestamps are naive UTC. Display-only conversion to US Eastern —
# internals stay UTC. America/New_York handles EST/EDT automatically, so the
# wall-clock time always matches the operator's local clock and the %Z label
# (EST in winter, EDT in summer) is always correct.
from zoneinfo import ZoneInfo
_ET_ZONE = ZoneInfo('America/New_York')


def _to_et(value, fmt='%Y-%m-%d %H:%M'):
    """Convert a naive-UTC ISO string (or datetime) to an Eastern display
    string with a zone label. Returns '' for empty and the original value if
    it can't be parsed as a timestamp."""
    if value is None or value == '':
        return ''
    dt = value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        except ValueError:
            return value
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        et = dt.astimezone(_ET_ZONE)
        return f"{et.strftime(fmt)} {et.strftime('%Z')}"
    except Exception:
        return str(value)


app.jinja_env.filters['et'] = _to_et


def _read_env_file_var(key, path='/root/xrp_grid/.env'):
    """Read KEY=VALUE from the .env file on disk. The dashboard's systemd unit
    does not source .env, so the file is the authoritative cross-process source
    for the configured trading mode (the live bot reads the same file)."""
    try:
        with open(path) as f:
            for line in f:
                s = line.strip()
                if s.startswith(key + '='):
                    return s.split('=', 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def _configured_live():
    """True iff the live bot's three-factor gate is satisfied on disk (env var
    in .env + CONFIRM_LIVE file + matching token). Mirrors grid/engine.py's gate
    without touching the dashboard's own (paper) engine."""
    try:
        from config import (LIVE_CONFIRMATION_FILE, LIVE_CONFIRMATION_TOKEN,
                            LIVE_CONFIRMATION_ENV_VAR, LIVE_CONFIRMATION_ENV_VALUE)
        env_val = (os.environ.get(LIVE_CONFIRMATION_ENV_VAR)
                   or _read_env_file_var(LIVE_CONFIRMATION_ENV_VAR))
        gate1 = env_val == LIVE_CONFIRMATION_ENV_VALUE
        gate2 = os.path.isfile(LIVE_CONFIRMATION_FILE)
        gate3 = gate2 and open(LIVE_CONFIRMATION_FILE).read() == LIVE_CONFIRMATION_TOKEN
        return bool(gate1 and gate2 and gate3)
    except Exception:
        return False


def _grid_status(last_grid_action, scheduler_alive, ks_active):
    """Runtime status of the trading grid for the GRID STATUS box. Sourced from
    the shared DB (open resting orders) + the on-disk live gate, so it reflects
    the live bot (magi.service), not the dashboard's own paper engine."""
    open_buys = open_sells = 0
    try:
        from database import get_conn
        conn = get_conn()
        rows = conn.execute(
            "SELECT side FROM grid_orders WHERE status='open'"
        ).fetchall()
        conn.close()
        open_buys = sum(1 for r in rows if r['side'] == 'buy')
        open_sells = sum(1 for r in rows if r['side'] == 'sell')
    except Exception as e:
        log.warning("grid_status open-order read failed: %r", e)
    open_total = open_buys + open_sells

    live = _configured_live()
    action = (last_grid_action or '').upper()
    if ks_active:
        label, color, active, detail = 'HALTED', '#ff4444', False, 'kill switch active'
    elif not scheduler_alive:
        label, color, active, detail = 'DOWN', '#ff4444', False, 'scheduler not running'
    elif action == 'HALT':
        label, color, active, detail = 'HALTED', '#ff4444', False, 'council/rule HALT'
    elif open_total == 0:
        label, color, active, detail = 'NO ORDERS', '#ffaa00', False, 'nothing resting on book'
    elif action in ('GRID_PAUSE', 'PAUSE'):
        label, color, active, detail = 'PAUSED', '#ffaa00', False, 'council PAUSE — orders still resting'
    else:
        label, color, active, detail = 'ACTIVE', '#00ff88', True, 'orders resting on book'

    return {
        'label': label, 'color': color, 'active': active, 'detail': detail,
        'mode': 'LIVE' if live else 'PAPER',
        'mode_color': '#ff4444' if live else '#ffaa00',
        'open_buys': open_buys, 'open_sells': open_sells, 'open_total': open_total,
    }


_LIVE = os.environ.get("MAGI_LIVE_CONFIRM") == "YES"
engine = GridEngine(paper=not _LIVE)
engine.load_state()

# Shared secret for /api/trigger_magi. Set MAGI_TRIGGER_TOKEN in .env to require
# the token as an X-Magi-Token header or ?token= query param. If unset, the
# endpoint remains open — acceptable when access is restricted by network topology
# (e.g. localhost-only). External exposure requires the token to be set.
MAGI_TRIGGER_TOKEN = os.environ.get('MAGI_TRIGGER_TOKEN', '')

# --- Auth: signed-cookie session (replaces nginx basic auth) ---
# The public dashboard is served by the cloudflared tunnel straight to this
# Flask app (nginx is not in that path), so app-side auth is what actually
# gates access. Single shared password; cookie persists 1 year so the
# operator stays logged in across browser sessions until explicit logout.
app.secret_key = os.environ.get('SECRET_KEY', '')
app.permanent_session_lifetime = timedelta(days=365)
DASHBOARD_PASSWORD = os.environ.get('DASHBOARD_PASSWORD', '')

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MAGI — Login</title>
<style>
  body{background:#0a0a0a;color:#00ffcc;font-family:monospace;display:flex;
       align-items:center;justify-content:center;height:100vh;margin:0;}
  .box{border:2px solid #00ff88;border-radius:6px;padding:32px 40px;
       background:#0f0f0f;text-align:center;box-shadow:0 0 24px #00ff8833;}
  h1{font-size:1.2em;letter-spacing:2px;margin:0 0 20px;}
  input[type=password]{background:#000;border:1px solid #00ff88;color:#00ffcc;
       font-family:monospace;padding:10px;font-size:1em;width:240px;border-radius:4px;}
  button{margin-top:16px;background:#00ff8822;color:#00ffcc;border:2px solid #00ff88;
       padding:10px 28px;font-family:monospace;font-size:1em;font-weight:bold;
       cursor:pointer;border-radius:4px;width:100%;}
  .err{color:#ff6666;margin-top:14px;font-size:0.85em;}
</style></head>
<body>
  <form class="box" method="POST" action="/login">
    <h1>⬡ MAGI DASHBOARD</h1>
    <input type="password" name="password" placeholder="password" autofocus
           autocomplete="current-password"/>
    <button type="submit">ENTER</button>
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
  </form>
</body></html>
"""


@app.before_request
def _require_login():
    # Public endpoints: login form/post, logout, and static assets.
    if request.endpoint in ('login', 'logout', 'static'):
        return None
    # Preserve token-authenticated automation (e.g. curl POST
    # /api/trigger_magi with X-Magi-Token / ?token=).
    token = request.headers.get('X-Magi-Token', '') or request.args.get('token', '')
    if MAGI_TRIGGER_TOKEN and token == MAGI_TRIGGER_TOKEN:
        return None
    if session.get('authed'):
        return None
    if request.path.startswith('/api/'):
        return jsonify({'ok': False, 'error': 'authentication required'}), 401
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if DASHBOARD_PASSWORD and request.form.get('password', '') == DASHBOARD_PASSWORD:
            session.permanent = True
            session['authed'] = True
            return redirect(url_for('index'))
        return render_template_string(LOGIN_TEMPLATE, error='Incorrect password.'), 401
    return render_template_string(LOGIN_TEMPLATE, error=None)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>MAGI — XRP Grid Bot</title>
    <!-- meta http-equiv="refresh" removed: it caused the chart iframe
         to disconnect/reconnect every 30s. Replaced by a JS soft-refresh
         at the bottom of the page that fetches / in the background and
         swaps in the changed data sections; chart iframe stays untouched. -->
    <!-- NGE typefaces: Michroma (Eurostile-equivalent, used for NERV signage in the show) for h1; Helvetica bold (NERV HUD interface text) for h2; VT323 (CRT readout) for big numerical values. Source: fontsinuse.com/uses/28760/neon-genesis-evangelion -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Michroma&family=VT323&display=swap" rel="stylesheet">
    <style>
      :root {
        --bg: #000000;
        --panel-bg: #0a0a0a;
        --magi-cyan: #00d4d4;
        --magi-cyan-fill: #1a8c8c;
        --magi-orange: #ff6600;
        --magi-orange-bright: #ff9933;
        --magi-red: #cc0000;
        --magi-red-bright: #ff3333;
        --magi-text: #ff9933;
        --magi-text-dim: #cc7722;
        --magi-grid: #221100;
        --signal-green: #00ff66;
        --signal-amber: #ffaa00;
        --signal-red: #ff3333;
      }

      body {
        background: var(--bg);
        background-image:
          repeating-linear-gradient(0deg,
            transparent 0, transparent 39px,
            var(--magi-grid) 39px, var(--magi-grid) 40px),
          repeating-linear-gradient(90deg,
            transparent 0, transparent 39px,
            var(--magi-grid) 39px, var(--magi-grid) 40px);
        color: var(--magi-text);
        font-family: "Courier New", "Consolas", "Liberation Mono", monospace;
        margin: 0;
        padding: 20px;
        letter-spacing: 0.5px;
      }

      h1 {
        color: var(--magi-orange-bright);
        font-family: "Michroma", "Eurostile", "Helvetica Neue", "Helvetica", "Arial", sans-serif;
        font-weight: 400;
        font-size: 20px;
        letter-spacing: 5px;
        text-transform: uppercase;
        border-bottom: 2px solid var(--magi-orange);
        padding-bottom: 8px;
        margin: 0 0 12px 0;
      }

      h2 {
        color: var(--magi-orange);
        font-family: "Helvetica Neue", "Helvetica", "Arial", sans-serif;
        font-weight: 700;
        font-size: 13px;
        letter-spacing: 3px;
        text-transform: uppercase;
        margin: 28px 0 12px 0;
        border-left: 4px solid var(--magi-orange);
        padding-left: 10px;
      }

      /* Generic cards (Market, Grid State, Live P&L, Inventory, Costs) */
      .card {
        background: var(--panel-bg);
        border: 2px solid var(--magi-orange);
        padding: 14px 18px;
        margin-bottom: 8px;
        position: relative;
      }
      .card::before {
        content: "";
        position: absolute;
        top: -2px; left: -2px;
        width: 12px; height: 12px;
        border-top: 2px solid var(--magi-orange-bright);
        border-left: 2px solid var(--magi-orange-bright);
      }
      .card::after {
        content: "";
        position: absolute;
        bottom: -2px; right: -2px;
        width: 12px; height: 12px;
        border-bottom: 2px solid var(--magi-orange-bright);
        border-right: 2px solid var(--magi-orange-bright);
      }
      .card .label, .card .field-label {
        color: var(--magi-text-dim);
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 2px;
        margin-bottom: 4px;
      }
      .card .value {
        color: var(--magi-orange-bright);
        font-size: 28px;
        font-weight: 400;
        font-family: "VT323", "Courier New", monospace;
        letter-spacing: 1px;
        line-height: 1.0;
      }
      .card .sub {
        color: var(--magi-text-dim);
        font-size: 11px;
        margin-top: 2px;
      }

      /* Multi-card rows (3-up, 2-up) */
      .row {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 12px;
      }
      .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

      /* AGENT COUNCIL — the three MAGI panels */
      .council-row {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 16px;
        margin-bottom: 20px;
      }
      .council-card {
        background: #0a0a0a;
        background-image: repeating-linear-gradient(
          to bottom,
          transparent 0px, transparent 2px,
          rgba(255, 153, 0, 0.05) 2px, rgba(255, 153, 0, 0.05) 3px
        );
        border: 1px solid #ff9900;
        outline: 1px solid #331100;
        outline-offset: 2px;
        color: #ffaa00;
        padding: 20px 22px;
        position: relative;
        font-family: "Courier New", "Consolas", monospace;
      }
      .council-card .council-name {
        font-family: "Courier New", monospace;
        color: #ff9900;
        font-size: 13px;
        letter-spacing: 4px;
        text-transform: uppercase;
        font-weight: bold;
        padding-bottom: 10px;
        border-bottom: 1px solid #663300;
        margin-bottom: 16px;
        display: block;
      }
      .council-card .council-pos {
        font-family: "Arial Black", "Helvetica", sans-serif;
        font-weight: 900;
        font-size: 38px;
        letter-spacing: 6px;
        color: #ffaa00;
        text-shadow: 0 0 12px rgba(255, 170, 0, 0.5);
        margin: 10px 0 18px 0;
        text-transform: uppercase;
        line-height: 1.1;
        display: block;
      }
      .council-card .conv-track {
        display: flex;
        gap: 4px;
        height: 16px;
        background: transparent;
        border: none;
        margin: 10px 0 16px 0;
        align-items: center;
      }
      .council-card .conv-track::before {
        content: "CONV";
        color: #cc7722;
        font-size: 10px;
        letter-spacing: 2px;
        margin-right: 10px;
        font-family: "Courier New", monospace;
      }
      .council-card .conv-fill {
        display: none;
      }
      .council-card .conv-seg {
        flex: 1;
        height: 100%;
        border: 1px solid #ff9900;
        background: transparent;
        box-sizing: border-box;
      }
      .council-card .conv-seg.active {
        background: #ffaa00;
        box-shadow: inset 0 0 6px rgba(255, 170, 0, 0.7), 0 0 4px rgba(255, 170, 0, 0.4);
      }
      .council-card .council-crux {
        font-style: italic;
        color: #cc8833;
        font-size: 12px;
        background: rgba(255, 153, 0, 0.05);
        border-left: 3px solid #ff9900;
        padding: 10px 14px;
        margin: 14px 0;
        line-height: 1.5;
      }
      .council-card .council-evidence {
        list-style: none;
        padding: 0;
        margin: 10px 0 0 0;
        color: #cc7722;
        font-size: 11px;
        font-family: "Courier New", monospace;
        line-height: 1.6;
      }
      .council-card .council-evidence li {
        padding: 2px 0 2px 18px;
        position: relative;
      }
      .council-card .council-evidence li::before {
        content: "›";
        position: absolute;
        left: 0;
        color: #ff9900;
        font-weight: bold;
      }
      .deadlock-banner {
        background: #220000;
        border: 1px solid #cc0000;
        outline: 1px solid #440000;
        outline-offset: 2px;
        color: #ff3333;
        font-family: "Arial Black", sans-serif;
        letter-spacing: 4px;
        text-align: center;
        padding: 12px;
        margin: 10px 0;
        text-transform: uppercase;
      }
      .override-line {
        color: #ff6633;
        font-size: 11px;
        letter-spacing: 2px;
        text-transform: uppercase;
        font-family: "Courier New", monospace;
      }

      /* Signal colors for trading positions (semantic — keep) */
      .pos-TRENDING, .pos-HALT, .risk-HALT, .risk-PAUSE_LONGS {
        color: var(--signal-red);
      }
      .pos-MAINTAIN, .pos-WIDEN, .pos-TIGHTEN, .pos-RECENTRE {
        color: var(--signal-amber);
      }
      .pos-RANGE, .pos-SIDEWAYS, .pos-CLEAR, .risk-CLEAR {
        color: var(--signal-green);
      }

      /* Tables (recent orders, costs per-agent, shadow variants) */
      table {
        width: 100%;
        border-collapse: collapse;
        background: var(--panel-bg);
        border: 2px solid var(--magi-orange);
        font-family: "Courier New", monospace;
        font-size: 12px;
      }
      th {
        background: var(--magi-orange);
        color: #000;
        text-align: left;
        padding: 8px 10px;
        font-family: "Arial Black", sans-serif;
        letter-spacing: 2px;
        text-transform: uppercase;
        font-size: 11px;
      }
      td {
        color: var(--magi-text);
        padding: 6px 10px;
        border-bottom: 1px solid #2a1500;
      }
      tr:hover td { background: #1a0d00; }

      /* Manual actions (buttons) */
      button, .button {
        background: var(--panel-bg);
        color: var(--magi-orange-bright);
        border: 2px solid var(--magi-orange);
        padding: 10px 18px;
        font-family: "Arial Black", sans-serif;
        font-size: 12px;
        letter-spacing: 3px;
        text-transform: uppercase;
        cursor: pointer;
        margin: 4px;
      }
      button:hover, .button:hover {
        background: var(--magi-orange);
        color: #000;
      }
      button.danger, .button.danger {
        color: var(--magi-red-bright);
        border-color: var(--magi-red);
      }
      button.danger:hover, .button.danger:hover {
        background: var(--magi-red);
        color: #fff;
      }

      /* Council Accuracy + Outcome Attribution cards */
      .accuracy-card, .evo-card, .attribution-card {
        background: var(--panel-bg);
        border: 2px solid var(--magi-orange);
        padding: 14px 18px;
      }
      .accuracy-card .agent-name {
        font-family: "Arial Black", sans-serif;
        color: var(--magi-orange-bright);
        letter-spacing: 3px;
        margin-bottom: 8px;
      }
      .evolution-grid, .attribution-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
      }

      /* Debate log collapsible */
      details.debate-row {
        background: var(--panel-bg);
        border: 1px solid var(--magi-orange);
        padding: 8px 12px;
        margin: 4px 0;
      }
      details.debate-row summary {
        color: var(--magi-orange-bright);
        cursor: pointer;
        font-family: "Courier New", monospace;
        font-size: 12px;
      }

      /* Header status indicators */
      .status-bar {
        color: var(--magi-text-dim);
        font-size: 11px;
        letter-spacing: 1px;
        margin-bottom: 16px;
      }
      .status-dot {
        display: inline-block;
        width: 8px; height: 8px;
        background: var(--signal-green);
        border-radius: 50%;
        margin-right: 4px;
        vertical-align: middle;
      }
      .status-dot.warn { background: var(--signal-amber); }
      .status-dot.fail { background: var(--signal-red); }

      /* Inline status text in the header bar. Previously these spans had no
         CSS rule and inherited color:#666 from the parent div (the
         "black-on-black" effect). OK = bright green so it's readable at
         a glance; ERR = red so it only pops when something's wrong. */
      .status-ok  { color: var(--signal-green); font-weight: 600; }
      .status-err { color: var(--signal-red);   font-weight: 700; }

      /* SVG charts inherit colors */
      svg text { fill: var(--magi-text); font-family: "Courier New", monospace; font-size: 10px; }
      svg .axis-line { stroke: var(--magi-text-dim); }
      svg .data-line { stroke: var(--magi-orange-bright); fill: none; stroke-width: 2; }

      /* Side-by-side pair container — stacks back to single column on narrow viewports */
      .dash-row {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 16px;
        margin-bottom: 12px;
        align-items: start;
      }
      .dash-cell { min-width: 0; }  /* prevent grid cell from overflowing on long content */
      @media (max-width: 900px) {
        .dash-row { grid-template-columns: 1fr; }
      }

      /* AGENT HEALTH tile — persistent degradation chips (Dim 1 of the
         BYOK contingency design). Three chips left-to-right in the same
         triangle order as the hero: CASPER · MELCHIOR · BALTHASAR. */
      .agent-health-panel {
        background: var(--panel-bg);
        border: 2px solid var(--magi-orange);
        padding: 12px 16px;
        margin-bottom: 16px;
        position: relative;
      }
      .agent-health-panel::before {
        content: "";
        position: absolute;
        top: -2px; left: -2px;
        width: 12px; height: 12px;
        border-top: 2px solid var(--magi-orange-bright);
        border-left: 2px solid var(--magi-orange-bright);
      }
      .agent-health-panel::after {
        content: "";
        position: absolute;
        bottom: -2px; right: -2px;
        width: 12px; height: 12px;
        border-bottom: 2px solid var(--magi-orange-bright);
        border-right: 2px solid var(--magi-orange-bright);
      }
      .agent-health-title {
        color: var(--magi-text-dim);
        font-size: 11px;
        letter-spacing: 2px;
        text-transform: uppercase;
        margin-bottom: 8px;
        font-family: "Helvetica Neue", "Helvetica", "Arial", sans-serif;
        font-weight: 700;
      }
      .agent-health-chips {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 10px;
      }
      .agent-chip {
        border: 2px solid var(--magi-orange);
        background: #050505;
        padding: 10px 12px;
        font-family: "Helvetica Neue", "Helvetica", "Arial", sans-serif;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .agent-chip .chip-head {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .agent-chip .chip-dot {
        display: inline-block;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        flex-shrink: 0;
      }
      .agent-chip .chip-name {
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 3px;
        text-transform: uppercase;
        color: var(--magi-orange-bright);
      }
      .agent-chip .chip-status {
        font-size: 11px;
        letter-spacing: 1.5px;
        text-transform: uppercase;
        margin-left: auto;
        font-weight: 700;
      }
      .agent-chip .chip-degraded {
        font-family: "VT323", "Courier New", monospace;
        font-size: 18px;
        color: var(--magi-text);
        letter-spacing: 1px;
      }
      .agent-chip .chip-model {
        font-family: "Courier New", monospace;
        font-size: 10px;
        color: var(--magi-text-dim);
        letter-spacing: 0.5px;
        word-break: break-all;
      }
      .agent-chip.health-green {
        border-color: var(--signal-green);
        box-shadow: 0 0 6px rgba(0, 255, 102, 0.18);
      }
      .agent-chip.health-green .chip-dot   { background: var(--signal-green); box-shadow: 0 0 6px rgba(0, 255, 102, 0.7); }
      .agent-chip.health-green .chip-status{ color: var(--signal-green); }
      .agent-chip.health-yellow {
        border-color: var(--signal-amber);
        box-shadow: 0 0 6px rgba(255, 170, 0, 0.25);
      }
      .agent-chip.health-yellow .chip-dot   { background: var(--signal-amber); box-shadow: 0 0 6px rgba(255, 170, 0, 0.7); }
      .agent-chip.health-yellow .chip-status{ color: var(--signal-amber); }
      .agent-chip.health-red {
        border-color: var(--signal-red);
        box-shadow: 0 0 10px rgba(255, 51, 51, 0.45);
        animation: chip-red-pulse 1.4s ease-in-out infinite alternate;
      }
      .agent-chip.health-red .chip-dot   { background: var(--signal-red); box-shadow: 0 0 6px rgba(255, 51, 51, 0.9); }
      .agent-chip.health-red .chip-status{ color: var(--signal-red); }
      @keyframes chip-red-pulse {
        from { box-shadow: 0 0 6px rgba(255, 51, 51, 0.4); }
        to   { box-shadow: 0 0 14px rgba(255, 51, 51, 0.85); }
      }

      /* READINESS panel — live readiness gate set (lifetime).
         Sits below INVENTORY+PAPER P&L, above LIVE CHART. Matches the
         AGENT HEALTH structural pattern (panel-with-corner-marks,
         chip grid). */
      .readiness-panel {
        background: var(--panel-bg);
        border: 2px solid var(--magi-orange);
        padding: 14px 18px;
        margin-bottom: 16px;
        position: relative;
      }
      .readiness-panel::before {
        content: "";
        position: absolute;
        top: -2px; left: -2px;
        width: 12px; height: 12px;
        border-top: 2px solid var(--magi-orange-bright);
        border-left: 2px solid var(--magi-orange-bright);
      }
      .readiness-panel::after {
        content: "";
        position: absolute;
        bottom: -2px; right: -2px;
        width: 12px; height: 12px;
        border-bottom: 2px solid var(--magi-orange-bright);
        border-right: 2px solid var(--magi-orange-bright);
      }
      .readiness-section {
        margin-top: 4px;
        margin-bottom: 18px;
      }
      .readiness-section:last-child { margin-bottom: 0; }
      .readiness-header {
        display: flex;
        align-items: baseline;
        gap: 14px;
        margin-bottom: 8px;
        flex-wrap: wrap;
      }
      .readiness-title {
        font-family: "Helvetica Neue", "Helvetica", "Arial", sans-serif;
        font-weight: 700;
        font-size: 12px;
        letter-spacing: 3px;
        text-transform: uppercase;
        color: var(--magi-orange-bright);
      }
      .readiness-meta {
        font-family: "Courier New", monospace;
        font-size: 11px;
        color: var(--magi-text-dim);
        letter-spacing: 1px;
      }
      .readiness-verdict {
        padding: 4px 12px;
        font-family: "Arial Black", sans-serif;
        font-weight: 900;
        font-size: 13px;
        letter-spacing: 3px;
        text-transform: uppercase;
        border: 2px solid currentColor;
      }
      .verdict-renew, .verdict-green {
        color: var(--signal-green);
        background: rgba(0, 255, 102, 0.10);
        box-shadow: 0 0 10px rgba(0, 255, 102, 0.35);
      }
      .verdict-marginal, .verdict-yellow {
        color: var(--signal-amber);
        background: rgba(255, 170, 0, 0.10);
        box-shadow: 0 0 10px rgba(255, 170, 0, 0.35);
      }
      .verdict-do_not_renew, .verdict-red {
        color: var(--signal-red);
        background: rgba(255, 51, 51, 0.12);
        box-shadow: 0 0 12px rgba(255, 51, 51, 0.45);
      }
      .gate-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
        gap: 8px;
      }
      .gate-chip {
        border: 1px solid var(--magi-orange);
        background: #050505;
        padding: 8px 10px;
        font-family: "Helvetica Neue", "Helvetica", "Arial", sans-serif;
        display: flex;
        flex-direction: column;
        gap: 3px;
        cursor: pointer;
      }
      .gate-chip:hover { background: #0d0d0d; }
      .gate-chip .gate-head {
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .gate-chip .gate-id {
        font-family: "Courier New", monospace;
        font-weight: 700;
        font-size: 11px;
        color: var(--magi-orange-bright);
        letter-spacing: 1px;
        min-width: 24px;
      }
      .gate-chip .gate-pill {
        margin-left: auto;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 1.5px;
        padding: 1px 6px;
        border: 1px solid currentColor;
      }
      .gate-chip .gate-value {
        font-family: "VT323", "Courier New", monospace;
        font-size: 16px;
        color: var(--magi-text);
        letter-spacing: 1px;
        line-height: 1.1;
      }
      .gate-chip .gate-label {
        font-family: "Courier New", monospace;
        font-size: 10px;
        color: var(--magi-text-dim);
        letter-spacing: 0.5px;
        line-height: 1.3;
      }
      .gate-pass {
        border-color: var(--signal-green);
      }
      .gate-pass .gate-pill   { color: var(--signal-green); }
      .gate-pass .gate-value  { color: var(--signal-green); }
      .gate-fail {
        border-color: var(--signal-red);
      }
      .gate-fail .gate-pill   { color: var(--signal-red); }
      .gate-fail .gate-value  { color: var(--signal-red); }
      .gate-na {
        border-color: var(--magi-text-dim);
        opacity: 0.7;
      }
      .gate-na .gate-pill   { color: var(--magi-text-dim); }
      .gate-na .gate-value  { color: var(--magi-text-dim); }
      /* W-series wake triggers (Fix 4, 2026-06-11): cyan = "this trigger
         can wake the council off-schedule". T-series chips stay on the
         green(fired)/dim(quiet) palette — they are context-only detectors
         and never wake MAGI. */
      .gate-wake {
        border-color: var(--magi-cyan);
      }
      .gate-wake .gate-id    { color: var(--magi-cyan); }
      .gate-wake .gate-pill  { color: var(--magi-cyan); }
      .gate-wake .gate-value { color: var(--magi-cyan); }
      .gate-wake .gate-label { color: var(--magi-cyan-fill); }
      .gate-wake.gate-quiet  { opacity: 0.75; }

      /* MAGI hero block: triangle of 3 agents around central MAGI core,
         plus a "CODE / STATUS" side panel. Matches the iconic NGE MAGI
         agent-arrangement screens (ref2 + ref3 in design refs). */
      .magi-hero {
        display: grid;
        grid-template-columns: 2fr 1fr;
        gap: 16px;
        margin-bottom: 20px;
      }
      @media (max-width: 900px) {
        .magi-hero { grid-template-columns: 1fr; }
      }
      .magi-triangle {
        display: grid;
        grid-template-columns: 1fr 1fr 1fr;
        grid-template-rows: auto auto;
        grid-template-areas:
          ".       balthasar .        "
          "casper  core      melchior ";
        gap: 14px;
        padding: 24px;
        background: #050505;
        border: 1px solid var(--magi-orange);
        min-height: 220px;
      }
      .magi-agent {
        padding: 12px 14px;
        border: 2px solid var(--magi-orange);
        background: var(--magi-orange);
        color: #000;
        font-family: "Helvetica Neue", "Helvetica", "Arial", sans-serif;
        text-align: center;
        text-transform: uppercase;
        transition: background 0.3s, border-color 0.3s, opacity 0.3s;
      }
      /* Conviction-based intensity. Higher conviction = brighter + solid;
         lower conviction = dimmer + dashed border. Mirrors the show's
         "high-confidence pillars glow, low-confidence ones look muted". */
      .magi-agent.conv-high {
        background: var(--magi-orange-bright);
        border-color: var(--magi-orange-bright);
        /* Layered glow: tight inner halo + wider outer bloom for that
           NGE "this pillar is engaged" look. */
        box-shadow:
          0 0 16px rgba(255, 153, 51, 0.85),
          0 0 36px rgba(255, 153, 51, 0.55),
          0 0 64px rgba(255, 153, 51, 0.25);
      }
      .magi-agent.conv-med {
        background: var(--magi-orange);
        border-color: var(--magi-orange);
      }
      .magi-agent.conv-low {
        background: var(--magi-text-dim);
        border-color: var(--magi-text-dim);
        border-style: dashed;
        opacity: 0.7;
      }
      .magi-agent .agent-name {
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 3px;
        margin-bottom: 8px;
      }
      .magi-agent .agent-position {
        font-size: 18px;
        font-family: "VT323", "Courier New", monospace;
        letter-spacing: 1px;
        font-weight: 400;
        margin-bottom: 4px;
      }
      .magi-agent .agent-conviction {
        font-size: 11px;
        letter-spacing: 1px;
        opacity: 0.7;
        font-weight: 700;
      }
      .agent-melchior { grid-area: melchior; }
      .agent-casper   { grid-area: casper; }
      .agent-balthasar{ grid-area: balthasar; }
      .magi-core {
        grid-area: core;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 16px;
        border: 2px solid var(--magi-cyan);
        background: #000;
        color: var(--magi-cyan);
        text-align: center;
      }
      .magi-core .core-label {
        font-family: "Michroma", sans-serif;
        font-size: 18px;
        letter-spacing: 6px;
        margin-bottom: 8px;
      }
      .magi-core .core-cycle {
        font-family: "VT323", "Courier New", monospace;
        font-size: 16px;
        letter-spacing: 1px;
      }
      .magi-core .core-age {
        font-size: 10px;
        letter-spacing: 1px;
        color: var(--magi-text-dim);
        margin-top: 6px;
        text-transform: uppercase;
      }
      .magi-codebox {
        background: #050505;
        border: 1px solid var(--magi-orange);
        display: flex;
        flex-direction: column;
      }
      .codebox-header {
        background: var(--magi-orange);
        color: #000;
        font-weight: 700;
        letter-spacing: 3px;
        padding: 6px 12px;
        font-size: 12px;
        text-transform: uppercase;
        font-family: "Helvetica Neue", "Helvetica", "Arial", sans-serif;
      }
      .codebox-body {
        padding: 12px 14px;
        flex: 1;
        font-size: 11px;
        color: var(--magi-text);
        font-family: "Helvetica Neue", "Helvetica", "Arial", sans-serif;
      }
      .codebox-body .row {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 8px;
        letter-spacing: 1px;
        text-transform: uppercase;
      }
      .codebox-body .row .label { color: var(--magi-text-dim); }
      .codebox-body .row .value {
        font-family: "VT323", "Courier New", monospace;
        font-size: 16px;
        color: var(--magi-orange-bright);
        letter-spacing: 1px;
      }
    </style>
</head>
<body>
    <a href="/logout" style="position:fixed; top:10px; right:14px; z-index:1000;
       background:#ff333322; color:#ff6666; border:1px solid #ff3333; padding:6px 14px;
       font-family:monospace; font-size:0.8em; text-decoration:none; border-radius:4px;">⎋ Logout</a>
    <h1>⬡ MAGI — XRP Grid Bot</h1>
    <div class="header-row" style="color: var(--magi-text-dim); font-size:0.8em; margin-bottom:20px; letter-spacing: 1px;">
        {{ now }} &nbsp;|&nbsp; Auto-refresh 30s &nbsp;|&nbsp;
        <span class="{{ 'status-ok' if scheduler_alive else 'status-err' }}">
            {{ '● SCHEDULER RUNNING' if scheduler_alive else '● SCHEDULER DOWN' }}
        </span>
        &nbsp;|&nbsp;
        <span class="{{ 'status-err' if kill_switch else 'status-ok' }}">
            {{ '⚠ KILL SWITCH ACTIVE' if kill_switch else '● GUARDRAILS OK' if guardrails_ok else '⚠ GUARDRAIL FAILURE' }}
        </span>
        {% if latest_debate_age_label %}
        &nbsp;|&nbsp; last MAGI:
        <span style="color:{{ latest_debate_age_color }};">{{ latest_debate_age_label }}</span>
        {% endif %}
        {% if next_magi %}
        &nbsp;|&nbsp; next:
        <span style="color:{{ '#88cc88' if next_magi.countdown_min < 60 else '#cccc88' }};">{{ next_magi.label }}</span>
        {% endif %}
        {% if llm_calls and llm_calls.cycles is not none %}
        &nbsp;|&nbsp; 24h:
        <span style="color:#88aaff;" title="seat calls counted from Langfuse named spans; cycles include manual/scratch runs">
            {%- if llm_calls.calls is not none %}{{ llm_calls.calls }} LLM calls · {% endif %}{{ llm_calls.cycles }} cycles</span>
        {% endif %}
    </div>

    {% if not guardrails_ok %}
    <div style="background:#ff000022; border:1px solid #ff4444; padding:12px; border-radius:4px; margin-bottom:20px; color:#ff4444;">
        <strong>GUARDRAIL FAILURES:</strong>
        {% for f in guardrail_failures %}<div style="margin-top:4px;">• {{ f }}</div>{% endfor %}
    </div>
    {% endif %}

    <div class="agent-health-panel">
        <div class="agent-health-title">⬢ AGENT HEALTH — last 3 R0 / agent</div>
        <div class="agent-health-chips">
            {% for agent in ['casper', 'melchior', 'balthasar'] %}
            {% set h = agent_health.get(agent) or {'status': 'green', 'degraded_count': 0, 'total': 0, 'model': ''} %}
            <div class="agent-chip health-{{ h.status }}">
                <div class="chip-head">
                    <span class="chip-dot"></span>
                    <span class="chip-name">{{ agent|upper }}</span>
                    <span class="chip-status">{{ h.status|upper }}</span>
                </div>
                <div class="chip-degraded">{{ h.degraded_count }}/{{ h.total if h.total > 0 else 3 }} degraded</div>
                <div class="chip-model">{{ h.model or '—' }}</div>
            </div>
            {% endfor %}
            {% set gm = gate_monitor or {'state': 'unknown'} %}
            {% set gm_state = gm.get('state') or 'unknown' %}
            {% set gm_color_map = {'connected':'green','degraded':'yellow','reconnecting':'yellow','starting':'yellow','disconnected':'red','unknown':'red'} %}
            <div class="agent-chip health-{{ gm_color_map.get(gm_state, 'red') }}">
                <div class="chip-head">
                    <span class="chip-dot"></span>
                    <span class="chip-name">GATE MON</span>
                    <span class="chip-status">{{ gm_state|upper }}</span>
                </div>
                <div class="chip-degraded">
                    {%- if gm.get('last_heartbeat_age_sec') is not none -%}
                        last msg {{ '%.1f' | format(gm.get('last_heartbeat_age_sec')) }}s ago
                    {%- else -%}
                        no traffic yet
                    {%- endif -%}
                </div>
                <div class="chip-model">reconnects/1h: {{ gm.get('reconnect_count_1h') or 0 }}</div>
            </div>
        </div>
    </div>

    {% if open_alerts %}
    {% set alert_critical = open_alerts | selectattr('severity', 'equalto', 'critical') | list %}
    <div style="background:{{ '#ff000022' if alert_critical else '#ffaa0022' }};
                border:1px solid {{ '#ff4444' if alert_critical else '#ffaa00' }};
                padding:12px; border-radius:4px; margin-bottom:20px;
                color:{{ '#ff4444' if alert_critical else '#ffaa00' }};">
        <strong>ALERTS ({{ open_alerts|length }} open):</strong>
        {% for a in open_alerts %}
        <div style="margin-top:6px; font-family:'Courier New',monospace; font-size:12px;">
            <span style="font-weight:bold;">[{{ a.severity|upper }}]</span>
            {{ a.timestamp | et }} —
            <span style="color:#ffcc66;">{{ a.category }}</span>
            {% if a.agent_id %}<span style="color:#66ccff;">({{ a.agent_id }})</span>{% endif %}
            {% if a.provider_name %}
              <span style="color:#cccccc;">[{{ a.provider_name }}/{{ a.provider_category or '?' }}]</span>
            {% endif %}
            — {{ a.message }}
            {% if a.step_id %}<span style="color:#666; font-size:10px;"> step={{ a.step_id[:18] }}…</span>{% endif %}
            {% if magi_token %}
            <a href="#" onclick="resolveAlert({{ a.id }}); return false;"
               style="color:#888; margin-left:8px; text-decoration:underline;">[resolve]</a>
            {% endif %}
        </div>
        {% endfor %}
    </div>
    <script>
      async function resolveAlert(id) {
        const r = await fetch('/api/resolve_alert?id=' + id + '&token={{ magi_token }}',
                              {method: 'POST'});
        if (r.ok) location.reload();
      }
    </script>
    {% endif %}

    {% if latest_debate %}
    <div class="magi-hero">
      <div class="magi-triangle">
        {% set m_conv = latest_debate.melchior_r0_conviction or 0 %}
        {% set m_cls = 'conv-high' if m_conv >= 0.75 else ('conv-med' if m_conv >= 0.5 else 'conv-low') %}
        <div class="magi-agent agent-melchior {{ m_cls }}">
          <div class="agent-name">MELCHIOR</div>
          <div class="agent-position">{{ latest_debate.melchior_r0_position or '—' }}</div>
          <div class="agent-conviction">conv {{ '%.2f'|format(m_conv) }}</div>
        </div>
        <div class="magi-core">
          <div class="core-label">MAGI</div>
          <div class="core-cycle">{{ latest_debate.cycle_id[-10:] if latest_debate.cycle_id else '—' }}</div>
          {% if latest_debate_age_label %}
          <div class="core-age">{{ latest_debate_age_label }}</div>
          {% endif %}
          {% if latest_is_blind_review and council_decision %}
          <div class="core-decision" style="margin-top:6px; font-size:10px; line-height:1.5;">
            <div style="color:#88ccff; letter-spacing:1px;">{{ council_decision.decision or '—' }}</div>
            {% if council_decision.vote_multiset %}<div style="color:#999;">votes [{{ council_decision.vote_multiset }}]</div>{% endif %}
            {% if council_decision.consensus %}<div style="color:#777; text-transform:uppercase; letter-spacing:1px;">{{ council_decision.consensus }}</div>{% endif %}
          </div>
          {% endif %}
        </div>
        {% set c_conv = latest_debate.casper_r0_conviction or 0 %}
        {% set c_cls = 'conv-high' if c_conv >= 0.75 else ('conv-med' if c_conv >= 0.5 else 'conv-low') %}
        <div class="magi-agent agent-casper {{ c_cls }}">
          <div class="agent-name">CASPER</div>
          <div class="agent-position">{{ latest_debate.casper_r0_position or '—' }}</div>
          <div class="agent-conviction">conv {{ '%.2f'|format(c_conv) }}</div>
        </div>
        {% set b_conv = latest_debate.balthasar_r0_conviction or 0 %}
        {% set b_cls = 'conv-high' if b_conv >= 0.75 else ('conv-med' if b_conv >= 0.5 else 'conv-low') %}
        <div class="magi-agent agent-balthasar {{ b_cls }}">
          <div class="agent-name">BALTHASAR</div>
          <div class="agent-position">{{ latest_debate.balthasar_r0_position or '—' }}</div>
          <div class="agent-conviction">conv {{ '%.2f'|format(b_conv) }}</div>
        </div>
      </div>
      <div class="magi-codebox">
        <div class="codebox-header">GRID STATUS</div>
        <div class="codebox-body">
          <div class="row"><span class="label">Grid Active?</span><span class="value" style="color:{{ grid_status.color }}; font-weight:bold;">{{ 'YES' if grid_status.active else 'NO' }} · {{ grid_status.label }}</span></div>
          <div class="row" style="margin-top:-4px;"><span class="label" style="font-size:9px;">&nbsp;</span><span class="value" style="font-family:inherit; font-size:10px; color:#999; text-transform:none;">{{ grid_status.detail }}</span></div>
          <div class="row"><span class="label">Mode</span><span class="value" style="color:{{ grid_status.mode_color }};">{{ grid_status.mode }}{{ ' (real money)' if grid_status.mode == 'LIVE' else '' }}</span></div>
          <div class="row"><span class="label">Resting</span><span class="value" style="color:{{ '#00ff88' if grid_status.open_total else '#ff4444' }};">{{ grid_status.open_buys }} buy / {{ grid_status.open_sells }} sell</span></div>
          <div class="row"><span class="label">Last fill</span><span class="value" style="color:{{ fill_age_color }};">{{ fill_age_label }}</span></div>
          <div class="row"><span class="label">Price</span><span class="value">${{ price }}</span></div>
          <div class="row"><span class="label">Vol regime</span><span class="value">{{ vol_regime }} · ATR {{ atr_pct }}</span></div>
          <div class="row"><span class="label">VWAP dev</span><span class="value">{{ vwap_dev }}%</span></div>
          <div class="row"><span class="label">Centre</span><span class="value">${{ grid_centre }}</span></div>
          <div class="row"><span class="label">Spacing</span><span class="value">{{ grid_spacing }}%</span></div>
          <div class="row"><span class="label">Levels</span><span class="value">{{ grid_levels }}</span></div>
          <div class="row"><span class="label">Action</span><span class="value">{{ latest_debate.final_grid_action or '—' }}</span></div>
          <div class="row"><span class="label">Risk</span><span class="value">{{ latest_debate.final_risk_action or '—' }}</span></div>
          {% if next_magi %}
          <div class="row"><span class="label">Next cycle</span><span class="value">{{ next_magi.label }}</span></div>
          {% endif %}
        </div>
      </div>
    </div>
    {% endif %}

    <h2>LIVE CHART</h2>
    <iframe src="/chart"
            style="width:100%; height:480px; border:1px solid #00ff8844;
                   border-radius:4px; background:#0a0a0a;"
            scrolling="no"></iframe>

    {% if latest_debate and latest_debate.deadlock %}
    {% if latest_is_blind_review %}
    <div class="deadlock-banner">NO CONSENSUS ON LAST CYCLE — grid defaulted to its safe stance (a valid council outcome, not an error)</div>
    {% else %}
    <div class="deadlock-banner">⚠ DEADLOCK ON LAST CYCLE — HUMAN REVIEW REQUESTED</div>
    {% endif %}
    {% endif %}
    {% if council_override_tags %}
    <div class="override-line">Hard rule overrides applied: {{ council_override_tags|join(', ') }}</div>
    {% endif %}

    <div class="dash-row inv-pnl-row">
        <div class="dash-cell">
    <h2>Inventory</h2>
    <div class="grid">
        <div class="card">
            <div class="label">XRP Held</div>
            <div class="value">{{ xrp_held }}</div>
        </div>
        <div class="card">
            <div class="label">USD Held</div>
            <div class="value">${{ usd_held }}</div>
        </div>
        <div class="card">
            <div class="label">Net Position</div>
            <div class="value">${{ net_position }}</div>
            <div class="sub">Skew: {{ inventory_skew }}</div>
        </div>
    </div>
        </div>
        <div class="dash-cell">
    <h2>{{ 'Paper' if paper_mode else 'Live' }} P&amp;L</h2>
    <div style="font-size:0.85em; color:#666; margin-bottom:8px;">
        Last fill: <span style="color:{{ fill_age_color }};">{{ fill_age_label }}</span>
        &nbsp;|&nbsp; {{ 'paper fills since the 2026-06-09 reset (live history excluded)' if paper_mode else 'live fills only (paper history excluded)' }}
        {% if fill_stale %}
        <span style="color:#ff4444; margin-left:12px;">
            ⚠ No fills in 24h+ — metrics below describe historical activity, not current operation.
        </span>
        {% endif %}
    </div>
    <div class="grid">
        <div class="card">
            <div class="label">Grid Harvest</div>
            <div class="value {{ 'pnl-pos' if pnl_realized >= 0 else 'pnl-neg' }}">${{ pnl_realized_fmt }}</div>
            <div class="sub">fee-adjusted round trips &nbsp;|&nbsp; {{ pnl_fill_count }} fills, {{ pnl_matched_trips }} trips</div>
        </div>
        <div class="card">
            <div class="label">Alpha vs Hold</div>
            {% if pnl_alpha is not none %}
            <div class="value {{ 'pnl-pos' if pnl_alpha >= 0 else 'pnl-neg' }}">${{ pnl_alpha_fmt }}</div>
            <div class="sub">bot's contribution vs holding the run-start book</div>
            {% else %}
            <div class="value">—</div>
            <div class="sub">no inventory baseline</div>
            {% endif %}
        </div>
        <div class="card">
            <div class="label">Total Equity &Delta;</div>
            <div class="value {{ 'pnl-pos' if pnl_total >= 0 else 'pnl-neg' }}">${{ pnl_total_fmt }}</div>
            <div class="sub">equity ${{ pnl_baseline_equity_fmt }} &rarr; ${{ pnl_current_equity_fmt }} &nbsp;|&nbsp; incl. inventory beta ${{ pnl_beta_fmt }} &nbsp;|&nbsp; fees ${{ pnl_fees_fmt }}</div>
        </div>
        <div class="card">
            <div class="label">Win Rate</div>
            <div class="value">{{ pnl_win_rate }}%</div>
            <div class="sub">
                {% if pnl_avg_per_trip is not none %}
                avg ${{ pnl_avg_per_trip }} / trip
                {% else %}
                no round trips yet
                {% endif %}
            </div>
        </div>
        <div class="card">
            <div class="label">Activity</div>
            <div class="value">{{ pnl_fills_today }}</div>
            <div class="sub">
                fills today
                {% if pnl_mins_since is not none %}
                &nbsp;|&nbsp; last {{ pnl_mins_since }}m ago
                {% endif %}
            </div>
        </div>
    </div>
        </div>
    </div>

    {% if readiness %}
    <div class="readiness-panel">
      <div class="readiness-section">
        <div class="readiness-header">
          <span class="readiness-title">⬢ Live Readiness</span>
          <span class="readiness-meta">no deadline · entire trading history</span>
          <span class="readiness-verdict verdict-{{ readiness.live.verdict|lower }}">
            {% if readiness.live.verdict == 'GREEN' %}GREEN — ready for live capital evaluation
            {% elif readiness.live.verdict == 'YELLOW' %}YELLOW — most gates pass, review failures
            {% else %}RED — not ready{% endif %}
          </span>
        </div>
        <div class="gate-grid">
          {% for gid in ['L1','L2','L3','L4','L5','L6','L7','L8','L9'] %}
          {% set g = readiness.live.gates[gid] %}
          <div class="gate-chip gate-{{ g.status|lower }}"
               onclick='console.log({{ {"gate": gid, "data": g}|tojson }})'
               title="{{ g.label }} — {{ g.detail }}">
            <div class="gate-head">
              <span class="gate-id">{{ gid }}</span>
              <span class="gate-pill">{{ g.status }}</span>
            </div>
            <div class="gate-value">{{ g.value }}</div>
            <div class="gate-label">{{ g.label.split(' ', 1)[1] if ' ' in g.label else g.label }} · thr {{ g.threshold }}</div>
          </div>
          {% endfor %}
        </div>
      </div>
    </div>
    {% endif %}

    {% if gate_activity %}
    <div class="readiness-panel">
      <div class="readiness-section">
        <div class="readiness-header">
          <span class="readiness-title">⬢ Gate Activity</span>
          <span class="readiness-meta">trailing {{ (gate_activity.window_hours // 24) }}d · fires shown 24h/window · cyan ★ W = wakes MAGI off-schedule · T = context-only (shown to council, never wakes it)</span>
          <span class="readiness-meta">off-schedule wakes: {{ gate_activity.wakes.last_24h }} (24h) · {{ gate_activity.wakes.window }} (window)</span>
        </div>
        {% if gate_activity.triggers %}
        <div class="gate-grid">
          {% for t in gate_activity.triggers %}
          {% set is_wake = t.trigger_id in ['W1','W2'] %}
          <div class="gate-chip {{ ('gate-wake' + ('' if t.fires_window else ' gate-quiet')) if is_wake else ('gate-pass' if t.fires_window else 'gate-na') }}"
               onclick='console.log({{ t|tojson }})'
               title="{{ t.trigger_id }} · {{ t.evals }} evals in window · last fired details → console">
            <div class="gate-head">
              <span class="gate-id">{{ t.trigger_id }}{% if is_wake %} ★{% endif %}</span>
              <span class="gate-pill">{{ t.fires_24h }}/{{ t.fires_window }}</span>
            </div>
            <div class="gate-value">{{ t.fires_window }} fires</div>
            <div class="gate-label">{{ 'WAKES COUNCIL' if is_wake else 'context-only' }}</div>
          </div>
          {% endfor %}
        </div>
        {% else %}
        <div class="readiness-meta">no gate evaluations recorded in window</div>
        {% endif %}
      </div>
    </div>
    {% endif %}

    {% if exposure_cap %}
    <div class="readiness-panel">
      <div class="readiness-section">
        <div class="readiness-header">
          <span class="readiness-title">⬢ Exposure Cap</span>
          <span class="readiness-meta">{{ exposure_cap.threshold }} linked down-rebuilds (≤{{ exposure_cap.link_hours }}h apart) → sells-only · releases on first higher rebuild</span>
          <span class="readiness-verdict verdict-{{ exposure_cap.colour }}">
            {% if exposure_cap.engaged %}ENGAGED — buys frozen, sells-only rebuilds
            {% elif exposure_cap.streak > 0 %}ARMING — streak {{ exposure_cap.streak }}/{{ exposure_cap.threshold }}
            {% else %}CLEAR — streak 0{% endif %}
          </span>
        </div>
        <div class="gate-grid">
          <div class="gate-chip {{ 'gate-fail' if exposure_cap.engaged else ('gate-pass' if exposure_cap.streak == 0 else 'gate-na') }}"
               onclick='console.log({{ exposure_cap|tojson }})'
               title="down-walk streak · full state → console">
            <div class="gate-head">
              <span class="gate-id">DOWN-WALK</span>
              <span class="gate-pill">{{ exposure_cap.streak }}/{{ exposure_cap.threshold }}</span>
            </div>
            <div class="gate-value">{{ 'SELLS-ONLY' if exposure_cap.engaged else 'streak ' ~ exposure_cap.streak }}</div>
            <div class="gate-label">last rebuild {{ exposure_cap.last_centre or '—' }}{% if exposure_cap.last_age_h is not none %} · {{ exposure_cap.last_age_h }}h ago{% endif %}</div>
          </div>
        </div>
      </div>
    </div>
    {% endif %}

    <h2>Manual Actions</h2>
    <div style="margin-top:10px; display:flex; flex-wrap:wrap; gap:10px; align-items:center;">
        <button onclick="triggerMagi(this)" style="background:#00ff8822; color:#00ffcc; border:2px solid #00ff88; padding:10px 24px; font-family:monospace; font-size:1em; font-weight:bold; cursor:pointer; border-radius:4px;">
            Trigger MAGI Cycle
        </button>
        <button onclick="toggleKill()" id="kill-btn" style="background:{{ '#ff000033' if kill_switch else '#33000022' }}; color:{{ '#ff4444' if kill_switch else '#aa3333' }}; border:1px solid {{ '#ff4444' if kill_switch else '#550000' }}; padding:8px 14px; font-family:monospace; font-size:0.8em; cursor:pointer; border-radius:4px;">
            {{ '⬛ DEACTIVATE KILL SWITCH' if kill_switch else '⬛ ACTIVATE KILL SWITCH' }}
        </button>
        <span id="magi-status" style="color:#888; font-size:0.85em;"></span>
        <span id="kill-status" style="color:#888; font-size:0.85em;"></span>
    </div>
    <script>
    async function triggerMagi(btn) {
        btn.disabled = true;
        btn.textContent = 'Running MAGI cycle...';
        try {
            const r = await fetch('/api/trigger_magi', {
                method: 'POST',
                headers: {'X-Magi-Token': '{{ magi_token }}'}
            });
            const data = await r.json();
            if (data.ok) {
                btn.textContent = 'Cycle complete — refreshing...';
                setTimeout(() => location.reload(), 1500);
            } else {
                btn.textContent = 'Failed: ' + (data.error || 'unknown');
                btn.disabled = false;
            }
        } catch (e) {
            btn.textContent = 'Error: ' + e.message;
            btn.disabled = false;
        }
    }
    function toggleKill() {
        const status = document.getElementById('kill-status');
        status.textContent = 'Toggling...';
        status.style.color = '#ffaa00';
        fetch('/api/toggle_kill', {method: 'POST'})
            .then(r => r.json())
            .then(data => {
                status.textContent = data.kill_switch ? 'KILL SWITCH ON' : 'Kill switch off';
                status.style.color = data.kill_switch ? '#ff4444' : '#00ff88';
                setTimeout(() => location.reload(), 1000);
            })
            .catch(e => {
                status.textContent = 'Error: ' + e;
                status.style.color = '#ff4444';
            });
    }
    </script>

    <details>
        <summary style="cursor:pointer; color:#88aaff;">
            {% if recent_orders %}Recent Orders ({{ recent_orders|length }} rows){% else %}Recent Orders (none yet){% endif %}
        </summary>
        {% if recent_orders %}
        <table>
            <tr>
                <th>Time</th>
                <th>Side</th>
                <th>Price</th>
                <th>Size</th>
                <th>Status</th>
                <th>Fill Price</th>
                <th>Fee</th>
                <th>P&amp;L</th>
            </tr>
            {% for o in recent_orders %}
            {% set order_pnl = order_pnl_map.get(o.order_id) %}
            <tr>
                <td style="color:#666;">{{ (o.filled_at or o.timestamp) | et }}</td>
                <td class="side-{{ o.side }}">{{ o.side }}</td>
                <td>${{ '%.4f'|format(o.price or 0) }}</td>
                <td>{{ '%.2f'|format(o.size or 0) }}</td>
                <td class="status-{{ o.status }}">{{ o.status }}</td>
                <td>{{ '$%.4f'|format(o.fill_price) if o.fill_price else '—' }}</td>
                <td>{{ '$%.5f'|format(o.fee) if o.fee else '—' }}</td>
                <td class="{{ 'pnl-pos' if order_pnl and order_pnl > 0 else ('pnl-neg' if order_pnl and order_pnl < 0 else 'pnl-zero') }}">
                    {{ '$%.4f'|format(order_pnl) if order_pnl is not none else '—' }}
                </td>
            </tr>
            {% endfor %}
        </table>
        {% else %}
        <div style="color:#666; font-size:0.8em; margin-top:8px;">No orders recorded yet — starts after first MAGI cycle.</div>
        {% endif %}
    </details>

    <details style="margin-bottom:10px;" id="d-council-log" open>
    <summary style="cursor:pointer; color:#88aaff; font-size:0.9em;">▸ Council Log — recent cycles (click to collapse)</summary>
    <!-- ── Council Log: every recent cycle, deep-linked to its Langfuse
         trace (full prompts/responses live there, not here) ────────── -->
    <h2>Council Log</h2>
    {% if council_log_rows %}
    <table style="margin-top:10px;">
        <tr>
            <th>Time</th>
            <th>Trigger</th>
            <th>Casper</th>
            <th>Melchior</th>
            <th>Balthasar</th>
            <th>Consensus</th>
            <th>Grid</th>
            <th>Risk</th>
            <th>Hard rules</th>
            <th>Fills 6h</th>
            <th>Trace</th>
        </tr>
        {% for d in council_log_rows %}
        <tr>
            <td style="color:#888;">{{ d.timestamp | et }}</td>
            <td style="color:#ffaa00; font-size:0.8em;">{{ d.trigger or '—' }}</td>
            <td style="font-size:0.8em;">{{ d.casper_r0_position or '—' }}</td>
            <td style="font-size:0.8em;">{{ d.melchior_r0_position or '—' }}</td>
            <td style="font-size:0.8em;">{{ d.balthasar_r0_position or '—' }}</td>
            {% if d.is_blind_review %}
            <td style="font-size:0.78em;">
              {% if d.consensus_class == 'no_consensus' %}<span style="color:#ffaa44;">no-consensus</span>
              {% elif d.consensus_class == 'reconciled' %}<span style="color:#88ccff;">reconciled</span>
              {% elif d.consensus_class == 'clear' %}<span style="color:#66cc88;">clear</span>
              {% else %}<span style="color:#666;">—</span>{% endif %}
            </td>
            {% else %}
            <td>{% if d.debate_triggered %}<span class="debate-flag-yes">YES</span>{% else %}<span class="debate-flag-no">no</span>{% endif %}</td>
            {% endif %}
            <td class="{{ d.final_grid_action }}">{{ d.final_grid_action or '—' }}</td>
            <td>{{ d.final_risk_action or '—' }}</td>
            <td style="color:#ff8866; font-size:0.78em;">{{ d.override_tags|join(', ') if d.override_tags else '—' }}</td>
            <td>{{ d.fills_6h if d.fills_6h is not none else '—' }}</td>
            <td>
                {% if d.trace_url %}
                <a href="{{ d.trace_url }}" target="_blank" rel="noopener"
                   style="color:#66ccff; text-decoration:underline;">trace →</a>
                {% else %}<span style="color:#444;">—</span>{% endif %}
            </td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <div style="color:#666; margin-top:10px;">No council cycles recorded yet.</div>
    {% endif %}
    </details>

    <details style="margin-bottom:10px;" id="d-paper-analytics">
    <summary style="cursor:pointer; color:#88aaff; font-size:0.9em;">▸ Paper-run analytics — council accuracy + outcome attribution (click to expand)</summary>
    <div style="color:#cc8855; font-size:0.78em; margin:8px 0;">
        ⚠ cycles before the 2026-06-12 five-fix rebuild graded the old 0.75% config
        (and span a stopped-engine window) — read aggregates accordingly until
        post-rebuild cycles mature
    </div>
    <!-- ── Phase 5 PANEL 2: Accuracy Tracker — scoped to the paper run ── -->
    <h2>Council Accuracy (paper run)</h2>
    <div style="color:#666; font-size:0.78em; margin-bottom:8px;">
        scored on cycles since the {{ paper_scope_start[:10] }} paper reset only — earlier eras (Letta, pre-rebuild) excluded
    </div>
    <div class="accuracy-grid">
        {% for agent in ['casper', 'melchior', 'balthasar'] %}
        {% set a = council_accuracy[agent] %}
        <div class="accuracy-card">
            <div style="color:#88cc88; font-size:0.78em; letter-spacing:2px;
                        text-transform:uppercase; margin-bottom:6px;">
                {{ agent }}
            </div>
            <div class="accuracy-line">
                accuracy: <span class="num">{{ a.acc.accuracy_pct if a.acc.accuracy_pct is not none else '—' }}{{ '%' if a.acc.accuracy_pct is not none else '' }}</span>
                <span style="color:#666;">({{ a.acc.positive_outcomes }}/{{ a.acc.scored }} scored)</span>
            </div>
            <div style="color:#666; font-size:0.68em; margin-top:4px;">
                {{ a.acc.total_calls }} eligible · {{ a.acc.excluded }} excluded{% if a.acc.excluded %} ({% for k, v in a.acc.excluded_reasons.items() if v %}{{ k }}: {{ v }}{{ ', ' if not loop.last else '' }}{% endfor %}){% endif %}
            </div>
            <div style="margin-top:10px;">
                <div style="color:#666; font-size:0.7em; margin-bottom:4px;">conviction (last 30 cycles)</div>
                {{ conviction_sparklines_svg[agent]|safe }}
            </div>
        </div>
        {% endfor %}
    </div>

    <!-- ── Phase 5 PANEL 5: Outcome Attribution — scoped to the paper run ── -->
    <h2>Outcome Attribution (paper run)</h2>
    <div style="color:#666; font-size:0.78em; margin-bottom:8px;">
        24h-matured cycles since the {{ paper_scope_start[:10] }} paper reset — first rows mature ~24h after each cycle
    </div>
    <div class="attribution-grid">
        <div class="attribution-card">
            <div class="evo-title" style="color:#00ff88;">Best 5 by 24h P&amp;L</div>
            {% if attribution_best %}
            <table>
                <tr><th>Time</th><th>Grid</th><th>C/M/B r0</th><th>Fills 24h</th><th>P&amp;L 24h</th></tr>
                {% for r in attribution_best %}
                <tr>
                    <td style="color:#888;">{{ r.timestamp | et }}</td>
                    <td class="{{ r.final_grid_action }}">{{ r.final_grid_action }}</td>
                    <td style="font-size:0.78em;">{{ r.casper_r0_position }} / {{ r.melchior_r0_position }} / {{ r.balthasar_r0_position }}</td>
                    <td>{{ r.fills_24h }}</td>
                    <td class="pnl-pos">${{ '%.4f'|format(r.pnl_24h or 0) }}</td>
                </tr>
                {% endfor %}
            </table>
            {% else %}
            <div style="color:#666;">No 24h-backfilled cycles yet.</div>
            {% endif %}
        </div>
        <div class="attribution-card">
            <div class="evo-title" style="color:#ff8866;">Worst 5 by 24h P&amp;L</div>
            {% if attribution_worst %}
            <table>
                <tr><th>Time</th><th>Grid</th><th>C/M/B r0</th><th>Fills 24h</th><th>P&amp;L 24h</th></tr>
                {% for r in attribution_worst %}
                <tr>
                    <td style="color:#888;">{{ r.timestamp | et }}</td>
                    <td class="{{ r.final_grid_action }}">{{ r.final_grid_action }}</td>
                    <td style="font-size:0.78em;">{{ r.casper_r0_position }} / {{ r.melchior_r0_position }} / {{ r.balthasar_r0_position }}</td>
                    <td>{{ r.fills_24h }}</td>
                    <td class="{{ 'pnl-neg' if (r.pnl_24h or 0) < 0 else 'pnl-pos' }}">${{ '%.4f'|format(r.pnl_24h or 0) }}</td>
                </tr>
                {% endfor %}
            </table>
            {% else %}
            <div style="color:#666;">No 24h-backfilled cycles yet.</div>
            {% endif %}
        </div>
    </div>
    <div class="evo-card" style="margin-top:14px;">
        <div class="evo-title">Fill rate &amp; P&amp;L by grid_action (paper run)</div>
        {% if action_summary %}
        <table>
            <tr><th>Action</th><th>Cycles</th><th>Avg fills 24h</th><th>Avg P&amp;L 24h</th></tr>
            {% for r in action_summary %}
            <tr>
                <td class="{{ r.action }}">{{ r.action }}</td>
                <td>{{ r.count }}</td>
                <td>{{ '%.2f'|format(r.avg_fills or 0) }}</td>
                <td class="{{ 'pnl-neg' if (r.avg_pnl or 0) < 0 else 'pnl-pos' }}">${{ '%.4f'|format(r.avg_pnl or 0) }}</td>
            </tr>
            {% endfor %}
        </table>
        {% else %}
        <div style="color:#666;">No 24h-backfilled cycles yet.</div>
        {% endif %}
    </div>
    </details>

    <div class="footer">
        MAGI Phase 5 — XRP/USD Spot Grid Bot — {{ 'Paper' if paper_mode else 'Live' }} Mode
    </div>

    <script>
    // Persist <details> open/closed state across refreshes. Only acts
    // on <details> elements with an id; the inner Debate Log per-row
    // <details> don't have ids and are intentionally skipped.
    (function() {
      function k(id) { return 'magi-details-' + id; }
      document.addEventListener('DOMContentLoaded', function() {
        document.querySelectorAll('details[id]').forEach(function(d) {
          var saved = localStorage.getItem(k(d.id));
          if (saved === 'open') d.open = true;
          else if (saved === 'closed') d.open = false;
          d.addEventListener('toggle', function() {
            localStorage.setItem(k(d.id), d.open ? 'open' : 'closed');
          });
        });
      });
    })();
    </script>

    <script>
    // Soft refresh: every 30s, fetch the page in the background and swap
    // in only the data sections that need updating. The chart iframe is
    // never touched, so its WebSocket / data connection stays alive
    // instead of disconnecting on each refresh.
    //
    // <details> state is preserved by the localStorage script above
    // (toggle listeners are re-attached after innerHTML replacement
    // because we re-run the same restore logic on each swap).
    (function() {
      var REFRESH_MS = 30000;
      // Selectors of regions to refresh from the fetched HTML. Chart iframe
      // is excluded by design. Each selector must resolve to a single
      // element on both the live and fetched documents.
      var SELECTORS = [
        '.header-row',          // top status bar (time / scheduler / age / next / 24h calls)
        '.agent-health-panel',  // BYOK contingency Dim 1 — degradation chips + GATE MON
        '.magi-hero',           // hero (agent triangle + CODE box)
        '.inv-pnl-row',         // Inventory + P&L pair
        '.readiness-panel',     // Live readiness gates
        '#d-council-log',       // Council Log (open by default; rows + trace links)
        // Other panels (Recent Orders, collapsed analytics) update
        // infrequently or only on user expand.
        // Add more selectors here if their data lag matters.
      ];
      function k(id) { return 'magi-details-' + id; }
      function rewireDetails() {
        document.querySelectorAll('details[id]').forEach(function(d) {
          if (d.dataset.wired) return;
          d.dataset.wired = '1';
          var saved = localStorage.getItem(k(d.id));
          if (saved === 'open') d.open = true;
          else if (saved === 'closed') d.open = false;
          d.addEventListener('toggle', function() {
            localStorage.setItem(k(d.id), d.open ? 'open' : 'closed');
          });
        });
      }
      async function softRefresh() {
        try {
          var r = await fetch('/', { cache: 'no-store' });
          if (!r.ok) return;
          var html = await r.text();
          var doc = new DOMParser().parseFromString(html, 'text/html');
          SELECTORS.forEach(function(sel) {
            var fresh = doc.querySelector(sel);
            var live = document.querySelector(sel);
            if (fresh && live) live.innerHTML = fresh.innerHTML;
          });
          rewireDetails();
        } catch (e) {
          // Silent failure — the next interval will try again.
        }
      }
      document.addEventListener('DOMContentLoaded', function() {
        setInterval(softRefresh, REFRESH_MS);
      });
    })();
    </script>

</body>
</html>
"""

CHART_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>XRP/USD Live Chart</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0a; color: #00ff88; font-family: monospace; overflow: hidden; }
        #chart-container { width: 100vw; height: 100vh; position: relative; }
        #status {
            position: absolute;
            top: 8px;
            left: 8px;
            z-index: 10;
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.75em;
            background: rgba(0,0,0,0.6);
            padding: 4px 8px;
            border-radius: 3px;
            pointer-events: none;
        }
        #status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #ffaa00;
            display: inline-block;
        }
    </style>
</head>
<body>
    <div id="chart-container">
        <div id="status">
            <span id="status-dot"></span>
            <span id="status-text" style="color:#ffaa00;">CONNECTING</span>
        </div>
    </div>
    <script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>
    <script>
    var container = document.getElementById('chart-container');
    var chart = LightweightCharts.createChart(container, {
        width: window.innerWidth,
        height: window.innerHeight,
        layout: {
            background: { color: '#0a0a0a' },
            textColor: '#00ff88',
        },
        grid: {
            vertLines: { color: '#ffffff11' },
            horzLines: { color: '#ffffff11' },
        },
        timeScale: {
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 5,
            barSpacing: 6,
            fixLeftEdge: false,
            fixRightEdge: false,
        },
        rightPriceScale: {
            autoScale: true,
            scaleMargins: {
                top: 0.2,
                bottom: 0.2,
            },
        },
    });

    var candleSeries = chart.addCandlestickSeries({
        upColor: '#00ff88',
        downColor: '#ff4444',
        borderUpColor: '#00ff88',
        borderDownColor: '#ff4444',
        wickUpColor: '#00ff88',
        wickDownColor: '#ff4444',
    });

    window.addEventListener('resize', function() {
        chart.applyOptions({ width: window.innerWidth, height: window.innerHeight });
    });

    var priceLines = [];
    var ws = null;
    var lastMessageAt = 0;
    var reconnectAttempt = 0;
    var reconnectTimer = null;
    var backoffMs = 2000;
    var backoffStartTime = null;

    function setStatus(state) {
        var dot = document.getElementById('status-dot');
        var txt = document.getElementById('status-text');
        if (state === 'live') {
            dot.style.background = '#00ff88';
            txt.textContent = 'LIVE';
            txt.style.color = '#00ff88';
        } else if (state === 'reconnecting') {
            dot.style.background = '#ffaa00';
            txt.textContent = 'RECONNECTING';
            txt.style.color = '#ffaa00';
        } else {
            dot.style.background = '#ff4444';
            txt.textContent = 'DISCONNECTED';
            txt.style.color = '#ff4444';
        }
    }

    function parseTime(rfc3339) {
        return Math.floor(new Date(rfc3339).getTime() / 1000);
    }

    function drawGridLevels() {
        fetch('/api/active_grid_levels')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                console.log('Grid levels:', data.levels ? data.levels.length : 0,
                            'centre:', data.centre_price);
                priceLines.forEach(function(pl) { candleSeries.removePriceLine(pl); });
                priceLines = [];

                (data.levels || []).forEach(function(level) {
                    var color = level.side === 'buy' ? '#00ff88' : '#ff4444';
                    var title = level.side === 'buy'
                        ? 'B@' + parseFloat(level.price).toFixed(4)
                        : 'S@' + parseFloat(level.price).toFixed(4);
                    var pl = candleSeries.createPriceLine({
                        price: parseFloat(level.price),
                        color: color,
                        lineStyle: level.side === 'buy' ? 1 : 0,
                        lineWidth: 2,
                        axisLabelVisible: true,
                        title: title,
                    });
                    priceLines.push(pl);
                });

                if (data.centre_price != null) {
                    var cpl = candleSeries.createPriceLine({
                        price: parseFloat(data.centre_price),
                        color: '#00ccff',
                        lineStyle: 2,
                        lineWidth: 2,
                        axisLabelVisible: true,
                        title: 'centre',
                    });
                    priceLines.push(cpl);
                }

                if (data.centre_price != null && data.levels && data.levels.length > 0) {
                    var prices = data.levels.map(function(l) {
                        return parseFloat(l.price);
                    });
                    prices.push(parseFloat(data.centre_price));
                    var minPrice = Math.min.apply(null, prices);
                    var maxPrice = Math.max.apply(null, prices);
                    var padding = (maxPrice - minPrice) * 0.5;
                    chart.priceScale('right').applyOptions({
                        autoScale: true,
                    });
                }
            })
            .catch(function(err) { console.error('Grid levels fetch failed:', err); });
    }

    drawGridLevels();
    setInterval(drawGridLevels, 30000);

    function connect() {
        if (ws) {
            ws.onclose = null;
            ws.onerror = null;
            try { ws.close(); } catch(e) {}
            ws = null;
        }

        ws = new WebSocket('wss://ws.kraken.com/v2');

        ws.onopen = function() {
            reconnectAttempt = 0;
            backoffMs = 2000;
            backoffStartTime = null;
            lastMessageAt = Date.now();
            ws.send(JSON.stringify({
                method: 'subscribe',
                params: { channel: 'ohlc', symbol: ['XRP/USD'], interval: 5 }
            }));
        };

        ws.onmessage = function(event) {
            lastMessageAt = Date.now();
            var msg;
            try { msg = JSON.parse(event.data); } catch(e) { return; }

            var channel = msg.channel;
            var type = msg.type;

            if (channel === 'heartbeat') {
                return;
            }

            if (channel === 'ohlc') {
                var data = msg.data || [];
                if (type === 'snapshot') {
                    console.log('Kraken OHLC snapshot: ' + data.length + ' bars received');
                    var bars = data.map(function(c) {
                        return {
                            time: parseTime(c.interval_begin),
                            open: c.open,
                            high: c.high,
                            low: c.low,
                            close: c.close,
                        };
                    });
                    candleSeries.setData(bars);
                    chart.timeScale().fitContent();
                    setStatus('live');
                } else if (type === 'update') {
                    data.forEach(function(c) {
                        candleSeries.update({
                            time: parseTime(c.interval_begin),
                            open: c.open,
                            high: c.high,
                            low: c.low,
                            close: c.close,
                        });
                    });
                    setStatus('live');
                }
            }
        };

        ws.onclose = function() {
            if (backoffStartTime === null) {
                backoffStartTime = Date.now();
            }

            reconnectAttempt++;
            var delay;
            if (reconnectAttempt === 1) {
                delay = 0;
            } else {
                delay = Math.min(backoffMs, 30000);
                backoffMs = Math.min(backoffMs * 2, 30000);
            }

            var elapsed = Date.now() - backoffStartTime;
            if (elapsed > 60000) {
                setStatus('disconnected');
            } else {
                setStatus('reconnecting');
            }

            clearTimeout(reconnectTimer);
            reconnectTimer = setTimeout(connect, delay);
        };

        ws.onerror = function() {
            try { ws.close(); } catch(e) {}
        };
    }

    setInterval(function() {
        if (lastMessageAt === 0) return;
        var silent = Date.now() - lastMessageAt;
        if (silent > 5000 && ws && ws.readyState === WebSocket.OPEN) {
            ws.close();
        } else if (silent <= 5000 && ws && ws.readyState === WebSocket.OPEN) {
            setStatus('live');
        }
    }, 2000);

    connect();
    </script>
</body>
</html>
"""


# ── Phase 5: council data fetch + SVG sparkline helpers ──────────────

def _svg_sparkline(values, w=80, h=24, color='#66ccff'):
    """One-line polyline sparkline. Returns SVG markup as a string."""
    if not values:
        return '<svg width="{}" height="{}"></svg>'.format(w, h)
    vmin = min(values)
    vmax = max(values)
    span = (vmax - vmin) or 1
    n = len(values)
    if n == 1:
        y = h / 2
        return (f'<svg width="{w}" height="{h}">'
                f'<line x1="0" y1="{y}" x2="{w}" y2="{y}" '
                f'stroke="{color}" stroke-width="1.5"/></svg>')
    pts = []
    for i, v in enumerate(values):
        x = i * (w / (n - 1))
        y = h - ((v - vmin) / span) * h
        pts.append(f"{x:.1f},{y:.1f}")
    return (f'<svg width="{w}" height="{h}">'
            f'<polyline points="{" ".join(pts)}" '
            f'fill="none" stroke="{color}" stroke-width="1.5"/></svg>')


_MAGI_DAILY_HOUR_EST = 20
# Hardcoded duplicate of scheduler.py:MAGI_DAILY_HOUR_EST — scheduler.py's
# module-level signal.signal() call fails when imported from a Flask
# worker thread. Update both places if the daily hour changes.
# (BU-2 2026-06-09: council cadence is gate-primary; this ETA is only the
# daily clock-floor call. Gate wakes can convene the council sooner.)


def _next_magi_eta():
    """Return {label, countdown_min} for the next scheduled (daily-floor)
    MAGI cycle, or None on failure. Hardcoded to match scheduler.py. Gate
    wakes may convene the council sooner; this is only the clock floor."""
    try:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
    except Exception:
        return None
    now_est = datetime.now(ZoneInfo('America/New_York'))
    next_dt = now_est.replace(hour=_MAGI_DAILY_HOUR_EST, minute=0,
                              second=0, microsecond=0)
    if now_est.hour >= _MAGI_DAILY_HOUR_EST:
        next_dt = next_dt + timedelta(days=1)
    delta_min = int((next_dt - now_est).total_seconds() / 60)
    hh, mm = divmod(delta_min, 60)
    zone = next_dt.strftime('%Z')
    if hh > 0:
        label = f"{next_dt.hour:02d}:00 {zone} · in {hh}h {mm:02d}m"
    else:
        label = f"{next_dt.hour:02d}:00 {zone} · in {mm}m"
    return {'label': label, 'countdown_min': delta_min}


def _fetch_council_data():
    """
    Single-call fetcher for the council panels (hero, accuracy, attribution,
    council log). Returns a dict that the index() route can splat into
    render_template_string. All values are JSON-serialisable (no SQLite Row
    objects). Accuracy + attribution are scoped to the paper run
    (system_state['paper_run_started_utc']) so the panels grade the current
    council_v2 lineup, never the Letta/pre-rebuild eras.
    """
    import json as _json
    import re as _re
    from datetime import datetime as _dt, timedelta as _td
    from database import get_conn, get_recent_debate_records, get_agent_accuracy

    recent = get_recent_debate_records(limit=1)
    latest_debate = recent[0] if recent else None

    override_tags = []
    if latest_debate:
        # Parse JSON-encoded evidence into Python lists for the template
        for agent in ('casper', 'melchior', 'balthasar'):
            ev = latest_debate.get(f'{agent}_r0_evidence')
            if ev and isinstance(ev, str):
                try:
                    latest_debate[f'{agent}_r0_evidence_list'] = _json.loads(ev)
                except (ValueError, TypeError):
                    latest_debate[f'{agent}_r0_evidence_list'] = []
            else:
                latest_debate[f'{agent}_r0_evidence_list'] = ev or []
        # Hard-rule overrides are stored as a JSON-encoded list of bracketed
        # tags on the debate_record itself. Older rows (pre-migration) have
        # NULL — fall back to parsing magi_decisions.notes for those.
        raw_overrides = latest_debate.get('hard_rule_overrides')
        if raw_overrides:
            try:
                tags = _json.loads(raw_overrides) if isinstance(raw_overrides, str) else raw_overrides
                override_tags = sorted({
                    t.strip('[]') for t in tags if isinstance(t, str)
                })
            except (ValueError, TypeError):
                override_tags = []
        else:
            conn0 = get_conn()
            nrow = conn0.execute(
                "SELECT notes FROM magi_decisions ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn0.close()
            if nrow and nrow['notes']:
                override_tags = sorted(set(_re.findall(r"\[([A-Z_]+)\]", nrow['notes'])))

    # Blind-review decision summary for the latest cycle. The redesign records the
    # authorship-free decision (winning action / NO_CONSENSUS), the vote multiset, and
    # the consensus class (clear / reconciled / no_consensus) in council_json — none of
    # which exist in the arbiter-era rows. A row is blind-review iff it carries a seat
    # action column or a council_json blob; the template renders redesign vocabulary
    # only for those, falling back to the legacy view otherwise.
    latest_is_blind_review = False
    council_decision = None
    if latest_debate:
        latest_is_blind_review = bool(
            latest_debate.get('casper_r0_action')
            or latest_debate.get('melchior_r0_action')
            or latest_debate.get('balthasar_r0_action')
            or latest_debate.get('council_json')
        )
        cj_raw = latest_debate.get('council_json')
        if cj_raw:
            try:
                cj = _json.loads(cj_raw) if isinstance(cj_raw, str) else cj_raw
                council_decision = {
                    'decision': cj.get('decision'),
                    'vote_multiset': cj.get('vote_multiset'),
                    'consensus': cj.get('consensus'),
                    'reconciled': bool(cj.get('reconciled')),
                }
            except (ValueError, TypeError):
                council_decision = None

    # Paper-run scope start (set at the 2026-06-09 paper book reset).
    conn0 = get_conn()
    _ps = conn0.execute(
        "SELECT value FROM system_state WHERE key='paper_run_started_utc'"
    ).fetchone()
    conn0.close()
    paper_scope_start = (_ps['value'] if _ps else None) or '2026-06-09T21:03:46'
    # debate_records timestamps are naive UTC — strip any tz suffix so the
    # SQL string comparison is apples-to-apples.
    paper_cutoff = paper_scope_start.split('+')[0]
    try:
        _ps_dt = _dt.fromisoformat(paper_cutoff)
        _paper_days = max(
            (_dt.utcnow() - _ps_dt).total_seconds() / 86400.0, 0.01)
    except (ValueError, TypeError):
        _paper_days = 30.0

    # Per-agent accuracy cards — scored over the paper run only.
    # (The capitulation stat was dropped with the old panel: conditional R1
    # means revisions are too rare to chart.)
    council_accuracy = {}
    for a in ('casper', 'melchior', 'balthasar'):
        council_accuracy[a] = {'acc': get_agent_accuracy(a, _paper_days)}

    conn = get_conn()

    # Sparkline data — last 30 cycles, ordered oldest → newest
    rows = conn.execute(
        "SELECT casper_r0_conviction, melchior_r0_conviction, balthasar_r0_conviction "
        "FROM debate_records ORDER BY id DESC LIMIT 30"
    ).fetchall()
    sparkline_data = {a: [] for a in ('casper', 'melchior', 'balthasar')}
    for r in reversed(rows):
        for a in sparkline_data:
            v = r[f'{a}_r0_conviction']
            sparkline_data[a].append(float(v) if v is not None else 0.0)
    sparklines_svg = {
        a: _svg_sparkline(sparkline_data[a], w=140, h=28,
                          color={'casper': '#66ccff', 'melchior': '#ffcc66',
                                 'balthasar': '#ff88aa'}[a])
        for a in sparkline_data
    }

    # Council Log — last 20 cycles (all cycles, not just triggered debates),
    # each deep-linked to its Langfuse trace when one was recorded.
    log_rows = conn.execute(
        "SELECT cycle_id, timestamp, trigger, "
        "casper_r0_position, melchior_r0_position, balthasar_r0_position, "
        "casper_r0_action, debate_triggered, deadlock, council_json, "
        "final_grid_action, final_risk_action, "
        "hard_rule_overrides, fills_6h, trace_id "
        "FROM debate_records ORDER BY id DESC LIMIT 20"
    ).fetchall()
    council_log = []
    for r in log_rows:
        d = dict(r)
        raw = d.get('hard_rule_overrides')
        try:
            tags = _json.loads(raw) if isinstance(raw, str) else (raw or [])
        except (ValueError, TypeError):
            tags = []
        d['override_tags'] = [t.strip('[]') for t in tags if isinstance(t, str)]
        d['trace_url'] = _langfuse_trace_url(d.get('trace_id'))
        # Era-aware consensus label for the log's Consensus column. Blind-review rows
        # carry the consensus class in council_json (clear / reconciled / no_consensus);
        # arbiter rows fall back to the legacy debate_triggered flag.
        d['is_blind_review'] = bool(d.get('casper_r0_action') or d.get('council_json'))
        consensus = None
        cj_raw = d.get('council_json')
        if cj_raw:
            try:
                cj = _json.loads(cj_raw) if isinstance(cj_raw, str) else cj_raw
                if cj.get('decision') == 'NO_CONSENSUS' or d.get('deadlock'):
                    consensus = 'no_consensus'
                else:
                    consensus = 'reconciled' if cj.get('reconciled') else 'clear'
            except (ValueError, TypeError):
                consensus = None
        d['consensus_class'] = consensus
        council_log.append(d)

    cutoff = paper_cutoff

    # Outcome attribution — paper-run scope
    best_rows = conn.execute(
        "SELECT timestamp, final_grid_action, "
        "casper_r0_position, melchior_r0_position, balthasar_r0_position, "
        "fills_24h, pnl_24h FROM debate_records "
        "WHERE timestamp >= ? AND outcome_24h_backfilled=1 "
        "ORDER BY pnl_24h DESC LIMIT 5", (cutoff,)
    ).fetchall()
    worst_rows = conn.execute(
        "SELECT timestamp, final_grid_action, "
        "casper_r0_position, melchior_r0_position, balthasar_r0_position, "
        "fills_24h, pnl_24h FROM debate_records "
        "WHERE timestamp >= ? AND outcome_24h_backfilled=1 "
        "ORDER BY pnl_24h ASC LIMIT 5", (cutoff,)
    ).fetchall()
    summary_rows = conn.execute(
        "SELECT final_grid_action AS action, COUNT(*) AS count, "
        "AVG(fills_24h) AS avg_fills, AVG(pnl_24h) AS avg_pnl "
        "FROM debate_records WHERE timestamp >= ? AND outcome_24h_backfilled=1 "
        "GROUP BY final_grid_action ORDER BY avg_pnl DESC",
        (cutoff,)
    ).fetchall()

    conn.close()

    # Compute a relative-age label for the latest cycle so the operator
    # can see at a glance when MAGI last ran. Replaces the equivalent
    # indicator that lived on the (now-removed) Latest MAGI Decision panel.
    latest_debate_age_label, latest_debate_age_color = None, '#888'
    if latest_debate and latest_debate.get('timestamp'):
        try:
            _ts = _dt.fromisoformat(latest_debate['timestamp'])
            _age_min = (_dt.utcnow() - _ts).total_seconds() / 60.0
            if _age_min < 60:
                latest_debate_age_label = f"{_age_min:.0f} min ago"
                latest_debate_age_color = '#88cc88'  # green — fresh
            elif _age_min < 240:  # under 4h — within current cadence window
                latest_debate_age_label = f"{_age_min/60:.1f} h ago"
                latest_debate_age_color = '#cccc88'  # amber — normal
            else:
                latest_debate_age_label = f"{_age_min/60:.1f} h ago"
                latest_debate_age_color = '#ff8866'  # red — overdue (>4h gap)
        except (ValueError, TypeError):
            pass

    return {
        'latest_debate':          latest_debate,
        'latest_debate_age_label': latest_debate_age_label,
        'latest_debate_age_color': latest_debate_age_color,
        'latest_is_blind_review': latest_is_blind_review,
        'council_decision':       council_decision,
        'council_override_tags':  override_tags,
        'council_accuracy':       council_accuracy,
        'conviction_sparklines_svg': sparklines_svg,
        'council_log_rows':       council_log,
        'paper_scope_start':      paper_scope_start,
        'attribution_best':       [dict(r) for r in best_rows],
        'attribution_worst':      [dict(r) for r in worst_rows],
        'action_summary':         [dict(r) for r in summary_rows],
    }


def _langfuse_trace_url(trace_id):
    """Deep link into the Langfuse UI for one council cycle's trace (the
    full per-seat prompts/responses live there, not in the dashboard).
    Returns None when the cycle has no trace_id or Langfuse isn't
    configured — the template renders a dash instead of a link."""
    if not trace_id:
        return None
    base = (os.environ.get('LANGFUSE_BASE_URL')
            or _read_env_file_var('LANGFUSE_BASE_URL') or '').rstrip('/')
    proj = (os.environ.get('LANGFUSE_PROJECT_ID')
            or _read_env_file_var('LANGFUSE_PROJECT_ID'))
    if not (base and proj):
        return None
    return f"{base}/project/{proj}/traces/{trace_id}"


_LLM_CALLS_CACHE = {'ts': 0.0, 'data': None}
# Blind-review seat generations are named by the bare seat (the phase —
# propose / review / reconcile — lives in span metadata, not the name; see
# magi/agents/tracing.py:trace_seat). The arbiter-era ':rebuttal'/':synthesis'
# span names are retired with the relay council.
_SEAT_CALL_NAMES = ('casper', 'melchior', 'balthasar')


def _fetch_llm_calls_24h_safe():
    """Trailing-24h LLM call volume from Langfuse (the system of record for
    per-seat calls since the 2026-06-09 rebuild — the local token_usage table
    stopped being written at the Letta decoupling).

    Counts ONLY the named seat spans (casper / melchior / balthasar),
    because each cycle trace also carries auto-instrumented inner SDK spans
    (ADK generate_content, call_llm) that duplicate the same vendor calls — a
    raw GENERATION count overstates by ~1.7x. A seat may emit more than one
    generation per cycle (propose + reconcile phases), each counted as a real
    call. Traces = council cycles (includes manual/scratch runs, deliberately
    — off-service calls should be visible).

    Cached 60s per process. Falls back to a debate_records cycle count
    (calls=None) if Langfuse is unreachable. Never raises.
    """
    import time as _time
    if _LLM_CALLS_CACHE['data'] is not None and \
            _time.time() - _LLM_CALLS_CACHE['ts'] < 60:
        return _LLM_CALLS_CACHE['data']

    frm = (datetime.now(timezone.utc) - timedelta(hours=24)
           ).strftime('%Y-%m-%dT%H:%M:%SZ')
    pub = os.environ.get('LANGFUSE_PUBLIC_KEY') or _read_env_file_var('LANGFUSE_PUBLIC_KEY')
    sec = os.environ.get('LANGFUSE_SECRET_KEY') or _read_env_file_var('LANGFUSE_SECRET_KEY')
    base = (os.environ.get('LANGFUSE_BASE_URL') or _read_env_file_var('LANGFUSE_BASE_URL') or '').rstrip('/')

    data = None
    if pub and sec and base:
        try:
            import requests as _rq
            sess = _rq.Session()
            sess.auth = (pub, sec)
            t = sess.get(f"{base}/api/public/traces",
                         params={'fromTimestamp': frm, 'limit': 1},
                         timeout=3).json()
            # One paginated generations sweep, counted client-side against
            # the named seat spans (cheaper + more robust than one
            # name-filtered request per seat span).
            calls, page = 0, 1
            while page <= 5:  # 500 generations/24h ≫ any sane day
                o = sess.get(f"{base}/api/public/observations",
                             params={'type': 'GENERATION',
                                     'fromStartTime': frm,
                                     'limit': 100, 'page': page},
                             timeout=3).json()
                items = o.get('data') or []
                calls += sum(1 for ob in items
                             if ob.get('name') in _SEAT_CALL_NAMES)
                if page >= (o.get('meta', {}).get('totalPages') or 1):
                    break
                page += 1
            data = {'calls': calls,
                    'cycles': t.get('meta', {}).get('totalItems'),
                    'source': 'langfuse'}
        except Exception:
            data = None  # fall through to DB fallback

    if data is None:
        try:
            from database import get_conn as _get_conn
            conn = _get_conn()
            n = conn.execute(
                "SELECT COUNT(*) FROM debate_records "
                "WHERE timestamp >= datetime('now', '-24 hours')"
            ).fetchone()[0]
            conn.close()
            data = {'calls': None, 'cycles': n, 'source': 'db'}
        except Exception:
            data = {'calls': None, 'cycles': None, 'source': 'none'}

    _LLM_CALLS_CACHE['data'] = data
    _LLM_CALLS_CACHE['ts'] = _time.time()
    return data


def _fetch_readiness_safe():
    """Wrap magi.readiness.evaluate so a single bad gate never blocks the
    dashboard. On error returns None and the template hides the panel."""
    try:
        from magi.readiness import evaluate
        return evaluate()
    except Exception as e:
        app.logger.warning("readiness evaluate failed: %r", e)
        return None


def _fetch_gate_activity_safe():
    """Wrap database.get_gate_trigger_stats so a failure never blocks the
    dashboard. On error returns None and the template hides the panel."""
    try:
        from database import get_gate_trigger_stats
        return get_gate_trigger_stats(window_hours=168)
    except Exception as e:
        app.logger.warning("gate activity stats failed: %r", e)
        return None


_AGENT_HEALTH_ORDER = ('casper', 'melchior', 'balthasar')


def _fetch_agent_health():
    """
    Per-agent degradation health computed from the last 3 council rows in
    debate_records. ERA-AWARE — the blind-review and arbiter eras record a
    degraded seat differently, so each row is classified and graded on its own
    era's fingerprint (mirrors database._score_action_seat / the era dispatch in
    observer.backfill_seat_accuracy_scores):

      blind-review row (any seat has a non-NULL {seat}_r0_action): a seat is
        degraded iff ITS OWN action is NULL while a peer responded — i.e. the
        cycle ran and this seat failed to produce a candidate. There is no
        SAFE_DEFAULTS sentinel in this era (a non-responder is simply absent),
        so the legacy crux fingerprint never fires here.
      arbiter-era row (all three actions NULL): the legacy SAFE_DEFAULTS
        fingerprint — conviction ≈ 0 AND crux LIKE '(no response)%' — the
        parse-failure / model-degradation marker magi/council.py used.

    A full-council-crash row (every action NULL, no '(no response)' crux) is
    neither era's degradation signal and is ignored here — that is a council-level
    failure surfaced via alerts, not a per-seat health event.

    Returns: {agent_id: {status, degraded_count, total, model}}
    Status: 0 degraded → green, 1 → yellow, 2-3 → red.
    Empty / missing data → green (no signal yet, don't false-alarm).
    """
    from database import get_conn
    # Model labels come from the authoritative live lineup (seats.MODELS, the
    # declared single source of truth), NOT the agent_registry table — that table
    # held the arbiter-era models (e.g. Balthasar claude-sonnet-4-6) and drifted
    # stale when the redesign dropped Balthasar to claude-haiku-4-5.
    try:
        from magi.agents.seats import MODELS as _LIVE_MODELS
    except Exception:
        _LIVE_MODELS = {}
    out = {}
    conn = get_conn()
    try:
        model_by_agent = {a: _LIVE_MODELS.get(a, '') for a in _AGENT_HEALTH_ORDER}

        cols = ", ".join(
            f"{a}_r0_action AS {a}_act, {a}_r0_conviction AS {a}_conv, "
            f"{a}_r0_crux AS {a}_crux"
            for a in _AGENT_HEALTH_ORDER
        )
        rows = conn.execute(
            f"SELECT {cols} FROM debate_records ORDER BY id DESC LIMIT 3"
        ).fetchall()

        def _legacy_degraded(conv, crux):
            conv_zero = (conv is None) or (abs(float(conv)) < 1e-9)
            return conv_zero and (crux or '').startswith('(no response)')

        for agent in _AGENT_HEALTH_ORDER:
            degraded = total = 0
            for r in rows:
                blind_review_cycle = any(
                    r[f'{a}_act'] is not None for a in _AGENT_HEALTH_ORDER
                )
                if blind_review_cycle:
                    total += 1
                    if r[f'{agent}_act'] is None:       # peers answered, this seat didn't
                        degraded += 1
                else:
                    # arbiter-era / pre-blind-review row. Scoreable for this seat only
                    # if it carried a real signal (conviction or crux); a full-council
                    # crash (all NULL, no '(no response)') is no-signal and ignored.
                    conv, crux = r[f'{agent}_conv'], r[f'{agent}_crux']
                    if conv is not None or crux:
                        total += 1
                        if _legacy_degraded(conv, crux):
                            degraded += 1
            status = 'green' if degraded == 0 else 'yellow' if degraded == 1 else 'red'
            out[agent] = {
                'status': status,
                'degraded_count': degraded,
                'total': total,
                'model': model_by_agent.get(agent, ''),
            }
    finally:
        conn.close()
    return out


def check_scheduler_alive():
    try:
        log_path = '/root/xrp_grid/magi.log'
        if not os.path.exists(log_path):
            return False
        mtime = os.path.getmtime(log_path)
        age_minutes = (datetime.now(timezone.utc).timestamp() - mtime) / 60
        return age_minutes < 90
    except Exception:
        return False


@app.route('/')
def index():
    now = _to_et(datetime.now(timezone.utc))

    indicators = get_latest_indicators('1h') or {}
    grid = get_current_grid_state() or {}
    inventory = get_latest_inventory() or {}
    price = engine.get_current_price() or 0.0

    guardrails_ok, guardrail_failures = check_all_guardrails()
    ks_active = kill_switch_active()

    # P&L snapshot — paper-scoped when the engine is in paper mode (the live
    # scope stays txid-based and untouched; see grid/pnl.py).
    snap = get_pnl_snapshot(price, paper=engine.paper)

    recent_orders = get_recent_grid_orders(limit=25)

    mins_since = snap.get('time_since_last_fill_minutes')
    if mins_since is None:
        fill_age_label = "no fills yet"
        # Check if any open orders have been sitting > 24h with no fills
        from database import get_conn as _get_conn
        _conn = _get_conn()
        _old_open = _conn.execute(
            """SELECT COUNT(*) FROM grid_orders
               WHERE status='open'
               AND timestamp < datetime('now', '-24 hours')"""
        ).fetchone()[0]
        _conn.close()
        if _old_open > 0:
            fill_age_color = "#ff4444"
            fill_stale = True
        else:
            fill_age_color = "#888"
            fill_stale = False
    else:
        hours = mins_since / 60
        if hours < 1:
            fill_age_label = f"{int(mins_since)} min ago"
        elif hours < 24:
            fill_age_label = f"{hours:.1f} h ago"
        else:
            fill_age_label = f"{hours/24:.1f} d ago"
        fill_age_color = "#ff4444" if hours > 24 else "#00ff88" if hours < 2 else "#ffaa00"
        fill_stale = hours > 24

    council_data = _fetch_council_data()
    _latest_debate = council_data.get('latest_debate') or {}
    grid_status = _grid_status(
        _latest_debate.get('final_grid_action'),
        check_scheduler_alive(),
        ks_active,
    )

    return render_template_string(HTML_TEMPLATE,
        now=now,
        price=f"{price:.4f}" if price else "N/A",
        vol_regime=indicators.get('vol_regime', 'N/A'),
        atr_pct=f"{indicators.get('atr_percentile', 0):.1f}",
        vwap_dev=f"{indicators.get('vwap_dev_pct', 0):.3f}",
        grid_centre=f"{grid.get('centre_price', 0):.4f}" if grid.get('centre_price') else "N/A",
        grid_spacing=f"{grid.get('spacing_pct', 0)*100:.3f}" if grid.get('spacing_pct') else "N/A",
        grid_levels=grid.get('levels', 0),
        xrp_held=f"{inventory.get('xrp_held', 0):.4f}",
        usd_held=f"{inventory.get('usd_held', 0):.2f}",
        net_position=f"{inventory.get('net_position_usd', 0):.2f}",
        inventory_skew=f"{inventory.get('inventory_skew', 0):.3f}",
        scheduler_alive=check_scheduler_alive(),
        guardrails_ok=guardrails_ok,
        guardrail_failures=guardrail_failures,
        kill_switch=ks_active,
        paper_mode=engine.paper,
        # P&L tiles
        pnl_realized=snap['realized'],
        pnl_realized_fmt=f"{snap['realized']:.4f}",
        pnl_unrealized=snap['unrealized'],
        pnl_unrealized_fmt=f"{snap['unrealized']:.4f}",
        pnl_total=snap['total'],
        pnl_total_fmt=f"{snap['total']:.4f}",
        pnl_alpha=snap.get('alpha_vs_hold'),
        pnl_alpha_fmt=(f"{snap['alpha_vs_hold']:.4f}" if snap.get('alpha_vs_hold') is not None else "—"),
        pnl_beta_fmt=(f"{snap['inventory_hold_delta']:.4f}" if snap.get('inventory_hold_delta') is not None else "—"),
        pnl_fees_fmt=f"{snap['fees']:.4f}",
        pnl_fill_count=snap['fill_count'],
        pnl_matched_trips=snap['matched_round_trips'],
        pnl_unmatched_buys=snap['unmatched_buys'],
        pnl_baseline_equity=snap['baseline_equity'],
        pnl_current_equity=snap['current_equity'],
        pnl_baseline_equity_fmt=(f"{snap['baseline_equity']:.2f}" if snap['baseline_equity'] is not None else "—"),
        pnl_current_equity_fmt=(f"{snap['current_equity']:.2f}" if snap['current_equity'] is not None else "—"),
        pnl_win_rate=snap['win_rate'],
        pnl_avg_per_trip=snap['avg_pnl_per_round_trip'],
        pnl_fills_today=snap['fills_today'],
        pnl_mins_since=snap['time_since_last_fill_minutes'],
        fill_age_label=fill_age_label,
        fill_age_color=fill_age_color,
        fill_stale=fill_stale,
        # Recent orders table
        recent_orders=recent_orders,
        order_pnl_map=snap['order_pnl_map'],
        magi_token=os.environ.get('MAGI_TRIGGER_TOKEN', ''),
        open_alerts=_get_open_alerts_safe(),
        next_magi=_next_magi_eta(),
        agent_health=_fetch_agent_health(),
        gate_monitor=_fetch_gate_monitor_safe(),
        llm_calls=_fetch_llm_calls_24h_safe(),
        readiness=_fetch_readiness_safe(),
        gate_activity=_fetch_gate_activity_safe(),
        exposure_cap=_fetch_exposure_cap_safe(),
        grid_status=grid_status,
        # Phase 5: agent council panels (single splat from helper)
        **council_data,
    )


def _fetch_exposure_cap_safe():
    """Down-walk exposure-cap state (Fix 2, 2026-06-11) for the EXPOSURE CAP
    chip. Reads the system_state keys grid/engine.py maintains; never raises
    — returns a CLEAR/zero state on any error so the dashboard renders."""
    try:
        from datetime import datetime
        from database import get_system_state
        from config import DOWN_WALK_CAP_STREAK, DOWN_WALK_LINK_HOURS
        try:
            streak = int(get_system_state('down_walk_streak', default='0') or 0)
        except (TypeError, ValueError):
            streak = 0
        last_centre = get_system_state('down_walk_last_centre', default=None)
        last_ts = get_system_state('down_walk_last_ts', default=None)
        last_age_h = None
        if last_ts:
            try:
                last_age_h = round(
                    (datetime.utcnow() - datetime.fromisoformat(last_ts))
                    .total_seconds() / 3600.0, 1)
            except (TypeError, ValueError):
                pass
        engaged = streak >= DOWN_WALK_CAP_STREAK
        return {
            'streak': streak,
            'threshold': DOWN_WALK_CAP_STREAK,
            'link_hours': DOWN_WALK_LINK_HOURS,
            'engaged': engaged,
            'colour': 'red' if engaged else ('yellow' if streak else 'green'),
            'last_centre': (f"{float(last_centre):.4f}" if last_centre else None),
            'last_ts': last_ts,
            'last_age_h': last_age_h,
        }
    except Exception as e:
        app.logger.warning("exposure cap fetch failed: %r", e)
        return {'streak': 0, 'threshold': 3, 'link_hours': 48,
                'engaged': False, 'colour': 'green',
                'last_centre': None, 'last_ts': None, 'last_age_h': None}


def _get_open_alerts_safe():
    """Wrap get_open_alerts so a DB hiccup never blocks dashboard render."""
    try:
        from database import get_open_alerts
        return get_open_alerts()
    except Exception as e:
        app.logger.warning("get_open_alerts failed: %r", e)
        return []


@app.route('/api/status')
def api_status():
    indicators = get_latest_indicators('1h') or {}
    grid = get_current_grid_state() or {}
    inventory = get_latest_inventory() or {}
    decisions = get_recent_magi_decisions(1)
    price = engine.get_current_price()
    return jsonify({
        'price': price,
        'vol_regime': indicators.get('vol_regime'),
        'vwap_dev_pct': indicators.get('vwap_dev_pct'),
        'grid_centre': grid.get('centre_price'),
        'grid_spacing_pct': grid.get('spacing_pct'),
        'inventory': {
            'xrp': inventory.get('xrp_held'),
            'usd': inventory.get('usd_held'),
            'net_usd': inventory.get('net_position_usd'),
            'skew': inventory.get('inventory_skew')
        },
        'latest_magi': decisions[0] if decisions else None,
        'scheduler_alive': check_scheduler_alive(),
        'paper_mode': engine.paper
    })


@app.route('/api/pnl')
def api_pnl():
    price = engine.get_current_price() or 0.0
    snap = get_pnl_snapshot(price, paper=engine.paper)
    pnl_denom = snap.get('baseline_equity') or MAX_INVENTORY_USD
    live_pnl_pct = round(snap['total'] / pnl_denom * 100, 4) if pnl_denom and pnl_denom > 0 else 0.0
    return jsonify({
        **snap,
        'live_pnl_pct': live_pnl_pct,
    })


@app.route('/api/recent_orders')
def api_recent_orders():
    limit = min(int(request.args.get('limit', 25)), 200)
    orders = get_recent_grid_orders(limit=limit)
    return jsonify({'orders': orders, 'count': len(orders)})


@app.route('/api/trigger_learning', methods=['POST'])
def trigger_learning():
    force = request.args.get('force', 'false').lower() == 'true'
    try:
        result = run_learning_cycle(force=force)
        return jsonify(result or {'skipped': True, 'reason': 'unknown'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/trigger_magi', methods=['POST'])
def trigger_magi():
    if MAGI_TRIGGER_TOKEN:
        provided = request.headers.get('X-Magi-Token', '') or \
                   request.args.get('token', '')
        if provided != MAGI_TRIGGER_TOKEN:
            return jsonify({'ok': False, 'error': 'Unauthorized'}), 403
    try:
        import urllib.request as _ur
        import json as _json
        import socket as _socket
        _socket.setdefaulttimeout(120)  # MAGI cycle can take ~30s
        req = _ur.Request(
            'http://127.0.0.1:5001/internal/trigger_magi',
            data=b'',
            method='POST'
        )
        with _ur.urlopen(req) as resp:
            result = _json.loads(resp.read().decode())
        from database import mark_magi_decision_applied
        # Mark applied if decision_id available
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/resolve_alert', methods=['POST'])
def resolve_alert():
    if MAGI_TRIGGER_TOKEN:
        provided = request.headers.get('X-Magi-Token', '') or \
                   request.args.get('token', '')
        if provided != MAGI_TRIGGER_TOKEN:
            return jsonify({'ok': False, 'error': 'Unauthorized'}), 403
    try:
        alert_id = int(request.args.get('id', '0'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'invalid id'}), 400
    if alert_id <= 0:
        return jsonify({'ok': False, 'error': 'missing id'}), 400
    try:
        from database import mark_alert_resolved
        updated = mark_alert_resolved(alert_id)
        return jsonify({'ok': True, 'updated': updated})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/toggle_kill', methods=['POST'])
def toggle_kill():
    try:
        if os.path.exists(KILL_SWITCH_FILE):
            os.remove(KILL_SWITCH_FILE)
            active = False
        else:
            open(KILL_SWITCH_FILE, 'w').close()
            active = True
        return jsonify({'kill_switch': active})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/active_grid_levels')
def api_active_grid_levels():
    from database import get_conn, get_current_grid_state
    conn = get_conn()
    rows = conn.execute(
        "SELECT order_id, side, price FROM grid_orders WHERE status='open' "
        "ORDER BY price ASC"
    ).fetchall()
    conn.close()
    levels = [
        {'order_id': r['order_id'], 'side': r['side'], 'price': r['price']}
        for r in rows
    ]
    grid_state = get_current_grid_state() or {}
    return jsonify({
        'levels': levels,
        'centre_price': grid_state.get('centre_price'),
        'spacing_pct': grid_state.get('spacing_pct'),
        'level_count': grid_state.get('levels'),
    })


@app.route('/chart')
def chart():
    return CHART_HTML_TEMPLATE


# ── Phase 5: council API routes ─────────────────────────────────────

@app.route('/api/council/latest')
def api_council_latest():
    """Most recent debate_records row as JSON (evidence parsed to list)."""
    from database import get_recent_debate_records
    import json as _json
    rows = get_recent_debate_records(limit=1)
    if not rows:
        return jsonify(None)
    row = rows[0]
    for agent in ('casper', 'melchior', 'balthasar'):
        ev = row.get(f'{agent}_r0_evidence')
        if isinstance(ev, str) and ev:
            try:
                row[f'{agent}_r0_evidence'] = _json.loads(ev)
            except (ValueError, TypeError):
                pass
    return jsonify(row)


@app.route('/api/council/history')
def api_council_history():
    """List of recent debate_records. ?limit=N (default 20, max 200)."""
    from database import get_recent_debate_records
    import json as _json
    try:
        n = int(request.args.get('limit', 20))
    except (TypeError, ValueError):
        n = 20
    n = max(1, min(n, 200))
    rows = get_recent_debate_records(limit=n)
    for row in rows:
        for agent in ('casper', 'melchior', 'balthasar'):
            ev = row.get(f'{agent}_r0_evidence')
            if isinstance(ev, str) and ev:
                try:
                    row[f'{agent}_r0_evidence'] = _json.loads(ev)
                except (ValueError, TypeError):
                    pass
    return jsonify(rows)


@app.route('/api/council/accuracy')
def api_council_accuracy():
    """{casper, melchior, balthasar} → {acc_7d, acc_30d, capit_30d}."""
    from database import get_agent_accuracy, get_capitulation_rate
    out = {}
    for a in ('casper', 'melchior', 'balthasar'):
        out[a] = {
            'acc_7d':    get_agent_accuracy(a, 7),
            'acc_30d':   get_agent_accuracy(a, 30),
            'capit_30d': get_capitulation_rate(a, 30),
        }
    return jsonify(out)


@app.route('/api/agent_health')
def api_agent_health():
    """Per-agent degradation health — one of the two visibility legs of the
    BYOK contingency plan (Dim 1). See _fetch_agent_health() for the
    degradation predicate."""
    return jsonify(_fetch_agent_health())


def _fetch_gate_monitor_safe():
    """Read the latest ws_health row. Returns a dict shaped for the
    GATE MON chip template; 'state' falls back to 'unknown' on error."""
    import time as _time
    try:
        from database import get_conn as _get_conn
        conn = _get_conn()
        row = conn.execute(
            "SELECT timestamp, state, last_heartbeat_age_sec, "
            "reconnect_count_1h, last_tick_age_sec, notes "
            "FROM ws_health ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception:
        return {'state': 'unknown'}
    if not row:
        return {'state': 'unknown'}
    return {
        'state':                  row['state'],
        'last_state_update_ts':   row['timestamp'],
        'row_age_sec':            (_time.time() - float(row['timestamp']))
                                    if row['timestamp'] else None,
        'last_heartbeat_age_sec': row['last_heartbeat_age_sec'],
        'reconnect_count_1h':     row['reconnect_count_1h'],
        'notes':                  row['notes'],
    }


@app.route('/api/gate_monitor')
def api_gate_monitor():
    """Live gate-monitor WS health. Reads the most recent ws_health row.
    state ∈ {'starting','connected','reconnecting','degraded','disconnected'}.
    Empty result means gate_monitor has never written — chip should
    render as 'unknown' until it does."""
    import time as _time
    try:
        from database import get_conn as _get_conn
        conn = _get_conn()
        row = conn.execute(
            "SELECT timestamp, state, last_heartbeat_age_sec, "
            "reconnect_count_1h, last_tick_age_sec, notes "
            "FROM ws_health ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception as e:
        return jsonify({'state': 'unknown', 'error': str(e)})
    if not row:
        return jsonify({'state': 'unknown',
                        'message': 'ws_health table empty'})
    now = _time.time()
    row_age_sec = now - float(row['timestamp']) if row['timestamp'] else None
    return jsonify({
        'state':                  row['state'],
        'last_state_update_ts':   row['timestamp'],
        'row_age_sec':            row_age_sec,
        'last_heartbeat_age_sec': row['last_heartbeat_age_sec'],
        'reconnect_count_1h':     row['reconnect_count_1h'],
        'notes':                  row['notes'],
    })


@app.route('/api/gate_activity')
def api_gate_activity():
    """Gate trigger fire-rates over a trailing window (default 7d) + count of
    off-schedule MAGI wakes. For tuning whether gate thresholds are too loose
    (firing constantly) or too tight (never firing). Read-only."""
    try:
        from database import get_gate_trigger_stats
        hours = request.args.get('hours', default=168, type=int)
        return jsonify(get_gate_trigger_stats(window_hours=hours))
    except Exception as e:
        return jsonify({'error': str(e), 'triggers': [], 'wakes': {}})


@app.route('/api/readiness')
def api_readiness():
    """Live capital readiness gates (lifetime). Pure read-only; see
    magi/readiness.py."""
    return jsonify(_fetch_readiness_safe())


if __name__ == "__main__":
    from magi import adam
    adam.init("dashboard")
    host, port = '0.0.0.0', 5000
    # Production: serve under waitress (a real WSGI server). Flask's built-in
    # app.run() is dev tooling and is not meant to sit behind the cloudflared
    # tunnel long-term. Fall back to app.run only if waitress is somehow absent,
    # so a dev box without it still boots.
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except ModuleNotFoundError:
        app.run(host=host, port=port, debug=False)

from flask import Flask, jsonify, render_template_string, request, session, redirect, url_for
import logging
import os
from datetime import datetime, timezone, timedelta
from database import (
    get_latest_indicators, get_current_grid_state,
    get_latest_inventory, get_recent_magi_decisions,
    get_cost_summary, get_cost_today, get_all_shadow_states,
    get_recent_grid_orders, get_best_shadow_from_db, get_fills_today_count
)
from grid.engine import GridEngine
from grid.pnl import get_pnl_snapshot
from magi.costs import get_fixed_monthly_total, FIXED_SUBSCRIPTIONS
from magi.learning import run_learning_cycle
from guardrails import check_all_guardrails, kill_switch_active
from config import KILL_SWITCH_FILE, MAX_INVENTORY_USD

log = logging.getLogger('dashboard')
app = Flask(__name__)
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

      /* Generic cards (Market, Grid State, Paper P&L, Inventory, Costs) */
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

      /* Council Accuracy, Council Evolution, Outcome Attribution cards */
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
          ".       melchior  .        "
          "casper  core      balthasar";
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
        {{ now }} EST &nbsp;|&nbsp; Auto-refresh 30s &nbsp;|&nbsp;
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

    {% if council_levers is not none %}
    {% set cl = council_levers %}
    {% set cl_n = cl.get('n_cycles_post_recreation') or 0 %}
    <div class="agent-health-panel">
        <div class="agent-health-title">⬢ COUNCIL LEVERS — last {{ cl.get('most_recent_window_n') or cl_n }} post-recreation cycles ({{ cl.get('cutoff', '')[:10] }}+)</div>
        <div class="agent-health-chips">
            {% set ra = cl.get('regime_action') or {} %}
            {% set ra_total = (ra.get('EXECUTE') or 0) + (ra.get('DEFER_STRUCTURAL') or 0) + (ra.get('STAND_DOWN') or 0) + (ra.get('(missing)') or 0) %}
            {% set ra_nondefault = (ra.get('DEFER_STRUCTURAL') or 0) + (ra.get('STAND_DOWN') or 0) %}
            {% set ra_color = 'green' if ra_nondefault == 0 else ('yellow' if ra_nondefault < (ra_total / 2) else 'red') %}
            <div class="agent-chip health-{{ ra_color }}">
                <div class="chip-head">
                    <span class="chip-dot"></span>
                    <span class="chip-name">CASPER regime_action</span>
                    <span class="chip-status">{{ ra_nondefault }}/{{ ra_total }} NON-EXEC</span>
                </div>
                <div class="chip-degraded">
                    EXEC {{ ra.get('EXECUTE') or 0 }} · DEFER {{ ra.get('DEFER_STRUCTURAL') or 0 }} · STANDDOWN {{ ra.get('STAND_DOWN') or 0 }}
                </div>
                <div class="chip-model">missing {{ ra.get('(missing)') or 0 }}</div>
            </div>

            {% set gv = cl.get('geometry_veto') or {} %}
            {% set gv_total = (gv.get('PROCEED') or 0) + (gv.get('HOLD_GEOMETRY') or 0) + (gv.get('RISK_BLOCK') or 0) + (gv.get('(missing)') or 0) %}
            {% set gv_nondefault = (gv.get('HOLD_GEOMETRY') or 0) + (gv.get('RISK_BLOCK') or 0) %}
            {% set gv_color = 'green' if gv_nondefault == 0 else ('yellow' if gv_nondefault < (gv_total / 2) else 'red') %}
            <div class="agent-chip health-{{ gv_color }}">
                <div class="chip-head">
                    <span class="chip-dot"></span>
                    <span class="chip-name">BALTHASAR geometry_veto</span>
                    <span class="chip-status">{{ gv_nondefault }}/{{ gv_total }} NON-PROCEED</span>
                </div>
                <div class="chip-degraded">
                    PROCEED {{ gv.get('PROCEED') or 0 }} · HOLD {{ gv.get('HOLD_GEOMETRY') or 0 }} · BLOCK {{ gv.get('RISK_BLOCK') or 0 }}
                </div>
                <div class="chip-model">missing {{ gv.get('(missing)') or 0 }}</div>
            </div>

            {% set vt = cl.get('veto_tag_counts') or {} %}
            {% set downgrade_cycles = cl.get('cycles_with_council_downgrade') or 0 %}
            {% set vt_color = 'green' if downgrade_cycles == 0 else 'yellow' %}
            <div class="agent-chip health-{{ vt_color }}">
                <div class="chip-head">
                    <span class="chip-dot"></span>
                    <span class="chip-name">VETO TAGS FIRED</span>
                    <span class="chip-status">{{ downgrade_cycles }} CYCLES</span>
                </div>
                <div class="chip-degraded">
                    REGIME_DEFER {{ vt.get('[REGIME_DEFER]') or 0 }} · STANDDOWN {{ vt.get('[REGIME_STANDDOWN]') or 0 }}
                </div>
                <div class="chip-model">
                    HOLD_GEOM {{ vt.get('[BALTHASAR_HOLD_GEOMETRY]') or 0 }} · RISK_BLOCK {{ vt.get('[BALTHASAR_RISK_BLOCK]') or 0 }}
                </div>
            </div>
        </div>
    </div>
    {% endif %}

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
            {{ a.timestamp }} —
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
          <div class="agent-name">MELCHIOR · 1</div>
          <div class="agent-position">{{ latest_debate.melchior_r0_position or '—' }}</div>
          <div class="agent-conviction">conv {{ '%.2f'|format(m_conv) }}</div>
        </div>
        <div class="magi-core">
          <div class="core-label">MAGI</div>
          <div class="core-cycle">{{ latest_debate.cycle_id[-10:] if latest_debate.cycle_id else '—' }}</div>
          {% if latest_debate_age_label %}
          <div class="core-age">{{ latest_debate_age_label }}</div>
          {% endif %}
        </div>
        {% set c_conv = latest_debate.casper_r0_conviction or 0 %}
        {% set c_cls = 'conv-high' if c_conv >= 0.75 else ('conv-med' if c_conv >= 0.5 else 'conv-low') %}
        <div class="magi-agent agent-casper {{ c_cls }}">
          <div class="agent-name">CASPER · 3</div>
          <div class="agent-position">{{ latest_debate.casper_r0_position or '—' }}</div>
          <div class="agent-conviction">conv {{ '%.2f'|format(c_conv) }}</div>
        </div>
        {% set b_conv = latest_debate.balthasar_r0_conviction or 0 %}
        {% set b_cls = 'conv-high' if b_conv >= 0.75 else ('conv-med' if b_conv >= 0.5 else 'conv-low') %}
        <div class="magi-agent agent-balthasar {{ b_cls }}">
          <div class="agent-name">BALTHASAR · 2</div>
          <div class="agent-position">{{ latest_debate.balthasar_r0_position or '—' }}</div>
          <div class="agent-conviction">conv {{ '%.2f'|format(b_conv) }}</div>
        </div>
      </div>
      <div class="magi-codebox">
        <div class="codebox-header">CODE · STATUS</div>
        <div class="codebox-body">
          {% if next_magi %}
          <div class="row"><span class="label">Next</span><span class="value">{{ next_magi.label }}</span></div>
          {% endif %}
          <div class="row"><span class="label">Centre</span><span class="value">${{ grid_centre }}</span></div>
          <div class="row"><span class="label">Spacing</span><span class="value">{{ grid_spacing }}%</span></div>
          <div class="row"><span class="label">Levels</span><span class="value">{{ grid_levels }}</span></div>
          <div class="row"><span class="label">Mode</span><span class="value" style="color:{{ '#ffaa00' if paper_mode else '#ff4444' }};">{{ 'PAPER' if paper_mode else 'LIVE' }}</span></div>
          <div class="row"><span class="label">Action</span><span class="value">{{ latest_debate.final_grid_action or '—' }}</span></div>
          <div class="row"><span class="label">Risk</span><span class="value">{{ latest_debate.final_risk_action or '—' }}</span></div>
        </div>
      </div>
    </div>
    {% endif %}

    <h2>LIVE CHART</h2>
    <iframe src="/chart"
            style="width:100%; height:480px; border:1px solid #00ff8844;
                   border-radius:4px; background:#0a0a0a;"
            scrolling="no"></iframe>

    <details style="margin-bottom:14px;" id="d-agent-reasoning">
    <summary style="cursor:pointer; color:#88aaff; font-size:0.9em;">▸ AGENT REASONING — full R0 evidence + cruxes + overrides (click to expand)</summary>
    <!-- ── Phase 5 PANEL 1: Agent Council ───────────────────────── -->
    <h2>Agent Council</h2>
    {% if latest_debate %}
    <div style="color:#666; font-size:0.78em; margin-bottom:6px;">
        cycle <span style="color:#88aaff;">{{ latest_debate.cycle_id }}</span>
        &nbsp;|&nbsp; {{ latest_debate.timestamp }}
        {% if latest_debate_age_label %}
        &nbsp;<span style="color:{{ latest_debate_age_color }};">({{ latest_debate_age_label }})</span>
        {% endif %}
        &nbsp;|&nbsp; Debate triggered:
        {% if latest_debate.debate_triggered %}
            <span class="debate-flag-yes">YES</span>
            ({{ latest_debate.conflict_pair or '?' }})
        {% else %}
            <span class="debate-flag-no">no</span>
        {% endif %}
    </div>
    {% if latest_debate.deadlock %}
    <div class="deadlock-banner">⚠ DEADLOCK ON LAST CYCLE — HUMAN REVIEW REQUESTED</div>
    {% endif %}
    {% if council_override_tags %}
    <div class="override-line">Hard rule overrides applied: {{ council_override_tags|join(', ') }}</div>
    {% endif %}
    <div class="council-row">
        {% for agent in ['casper', 'melchior', 'balthasar'] %}
        {% set pos = latest_debate[agent + '_r0_position'] or '—' %}
        {% set conv = latest_debate[agent + '_r0_conviction'] or 0 %}
        {% set crux = latest_debate[agent + '_r0_crux'] or '' %}
        {% set evidence = latest_debate[agent + '_r0_evidence_list'] or [] %}
        <div class="council-card">
            <div class="council-name">{{ agent }}</div>
            <div class="council-pos {{ pos }}">{{ pos }}</div>
            <div class="conv-track">
              <div class="conv-seg {{ 'active' if conv >= 0.34 else '' }}"></div>
              <div class="conv-seg {{ 'active' if conv >= 0.67 else '' }}"></div>
              <div class="conv-seg {{ 'active' if conv >= 0.90 else '' }}"></div>
            </div>
            <div class="conv-pct">conviction {{ (conv*100)|round(0)|int }}%</div>
            {% if crux %}<div class="council-crux">"{{ crux }}"</div>{% endif %}
            {% if evidence %}
            <ul class="council-evidence">
                {% for e in evidence %}<li>{{ e }}</li>{% endfor %}
            </ul>
            {% endif %}
        </div>
        {% endfor %}
    </div>
    {% else %}
    <div style="color:#666; margin-bottom:18px;">No debate records yet — first cycle pending.</div>
    {% endif %}
    </details>

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
    <h2>Paper P&amp;L</h2>
    <div style="font-size:0.85em; color:#666; margin-bottom:8px;">
        Last fill: <span style="color:{{ fill_age_color }};">{{ fill_age_label }}</span>
        {% if fill_stale %}
        <span style="color:#ff4444; margin-left:12px;">
            ⚠ No fills in 24h+ — metrics below describe historical activity, not current operation.
        </span>
        {% endif %}
    </div>
    <div class="grid">
        <div class="card">
            <div class="label">Realized P&amp;L</div>
            <div class="value {{ 'pnl-pos' if pnl_realized >= 0 else 'pnl-neg' }}">${{ pnl_realized_fmt }}</div>
            <div class="sub">{{ pnl_fill_count }} total fills &nbsp;|&nbsp; {{ pnl_matched_trips }} round trips</div>
        </div>
        <div class="card">
            <div class="label">Unrealized P&amp;L</div>
            <div class="value {{ 'pnl-pos' if pnl_unrealized >= 0 else 'pnl-neg' }}">${{ pnl_unrealized_fmt }}</div>
            <div class="sub">{{ pnl_unmatched_buys }} open buy position{{ 's' if pnl_unmatched_buys != 1 else '' }}</div>
        </div>
        <div class="card">
            <div class="label">Total P&amp;L</div>
            <div class="value {{ 'pnl-pos' if pnl_total >= 0 else 'pnl-neg' }}">${{ pnl_total_fmt }}</div>
            <div class="sub">Fees paid: ${{ pnl_fees_fmt }}</div>
        </div>
        <div class="card">
            <div class="label">vs Best Shadow</div>
            <div class="value {{ 'pnl-pos' if live_vs_shadow >= 0 else 'pnl-neg' }}">{{ live_vs_shadow_fmt }}%</div>
            <div class="sub">
                Live: {{ live_pnl_pct_fmt }}%
                &nbsp;/&nbsp;
                Shadow {{ best_shadow_level or '—' }}-lv: {{ best_shadow_pnl_fmt }}%
            </div>
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
          <span class="readiness-meta">trailing {{ (gate_activity.window_hours // 24) }}d · fires shown 24h/window · ★ = wakes MAGI off-schedule</span>
          <span class="readiness-meta">off-schedule wakes: {{ gate_activity.wakes.last_24h }} (24h) · {{ gate_activity.wakes.window }} (window)</span>
        </div>
        {% if gate_activity.triggers %}
        <div class="gate-grid">
          {% for t in gate_activity.triggers %}
          <div class="gate-chip {{ 'gate-pass' if t.fires_window else 'gate-na' }}"
               onclick='console.log({{ t|tojson }})'
               title="{{ t.trigger_id }} · {{ t.evals }} evals in window · last fired details → console">
            <div class="gate-head">
              <span class="gate-id">{{ t.trigger_id }}{% if t.trigger_id in ['T14','T2','T11'] %} ★{% endif %}</span>
              <span class="gate-pill">{{ t.fires_24h }}/{{ t.fires_window }}</span>
            </div>
            <div class="gate-value">{{ t.fires_window }} fires</div>
            <div class="gate-label">{{ 'wakes MAGI' if t.trigger_id in ['T14','T2','T11'] else 'context-only' }}</div>
          </div>
          {% endfor %}
        </div>
        {% else %}
        <div class="readiness-meta">no gate evaluations recorded in window</div>
        {% endif %}
      </div>
    </div>
    {% endif %}

    <h2>Manual Actions</h2>
    <div style="margin-top:10px; display:flex; flex-wrap:wrap; gap:10px; align-items:center;">
        <button onclick="triggerMagi(this)" style="background:#00ff8822; color:#00ffcc; border:2px solid #00ff88; padding:10px 24px; font-family:monospace; font-size:1em; font-weight:bold; cursor:pointer; border-radius:4px;">
            Trigger MAGI Cycle
        </button>
        <button onclick="triggerLearning()" style="background:#00ccff11; color:#4488aa; border:1px solid #00ccff33; padding:8px 14px; font-family:monospace; font-size:0.8em; cursor:pointer; border-radius:4px;">
            Generate Daily Summary
        </button>
        <button onclick="triggerLearning(true)" style="background:#ffaa0011; color:#887744; border:1px solid #ffaa0033; padding:8px 14px; font-family:monospace; font-size:0.8em; cursor:pointer; border-radius:4px;">
            Generate Summary (Weekend Override)
        </button>
        <button onclick="toggleKill()" id="kill-btn" style="background:{{ '#ff000033' if kill_switch else '#33000022' }}; color:{{ '#ff4444' if kill_switch else '#aa3333' }}; border:1px solid {{ '#ff4444' if kill_switch else '#550000' }}; padding:8px 14px; font-family:monospace; font-size:0.8em; cursor:pointer; border-radius:4px;">
            {{ '⬛ DEACTIVATE KILL SWITCH' if kill_switch else '⬛ ACTIVATE KILL SWITCH' }}
        </button>
        <span id="magi-status" style="color:#888; font-size:0.85em;"></span>
        <span id="learning-status" style="color:#888; font-size:0.85em;"></span>
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
    function triggerLearning(force) {
        const status = document.getElementById('learning-status');
        status.textContent = 'Running learning cycle...';
        status.style.color = '#ffaa00';
        fetch('/api/trigger_learning' + (force ? '?force=true' : ''), {method: 'POST'})
            .then(r => r.json())
            .then(data => {
                if (data.skipped) {
                    status.textContent = 'Skipped: ' + data.reason;
                    status.style.color = '#888';
                } else {
                    status.textContent = 'Done — ' + data.decisions_count + ' decisions processed';
                    status.style.color = '#00ff88';
                }
            })
            .catch(e => {
                status.textContent = 'Error: ' + e;
                status.style.color = '#ff4444';
            });
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

    <h2>Recent Orders</h2>
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
                <td style="color:#666;">{{ (o.filled_at or o.timestamp or '')[:16] }}</td>
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

    <h2>Market</h2>
    <div class="grid">
        <div class="card">
            <div class="label">XRP/USD Price</div>
            <div class="value">${{ price }}</div>
        </div>
        <div class="card">
            <div class="label">Vol Regime</div>
            <div class="value {{ vol_regime }}">{{ vol_regime }}</div>
            <div class="sub">ATR pct: {{ atr_pct }}</div>
        </div>
        <div class="card">
            <div class="label">VWAP Deviation</div>
            <div class="value">{{ vwap_dev }}%</div>
        </div>
    </div>

    {% if letta_census %}
    <h2>LETTA AGENTS</h2>
    {% set census_bg = {'green': '#00ff8811', 'amber': '#ffaa0022', 'red': '#ff000022'}[letta_census.color] %}
    {% set census_bd = {'green': '#00ff8866', 'amber': '#ffaa0088', 'red': '#ff444488'}[letta_census.color] %}
    {% set census_fg = {'green': '#00ff88', 'amber': '#ffaa00', 'red': '#ff4444'}[letta_census.color] %}
    <div class="card" style="background:{{ census_bg }}; border:1px solid {{ census_bd }};">
      <div class="label">Active agents on Letta API key</div>
      <div class="value" style="color:{{ census_fg }};">
        {{ letta_census.total }} total
      </div>
      <div class="sub">
        {{ letta_census.eval_count }} eval / {{ letta_census.prod_count }} production
        {% if letta_census.color == 'red' %}
          <br><span style="color:#ff4444; font-weight:bold;">RUN cleanup_eval_agents.py IMMEDIATELY</span>
        {% endif %}
        {% if letta_census.error %}
          <br><span style="color:#888;">{{ letta_census.error }}</span>
        {% endif %}
      </div>
    </div>
    {% endif %}

    {% if eval_history and eval_history.has_any_runs %}

    <h2>Shadow Grid Variants</h2>
    {% if shadow_variants %}
    <table>
        <tr>
            <th>Levels</th>
            <th>Spacing</th>
            <th>Expected PnL/trip</th>
            <th>Fills (24h)</th>
            <th>Rolling P&amp;L%</th>
            <th>Status</th>
        </tr>
        {% for sv in shadow_variants %}
        <tr>
            <td style="{{ 'color:#00ff88; font-weight:bold;' if sv.level_count == active_levels else '' }}">
                {{ sv.level_count }}{{ ' ★' if sv.level_count == active_levels else '' }}
            </td>
            <td>{{ '%.2f'|format((sv.spacing_pct or 0) * 100) }}%</td>
            <td style="{{ 'color:#00ff88;' if (sv.expected_pnl_pct or 0) > 0 else 'color:#ff4444;' }}">
                {{ '%.4f'|format((sv.expected_pnl_pct or 0) * 100) }}%
            </td>
            <td>{{ sv.fill_count }}</td>
            <td style="{{ 'color:#00ff88;' if (sv.rolling_pnl_pct or 0) >= 0 else 'color:#ff4444;' }}">
                {{ '%.4f'|format(sv.rolling_pnl_pct or 0) }}%
            </td>
            <td style="{{ 'color:#00ff88;' if sv.level_count == active_levels else 'color:#666;' }}">
                {{ 'ACTIVE' if sv.level_count == active_levels else 'shadow' }}
            </td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <div style="color:#666; font-size:0.8em;">Shadow simulation not yet initialised — starts after first observer cycle.</div>
    {% endif %}

    <details style="margin-bottom:10px;" id="d-costs">
    <summary style="cursor:pointer; color:#88aaff; font-size:0.9em;">▸ Costs (click to expand)</summary>
    <h2>Costs</h2>

    <div class="grid">
        <div class="card">
            <div class="label">Today's LLM Spend</div>
            <div class="value">${{ cost_today }}</div>
            <div class="sub">{{ calls_today }} calls / {{ tokens_today }} tokens</div>
        </div>
        <div class="card">
            <div class="label">LLM — 30d Actual</div>
            <div class="value">${{ cost_30d }}</div>
            <div class="sub">
                Projected this month: ${{ '%.2f'|format(llm_monthly_projected) }}
                {% if llm_over_budget %}
                <span style="color:#ff4444;"> ⚠ over ${{ llm_monthly_budget }} budget</span>
                {% endif %}
            </div>
        </div>
        <div class="card">
            <div class="label">DigitalOcean — MTD</div>
            <div class="value">
                ${{ '%.2f'|format(do_mtd) }}
                {% if do_error %}
                <span style="color:#ffaa00; font-size:0.6em;" title="{{ do_error }}"> ⚠ est</span>
                {% endif %}
            </div>
            <div class="sub">
                {% if do_balance is not none %}
                Acct balance: ${{ '%.2f'|format(do_balance) }}
                {% else %}
                Live data unavailable
                {% endif %}
            </div>
        </div>
        <div class="card">
            <div class="label">Total Projected Monthly</div>
            <div class="value {{ 'pnl-neg' if total_projected > llm_monthly_budget + 6 else '' }}">
                ${{ '%.2f'|format(total_projected) }}
            </div>
            <div class="sub">LLM proj + DO MTD</div>
        </div>
    </div>

    <table>
        <tr>
            <th>Agent</th>
            <th>Model</th>
            <th>Calls (30d)</th>
            <th>Tokens (30d)</th>
            <th>Cost (30d)</th>
            <th>Daily Rate</th>
            <th>Credit Left</th>
            <th>Runway</th>
        </tr>
        {% for r in agent_runway %}
        <tr>
            <td>{{ r.agent }}</td>
            <td style="color:#888;">{{ r.model }}</td>
            <td>{{ r.calls }}</td>
            <td>{{ r.tokens }}</td>
            <td>${{ '%.4f'|format(r.cost_30d) }}</td>
            <td>${{ '%.4f'|format(r.daily_rate) }}</td>
            <td>${{ '%.2f'|format(r.credit) }}</td>
            <td style="color:{{ '#ff4444' if r.runway_days < 30 else ('#ffaa00' if r.runway_days < 90 else '#00ff88') }}">
                {% if r.runway_days >= 9999 %}—{% else %}{{ r.runway_days }}d{% endif %}
            </td>
        </tr>
        {% endfor %}
    </table>
    </details>

    <details style="margin-bottom:10px;" id="d-council-accuracy">
    <summary style="cursor:pointer; color:#88aaff; font-size:0.9em;">▸ Council Accuracy (click to expand)</summary>
    <!-- ── Phase 5 PANEL 2: Accuracy Tracker ─────────────────────── -->
    <h2>Council Accuracy</h2>
    <div class="accuracy-grid">
        {% for agent in ['casper', 'melchior', 'balthasar'] %}
        {% set a = council_accuracy[agent] %}
        <div class="accuracy-card">
            <div style="color:#88cc88; font-size:0.78em; letter-spacing:2px;
                        text-transform:uppercase; margin-bottom:6px;">
                {{ agent }}
            </div>
            <div class="accuracy-line">
                7d accuracy: <span class="num">{{ a.acc_7d.accuracy_pct }}%</span>
                <span style="color:#666;">({{ a.acc_7d.positive_outcomes }}/{{ a.acc_7d.total_calls }})</span>
            </div>
            <div class="accuracy-line">
                30d accuracy: <span class="num">{{ a.acc_30d.accuracy_pct }}%</span>
                <span style="color:#666;">({{ a.acc_30d.positive_outcomes }}/{{ a.acc_30d.total_calls }})</span>
            </div>
            <div class="accuracy-line">
                30d capitulation:
                <span class="num {{ 'bad' if a.capit_30d.invalid_revisions > 0 else '' }}">
                    {{ a.capit_30d.invalid_revisions }}/{{ a.capit_30d.total_revisions }}
                </span>
                <span style="color:#666;">({{ a.capit_30d.capitulation_pct }}%)</span>
            </div>
            <div style="margin-top:10px;">
                <div style="color:#666; font-size:0.7em; margin-bottom:4px;">conviction (last 30 cycles)</div>
                {{ conviction_sparklines_svg[agent]|safe }}
            </div>
        </div>
        {% endfor %}
    </div>
    </details>

    <details style="margin-bottom:10px;" id="d-council-evolution">
    <summary style="cursor:pointer; color:#88aaff; font-size:0.9em;">▸ Council Evolution (30d) (click to expand)</summary>
    <!-- ── Phase 5 PANEL 4: Evolution ────────────────────────────── -->
    <h2>Council Evolution (30d)</h2>
    <div class="evolution-grid">
        <div class="evo-card">
            <div class="evo-title">Daily agreement rate (% no-debate)</div>
            {{ evolution_agreement_svg|safe }}
            <div style="color:#666; font-size:0.7em; margin-top:4px;">
                {{ evolution_agreement|length }} day{{ '' if evolution_agreement|length == 1 else 's' }} of data
            </div>
        </div>
        <div class="evo-card">
            <div class="evo-title">Average conviction per agent (daily)</div>
            {{ evolution_convictions_svg|safe }}
            <div style="font-size:0.7em; margin-top:6px;">
                <span style="color:#66ccff;">■</span> casper
                &nbsp;<span style="color:#ffcc66;">■</span> melchior
                &nbsp;<span style="color:#ff88aa;">■</span> balthasar
            </div>
        </div>
        <div class="evo-card" style="grid-column:1 / span 2;">
            <div class="evo-title">Hard-rule override counts (30d)</div>
            {% if evolution_overrides %}
                {{ evolution_overrides_svg|safe }}
            {% else %}
                <div style="color:#666; font-size:0.8em;">No overrides triggered in last 30d.</div>
            {% endif %}
        </div>
    </div>
    </details>

    <details style="margin-bottom:10px;" id="d-outcome-attribution">
    <summary style="cursor:pointer; color:#88aaff; font-size:0.9em;">▸ Outcome Attribution (30d) (click to expand)</summary>
    <!-- ── Phase 5 PANEL 5: Outcome Attribution ──────────────────── -->
    <h2>Outcome Attribution (30d)</h2>
    <div class="attribution-grid">
        <div class="attribution-card">
            <div class="evo-title" style="color:#00ff88;">Best 5 by 24h P&amp;L</div>
            {% if attribution_best %}
            <table>
                <tr><th>Time</th><th>Grid</th><th>C/M/B r0</th><th>Fills 24h</th><th>P&amp;L 24h</th></tr>
                {% for r in attribution_best %}
                <tr>
                    <td style="color:#888;">{{ r.timestamp[:16] }}</td>
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
                    <td style="color:#888;">{{ r.timestamp[:16] }}</td>
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
        <div class="evo-title">Fill rate &amp; P&amp;L by grid_action (30d)</div>
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

    <details style="margin-bottom:10px;" id="d-debate-log">
    <summary style="cursor:pointer; color:#88aaff; font-size:0.9em;">▸ Debate Log (click to expand)</summary>
    <!-- ── Phase 5 PANEL 3: Debate Log ───────────────────────────── -->
    <h2>Debate Log</h2>
    <details>
        <summary style="cursor:pointer; color:#88aaff;">
            Last {{ debate_log_rows|length }} triggered debates (click to expand)
        </summary>
        {% if debate_log_rows %}
        <table style="margin-top:10px;">
            <tr>
                <th>Time</th>
                <th>Pair</th>
                <th>Casper held</th>
                <th>Melchior held</th>
                <th>Balthasar held</th>
                <th>Any valid revision</th>
                <th>Grid</th>
                <th>Deadlock</th>
                <th>Fills 6h</th>
            </tr>
            {% for d in debate_log_rows %}
            <tr>
                <td colspan="9" style="padding:0;">
                <details class="debate-row">
                    <summary>
                        <table style="margin:0;"><tr>
                            <td style="width:14%; color:#888;">{{ d.timestamp[:19] }}</td>
                            <td style="width:14%; color:#ffaa00;">{{ d.conflict_pair or '—' }}</td>
                            <td style="width:9%;">{% if d.casper_r1_held    is none %}—{% elif d.casper_r1_held    %}HELD{% else %}revised{% endif %}</td>
                            <td style="width:9%;">{% if d.melchior_r1_held  is none %}—{% elif d.melchior_r1_held  %}HELD{% else %}revised{% endif %}</td>
                            <td style="width:9%;">{% if d.balthasar_r1_held is none %}—{% elif d.balthasar_r1_held %}HELD{% else %}revised{% endif %}</td>
                            <td style="width:11%; color:{{ '#00ff88' if d.any_revision_valid else '#888' }};">
                                {{ 'yes' if d.any_revision_valid else 'no' }}
                            </td>
                            <td style="width:10%;" class="{{ d.final_grid_action }}">{{ d.final_grid_action }}</td>
                            <td style="width:10%;" class="{{ 'HALT' if d.deadlock else '' }}">{{ 'YES' if d.deadlock else '—' }}</td>
                            <td style="width:8%;">{{ d.fills_6h if d.fills_6h is not none else '—' }}</td>
                        </tr></table>
                    </summary>
                    <div style="background:#0c0c14; padding:10px 14px; margin:4px 0; border:1px dashed #444466;">
                        {% for ag in ['casper', 'melchior', 'balthasar'] %}
                        <div style="margin-bottom:8px;">
                            <span style="color:#88aaff; letter-spacing:2px; font-size:0.75em;">{{ ag|upper }}</span>
                            {% set ev = d[ag + '_r0_evidence_list'] or [] %}
                            {% if ev %}<ul style="margin:4px 0 4px 18px; color:#aaaacc; font-size:0.8em;">
                                {% for e in ev %}<li>{{ e }}</li>{% endfor %}
                            </ul>{% endif %}
                            {% set r1 = d[ag + '_r1_text'] %}
                            {% if r1 %}<div style="color:#ccccdd; font-size:0.78em; font-style:italic;
                                margin:4px 0 0 18px;">r1: "{{ r1 }}"</div>{% endif %}
                        </div>
                        {% endfor %}
                    </div>
                </details>
                </td>
            </tr>
            {% endfor %}
        </table>
        {% else %}
        <div style="color:#666; margin-top:10px;">No triggered debates yet.</div>
        {% endif %}
    </details>
    </details>

    <details style="margin-bottom:10px;" id="d-eval-history">
    <summary style="cursor:pointer; color:#88aaff; font-size:0.9em;">▸ Eval History (click to expand)</summary>
    <h2>EVAL HISTORY</h2>
    <div class="grid">
      {% for ag in eval_history.agents %}
        {% if ag.latest_accuracy is not none %}
        {% set bg = {'green': '#00ff8811', 'amber': '#ffaa0022', 'red': '#ff000022', 'gray': '#88888811'}[ag.panel_color] %}
        {% set bd = {'green': '#00ff8866', 'amber': '#ffaa0088', 'red': '#ff444488', 'gray': '#888888'}[ag.panel_color] %}
        {% set fg = {'green': '#00ff88', 'amber': '#ffaa00', 'red': '#ff4444', 'gray': '#aaaaaa'}[ag.panel_color] %}
        <div class="card" style="background:{{ bg }}; border:1px solid {{ bd }};">
          <div class="label">{{ ag.agent_id|upper }} EVAL</div>
          <div class="value" style="color:{{ fg }};">
            {{ '%.0f' % (ag.latest_accuracy * 100) }}%
            <span style="font-size:12px; color:#aaaaaa;">
              gate {{ '%.0f' % (ag.gate_threshold * 100) }}%
            </span>
          </div>
          <div class="sub">
            {{ 'PASS' if ag.latest_passed else 'FAIL' }}
            {% if ag.consec_fails >= 2 %} · {{ ag.consec_fails }}× fail{% endif %}
            <a href="/evals/{{ ag.agent_id }}" style="color:#66ccff; margin-left:8px;">[detail]</a>
          </div>
          {% if ag.sparkline %}
          <div style="margin-top:6px;">{{ ag.sparkline|safe }}</div>
          {% endif %}
          <div class="sub" style="font-size:10px; color:#888; margin-top:4px;">
            {% for r in ag.last_n %}
              <span style="color:{{ '#00ff88' if r.passed else '#ff4444' }};">●</span>
            {% endfor %}
            ({{ ag.last_n|length }} runs)
          </div>
        </div>
        {% else %}
        <div class="card" style="background:#88888811; border:1px solid #888888;">
          <div class="label">{{ ag.agent_id|upper }} EVAL</div>
          <div class="value" style="color:#888;">—</div>
          <div class="sub">no runs yet</div>
        </div>
        {% endif %}
      {% endfor %}
    </div>
    {% endif %}
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
        '.header-row',          // top status bar (time / scheduler / age / next)
        '.agent-health-panel',  // BYOK contingency Dim 1 — degradation chips + GATE MON + COUNCIL LEVERS
        '.magi-hero',           // hero (agent triangle + CODE box)
        '.inv-pnl-row',         // Inventory + Paper P&L pair
        '.readiness-panel',     // Live readiness gates
        // Other panels (Recent Orders, Market, LETTA AGENTS, Shadow,
        // collapsed analytics) update infrequently or only on user
        // expand. Add more selectors here if their data lag matters.
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


def _svg_multiline(series_dict, w=560, h=140,
                   colors=None, y_min=0.0, y_max=1.0):
    """Multi-series line chart on a 0..1 y-axis (default). Returns SVG."""
    colors = colors or {'casper': '#66ccff', 'melchior': '#ffcc66',
                        'balthasar': '#ff88aa'}
    if not series_dict or not any(len(v) > 0 for v in series_dict.values()):
        return '<div style="color:#666; font-size:0.8em;">No data yet.</div>'
    n = max(len(v) for v in series_dict.values())
    if n < 2:
        return '<div style="color:#666; font-size:0.8em;">Need ≥2 data points.</div>'
    span = (y_max - y_min) or 1
    pad = 4
    inner_w = w - 2 * pad
    inner_h = h - 2 * pad
    parts = [f'<svg width="{w}" height="{h}" style="background:#08080f;">']
    # y-axis baseline
    parts.append(f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" '
                 f'stroke="#222" stroke-width="1"/>')
    for label, series in series_dict.items():
        if not series:
            continue
        pts = []
        for i, v in enumerate(series):
            x = pad + i * (inner_w / (n - 1))
            y = pad + inner_h - ((float(v) - y_min) / span) * inner_h
            pts.append(f"{x:.1f},{y:.1f}")
        parts.append(f'<polyline points="{" ".join(pts)}" '
                     f'fill="none" stroke="{colors.get(label, "#888")}" '
                     f'stroke-width="1.5"/>')
    parts.append('</svg>')
    return ''.join(parts)


def _svg_agreement(series, w=560, h=140, color='#88cc88'):
    """Single-series area + line for agreement rate 0..100."""
    if not series:
        return '<div style="color:#666; font-size:0.8em;">No data yet.</div>'
    values = [s.get('rate', 0) for s in series]
    return _svg_multiline({'rate': values}, w=w, h=h,
                          colors={'rate': color}, y_min=0.0, y_max=100.0)


def _svg_bars(items, w=540, h=120, color='#ffaa00'):
    """Horizontal bar chart for (tag, count) tuples. Items sorted by caller."""
    if not items:
        return '<div style="color:#666; font-size:0.8em;">No overrides.</div>'
    n = len(items)
    bar_h = max(10, min(22, (h - 8) // n - 4))
    label_w = 160
    total_h = (bar_h + 6) * n + 8
    max_v = max((c for _, c in items), default=1) or 1
    parts = [f'<svg width="{w}" height="{total_h}" style="background:#08080f;">']
    for i, (tag, count) in enumerate(items):
        y = 4 + i * (bar_h + 6)
        bar_w = int((count / max_v) * (w - label_w - 50))
        parts.append(f'<text x="0" y="{y + bar_h - 3}" fill="#ccc" '
                     f'font-size="11">{tag}</text>')
        parts.append(f'<rect x="{label_w}" y="{y}" width="{bar_w}" '
                     f'height="{bar_h}" fill="{color}" opacity="0.85"/>')
        parts.append(f'<text x="{label_w + bar_w + 6}" y="{y + bar_h - 3}" '
                     f'fill="#ffcc66" font-size="11">{count}</text>')
    parts.append('</svg>')
    return ''.join(parts)


_MAGI_HOURS_EST = [0, 4, 8, 12, 16, 20]
# Hardcoded duplicate of scheduler.py:MAGI_HOURS_EST — scheduler.py's
# module-level signal.signal() call fails when imported from a Flask
# worker thread. Update both places if the schedule changes.


def _next_magi_eta():
    """Return {label, countdown_min} for the next scheduled MAGI cycle,
    or None on failure. Hardcoded to match scheduler.py."""
    try:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
    except Exception:
        return None
    now_est = datetime.now(ZoneInfo('America/New_York'))
    slots = sorted(_MAGI_HOURS_EST)
    next_dt = None
    for h in slots:
        if h > now_est.hour:
            next_dt = now_est.replace(hour=h, minute=0, second=0, microsecond=0)
            break
    if next_dt is None:
        next_dt = (now_est + timedelta(days=1)).replace(
            hour=slots[0], minute=0, second=0, microsecond=0)
    delta_min = int((next_dt - now_est).total_seconds() / 60)
    hh, mm = divmod(delta_min, 60)
    if hh > 0:
        label = f"{next_dt.hour:02d}:00 EST · in {hh}h {mm:02d}m"
    else:
        label = f"{next_dt.hour:02d}:00 EST · in {mm}m"
    return {'label': label, 'countdown_min': delta_min}


def _fetch_council_data():
    """
    Single-call fetcher for all five council panels. Returns a dict that the
    index() route can splat into render_template_string. All values are
    JSON-serialisable (no SQLite Row objects).
    """
    import json as _json
    import re as _re
    from datetime import datetime as _dt, timedelta as _td
    from database import (
        get_conn, get_recent_debate_records,
        get_agent_accuracy, get_capitulation_rate,
    )

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

    # Per-agent accuracy + capitulation cards
    council_accuracy = {}
    for a in ('casper', 'melchior', 'balthasar'):
        council_accuracy[a] = {
            'acc_7d':    get_agent_accuracy(a, 7),
            'acc_30d':   get_agent_accuracy(a, 30),
            'capit_30d': get_capitulation_rate(a, 30),
        }

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

    # Debate log — last 30 triggered debates
    debate_rows = conn.execute(
        "SELECT cycle_id, timestamp, conflict_pair, "
        "casper_r1_held, melchior_r1_held, balthasar_r1_held, "
        "casper_revision_valid, melchior_revision_valid, balthasar_revision_valid, "
        "casper_r1_text, melchior_r1_text, balthasar_r1_text, "
        "casper_r0_evidence, melchior_r0_evidence, balthasar_r0_evidence, "
        "final_grid_action, deadlock, fills_6h "
        "FROM debate_records WHERE debate_triggered=1 "
        "ORDER BY id DESC LIMIT 30"
    ).fetchall()
    debate_log = []
    for r in debate_rows:
        d = dict(r)
        for a in ('casper', 'melchior', 'balthasar'):
            ev = d.get(f'{a}_r0_evidence')
            try:
                d[f'{a}_r0_evidence_list'] = _json.loads(ev) if ev else []
            except (ValueError, TypeError):
                d[f'{a}_r0_evidence_list'] = []
        d['any_revision_valid'] = (
            d.get('casper_revision_valid') == 1
            or d.get('melchior_revision_valid') == 1
            or d.get('balthasar_revision_valid') == 1
        )
        debate_log.append(d)

    cutoff = (_dt.utcnow() - _td(days=30)).isoformat()

    # Daily agreement rate
    agreement_rows = conn.execute(
        "SELECT DATE(timestamp) AS d, "
        "SUM(CASE WHEN debate_triggered=0 THEN 1 ELSE 0 END) AS agree, "
        "COUNT(*) AS total "
        "FROM debate_records WHERE timestamp >= ? "
        "GROUP BY DATE(timestamp) ORDER BY d ASC",
        (cutoff,)
    ).fetchall()
    agreement_series = [
        {'date': r['d'], 'rate': round((r['agree'] / r['total'] * 100), 2) if r['total'] else 0.0}
        for r in agreement_rows
    ]

    # Daily avg conviction per agent
    conv_rows = conn.execute(
        "SELECT DATE(timestamp) AS d, "
        "AVG(casper_r0_conviction) AS c, "
        "AVG(melchior_r0_conviction) AS m, "
        "AVG(balthasar_r0_conviction) AS b "
        "FROM debate_records WHERE timestamp >= ? "
        "GROUP BY DATE(timestamp) ORDER BY d ASC",
        (cutoff,)
    ).fetchall()
    conviction_series = {
        'dates':     [r['d'] for r in conv_rows],
        'casper':    [float(r['c'] or 0) for r in conv_rows],
        'melchior':  [float(r['m'] or 0) for r in conv_rows],
        'balthasar': [float(r['b'] or 0) for r in conv_rows],
    }

    # Hard-rule overrides — read the JSON-encoded list directly from
    # debate_records. Pre-migration rows (NULL) contribute 0; their counts
    # remain in magi_decisions.notes and age out of the 30d window naturally.
    override_rows = conn.execute(
        "SELECT hard_rule_overrides FROM debate_records "
        "WHERE timestamp >= ? AND hard_rule_overrides IS NOT NULL",
        (cutoff,)
    ).fetchall()
    tag_counts = {}
    for r in override_rows:
        raw = r['hard_rule_overrides']
        try:
            tags = _json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        for t in tags or []:
            tag = t.strip('[]') if isinstance(t, str) else None
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
    override_counts = sorted(tag_counts.items(), key=lambda kv: kv[1], reverse=True)

    # Outcome attribution
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

    # Convert convictions 0..1 → 0..100% so the multiline chart shares a y-axis
    conv_for_chart = {k: [v * 100.0 for v in conviction_series[k]]
                      for k in ('casper', 'melchior', 'balthasar')}

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
        'council_override_tags':  override_tags,
        'council_accuracy':       council_accuracy,
        'conviction_sparklines_svg': sparklines_svg,
        'debate_log_rows':        debate_log,
        'evolution_agreement':    agreement_series,
        'evolution_agreement_svg': _svg_agreement(agreement_series, w=480, h=120),
        'evolution_convictions':  conviction_series,
        'evolution_convictions_svg': _svg_multiline(
            conv_for_chart, w=480, h=120, y_min=0.0, y_max=100.0
        ),
        'evolution_overrides':    override_counts,
        'evolution_overrides_svg': _svg_bars(override_counts, w=960, h=200),
        'attribution_best':       [dict(r) for r in best_rows],
        'attribution_worst':      [dict(r) for r in worst_rows],
        'action_summary':         [dict(r) for r in summary_rows],
    }


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
    Per-agent degradation health computed from the last 3 R0 rows per agent
    in debate_records.

    Safe-default fingerprint: conviction == 0.0 AND crux LIKE '(no response)%'.
    Same gate magi/council.py uses in SAFE_DEFAULTS — anything matching it is
    a parse-failure / model-degradation event, not a real response.

    Returns:
      {agent_id: {status, degraded_count, total, model}}

    Status thresholds:
      0 of 3 degraded → 'green'
      1 of 3 degraded → 'yellow'
      2 or 3 degraded → 'red'

    Empty / missing data → status='green' (no signal yet, don't false-alarm).
    """
    from database import get_conn
    out = {}
    conn = get_conn()
    try:
        registry_rows = conn.execute(
            "SELECT agent_id, model FROM agent_registry"
        ).fetchall()
        model_by_agent = {r['agent_id']: (r['model'] or '') for r in registry_rows}

        for agent in _AGENT_HEALTH_ORDER:
            rows = conn.execute(
                f"SELECT {agent}_r0_conviction AS conv, "
                f"       {agent}_r0_crux AS crux "
                f"FROM debate_records ORDER BY id DESC LIMIT 3"
            ).fetchall()
            degraded = 0
            total = len(rows)
            for r in rows:
                conv = r['conv']
                crux = r['crux'] or ''
                # Tolerate a tiny float epsilon on conviction
                conv_zero = (conv is None) or (abs(float(conv)) < 1e-9)
                if conv_zero and crux.startswith('(no response)'):
                    degraded += 1
            if degraded == 0:
                status = 'green'
            elif degraded == 1:
                status = 'yellow'
            else:
                status = 'red'
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


_do_billing_cache = {'data': None, 'expires': 0}


def get_do_billing():
    """Fetch DO month-to-date usage and account balance via DO API.
    Returns dict with keys: mtd_usage, account_balance, error.
    Times out after 3 seconds to avoid blocking dashboard render."""
    import time
    global _do_billing_cache
    if _do_billing_cache['data'] and time.time() < _do_billing_cache['expires']:
        return _do_billing_cache['data']
    from config import DO_API_TOKEN, DO_DROPLET_MONTHLY_USD
    if not DO_API_TOKEN:
        result = {
            'mtd_usage': DO_DROPLET_MONTHLY_USD,
            'account_balance': None,
            'error': 'DO_API_TOKEN not set — using hardcoded fallback'
        }
        _do_billing_cache = {'data': result, 'expires': time.time() + 300}
        return result
    try:
        import urllib.request
        import json
        import socket
        socket.setdefaulttimeout(3)
        req = urllib.request.Request(
            'https://api.digitalocean.com/v2/customers/my/balance',
            headers={
                'Authorization': f'Bearer {DO_API_TOKEN}',
                'Content-Type': 'application/json'
            }
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
        result = {
            'mtd_usage': float(data.get('month_to_date_usage', DO_DROPLET_MONTHLY_USD)),
            'account_balance': float(data.get('account_balance', 0)),
            'month_to_date_balance': float(data.get('month_to_date_balance', 0)),
            'error': None
        }
        _do_billing_cache = {'data': result, 'expires': time.time() + 300}
        return result
    except Exception as e:
        result = {
            'mtd_usage': DO_DROPLET_MONTHLY_USD,
            'account_balance': None,
            'month_to_date_balance': None,
            'error': str(e)
        }
        _do_billing_cache = {'data': result, 'expires': time.time() + 300}
        return result


@app.route('/')
def index():
    from zoneinfo import ZoneInfo
    EST = ZoneInfo('America/New_York')
    now = datetime.now(timezone.utc).astimezone(EST).strftime('%Y-%m-%d %H:%M EST')

    indicators = get_latest_indicators('1h') or {}
    grid = get_current_grid_state() or {}
    inventory = get_latest_inventory() or {}
    price = engine.get_current_price() or 0.0

    cost_today_data = get_cost_today()
    cost_30d_data = get_cost_summary(days_back=30)
    total_cost_30d = sum((c.get('cost') or 0) for c in cost_30d_data)
    fixed_breakdown = ', '.join(f"{k}: ${v}" for k, v in FIXED_SUBSCRIPTIONS.items() if v > 0)

    from config import (LLM_MONTHLY_BUDGET_USD,
                        ANTHROPIC_CREDIT_REMAINING,
                        OPENAI_CREDIT_REMAINING,
                        GOOGLE_CREDIT_REMAINING)
    from datetime import date as _date

    do_billing = get_do_billing()
    do_mtd = do_billing['mtd_usage']
    do_balance = do_billing.get('account_balance')
    do_error = do_billing.get('error')

    day_of_month = _date.today().day
    days_in_month = 30
    daily_llm_rate = total_cost_30d / 30 if total_cost_30d else 0
    llm_monthly_projected = daily_llm_rate * days_in_month
    total_projected = llm_monthly_projected + do_mtd
    llm_over_budget = llm_monthly_projected > LLM_MONTHLY_BUDGET_USD

    credit_map = {
        'balthasar': ANTHROPIC_CREDIT_REMAINING,
        'melchior':  OPENAI_CREDIT_REMAINING,
        'casper':    GOOGLE_CREDIT_REMAINING,
    }
    agent_runway = []
    for agent in cost_30d_data:
        name = agent.get('agent', '')
        cost_30d_val = agent.get('cost') or 0
        daily_rate = cost_30d_val / 30
        credit = credit_map.get(name, 0)
        if daily_rate > 0:
            runway_days = int(credit / daily_rate)
        else:
            runway_days = 9999
        agent_runway.append({
            'agent':       name,
            'model':       agent.get('model', ''),
            'calls':       agent.get('calls', 0),
            'tokens':      agent.get('total_tokens', 0),
            'cost_30d':    cost_30d_val,
            'daily_rate':  daily_rate,
            'credit':      credit,
            'runway_days': runway_days,
        })

    guardrails_ok, guardrail_failures = check_all_guardrails()
    ks_active = kill_switch_active()
    shadow_variants = get_all_shadow_states()

    # P&L snapshot
    snap = get_pnl_snapshot(price)
    best_shadow_level, best_shadow_spacing, best_shadow_pnl = get_best_shadow_from_db()
    live_pnl_pct = round(snap['total'] / MAX_INVENTORY_USD * 100, 4) if MAX_INVENTORY_USD > 0 else 0.0
    best_shadow_pnl = best_shadow_pnl or 0.0
    live_vs_shadow = round(live_pnl_pct - best_shadow_pnl, 4)

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
        cost_today=f"{cost_today_data.get('cost') or 0:.4f}",
        calls_today=cost_today_data.get('calls') or 0,
        tokens_today=cost_today_data.get('tokens') or 0,
        cost_30d=f"{total_cost_30d:.4f}",
        cost_breakdown=cost_30d_data,
        fixed_monthly=f"{get_fixed_monthly_total():.2f}",
        fixed_breakdown=fixed_breakdown,
        do_mtd=do_mtd,
        do_balance=do_balance,
        do_error=do_error,
        llm_monthly_projected=round(llm_monthly_projected, 4),
        total_projected=round(total_projected, 4),
        llm_over_budget=llm_over_budget,
        llm_monthly_budget=LLM_MONTHLY_BUDGET_USD,
        agent_runway=agent_runway,
        scheduler_alive=check_scheduler_alive(),
        guardrails_ok=guardrails_ok,
        guardrail_failures=guardrail_failures,
        kill_switch=ks_active,
        paper_mode=engine.paper,
        shadow_variants=shadow_variants,
        active_levels=engine.level_count,
        # P&L tiles
        pnl_realized=snap['realized'],
        pnl_realized_fmt=f"{snap['realized']:.4f}",
        pnl_unrealized=snap['unrealized'],
        pnl_unrealized_fmt=f"{snap['unrealized']:.4f}",
        pnl_total=snap['total'],
        pnl_total_fmt=f"{snap['total']:.4f}",
        pnl_fees_fmt=f"{snap['fees']:.4f}",
        pnl_fill_count=snap['fill_count'],
        pnl_matched_trips=snap['matched_round_trips'],
        pnl_unmatched_buys=snap['unmatched_buys'],
        pnl_win_rate=snap['win_rate'],
        pnl_avg_per_trip=snap['avg_pnl_per_round_trip'],
        pnl_fills_today=snap['fills_today'],
        pnl_mins_since=snap['time_since_last_fill_minutes'],
        fill_age_label=fill_age_label,
        fill_age_color=fill_age_color,
        fill_stale=fill_stale,
        live_pnl_pct_fmt=f"{live_pnl_pct:.4f}",
        best_shadow_level=best_shadow_level,
        best_shadow_pnl_fmt=f"{best_shadow_pnl:.4f}",
        live_vs_shadow=live_vs_shadow,
        live_vs_shadow_fmt=f"{live_vs_shadow:+.4f}",
        # Recent orders table
        recent_orders=recent_orders,
        order_pnl_map=snap['order_pnl_map'],
        magi_token=os.environ.get('MAGI_TRIGGER_TOKEN', ''),
        open_alerts=_get_open_alerts_safe(),
        eval_history=_fetch_eval_history(),
        letta_census=_fetch_letta_agent_census(),
        next_magi=_next_magi_eta(),
        agent_health=_fetch_agent_health(),
        council_levers=_fetch_council_levers_safe(),
        gate_monitor=_fetch_gate_monitor_safe(),
        readiness=_fetch_readiness_safe(),
        gate_activity=_fetch_gate_activity_safe(),
        # Phase 5: agent council panels (single splat from helper)
        **_fetch_council_data(),
    )


_LETTA_CENSUS_CACHE = {'ts': 0.0, 'data': None}


def _fetch_letta_agent_census():
    """Count Letta agents per dashboard render. Cached 60s to avoid
    hammering the SDK. Returns dict with total/eval/prod counts and a
    color band, or None on error (template hides the widget).

    FIX D, 2026-05-18 audit — visibility layer that should have surfaced
    the 102-agent leak as it accumulated.
    """
    import time as _time
    now = _time.time()
    cached = _LETTA_CENSUS_CACHE.get('data')
    if cached is not None and (now - _LETTA_CENSUS_CACHE['ts']) < 60:
        return cached

    try:
        import os, re, sqlite3
        from dotenv import load_dotenv
        load_dotenv('/root/xrp_grid/.env')
        from letta_client import Letta as _Letta

        eval_re = re.compile(
            r"^eval-(casper|melchior|balthasar)-[a-zA-Z0-9_-]+?-\d+-\d+$"
        )
        c = _Letta(api_key=os.environ['LETTA_API_KEY'])

        conn = sqlite3.connect('/root/xrp_grid/observer.db')
        prod_ids = set(r[0] for r in conn.execute(
            "SELECT letta_agent_id FROM agent_registry "
            "WHERE letta_agent_id IS NOT NULL"))
        conn.close()

        all_agents = []
        after = None
        while True:
            page = list(c.agents.list(
                limit=100, **({"after": after} if after else {})
            ))
            if not page:
                break
            all_agents.extend(page)
            if len(page) < 100:
                break
            after = page[-1].id

        prod_count = sum(1 for a in all_agents if a.id in prod_ids)
        eval_count = sum(1 for a in all_agents if eval_re.match(a.name or ""))
        total = len(all_agents)

        if eval_count > 50:
            color = 'red'
        elif eval_count > 20:
            color = 'amber'
        else:
            color = 'green'

        data = {
            'total': total,
            'prod_count': prod_count,
            'eval_count': eval_count,
            'color': color,
            'error': None,
        }
    except Exception as e:
        data = {
            'total': '?', 'prod_count': '?', 'eval_count': '?',
            'color': 'amber', 'error': f"census fetch failed: {type(e).__name__}",
        }

    _LETTA_CENSUS_CACHE['ts'] = now
    _LETTA_CENSUS_CACHE['data'] = data
    return data


def _fetch_eval_history():
    """Per-agent eval history for the EVAL HISTORY dashboard panel.

    Returns dict shape:
      {
        'has_any_runs': bool,
        'agents': [
          {'agent_id': 'casper',
           'latest_accuracy': 0.85, 'latest_passed': True,
           'gate_threshold': 0.70,
           'last_n': [{'ts': ..., 'accuracy': ..., 'passed': bool}, ...],
           'panel_color': 'green' | 'amber' | 'red',
           'consec_fails': int,
           'sparkline': '<svg>...</svg>',
          },
          ...
        ]
      }
    Empty/missing data → has_any_runs=False; template collapses the panel.
    """
    out = {'has_any_runs': False, 'agents': []}
    try:
        from database import get_recent_eval_runs
    except ImportError:
        return out
    any_runs = False
    for agent_id in ('casper', 'melchior', 'balthasar'):
        try:
            rows = get_recent_eval_runs(agent_id, limit=5)
        except Exception as e:
            app.logger.warning("get_recent_eval_runs(%s) failed: %r", agent_id, e)
            rows = []
        if not rows:
            out['agents'].append({
                'agent_id': agent_id, 'latest_accuracy': None,
                'latest_passed': None, 'gate_threshold': None,
                'last_n': [], 'panel_color': 'gray',
                'consec_fails': 0, 'sparkline': '',
            })
            continue
        any_runs = True
        latest = rows[0]
        consec_fails = 0
        for r in rows:
            if not r['gate_passed']:
                consec_fails += 1
            else:
                break
        if latest['gate_passed']:
            color = 'green'
        elif consec_fails >= 2:
            color = 'red'
        else:
            color = 'amber'
        last_n = [
            {
                'ts': r['timestamp'][:16].replace('T', ' '),
                'accuracy': float(r['accuracy']),
                'passed': bool(r['gate_passed']),
            }
            for r in rows
        ]
        # Sparkline of accuracy oldest→newest
        spark_vals = [r['accuracy'] for r in reversed(rows)]
        spark = _svg_sparkline(
            spark_vals, w=110, h=22,
            color={'green': '#00ff88', 'amber': '#ffaa00',
                   'red': '#ff4444'}.get(color, '#888'),
        ) if len(spark_vals) >= 2 else ''
        out['agents'].append({
            'agent_id': agent_id,
            'latest_accuracy': float(latest['accuracy']),
            'latest_passed': bool(latest['gate_passed']),
            'gate_threshold': float(latest['gate_threshold']),
            'last_n': last_n,
            'panel_color': color,
            'consec_fails': consec_fails,
            'sparkline': spark,
        })
    out['has_any_runs'] = any_runs
    return out


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
    snap = get_pnl_snapshot(price)
    best_lc, _best_sp, best_shadow_pnl = get_best_shadow_from_db()
    live_pnl_pct = round(snap['total'] / MAX_INVENTORY_USD * 100, 4) if MAX_INVENTORY_USD > 0 else 0.0
    live_minus_shadow = round(live_pnl_pct - (best_shadow_pnl or 0.0), 4)
    return jsonify({
        **snap,
        'best_shadow_level': best_lc,
        'best_shadow_pnl_pct': best_shadow_pnl,
        'live_pnl_pct': live_pnl_pct,
        'live_minus_shadow_pct': live_minus_shadow,
    })


@app.route('/api/recent_orders')
def api_recent_orders():
    limit = min(int(request.args.get('limit', 25)), 200)
    orders = get_recent_grid_orders(limit=limit)
    return jsonify({'orders': orders, 'count': len(orders)})


@app.route('/api/shadow_variants')
def api_shadow_variants():
    shadow_variants = get_all_shadow_states()
    return jsonify({
        'variants': shadow_variants,
        'active_levels': engine.level_count
    })


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


@app.route('/evals/<agent_id>')
def eval_detail(agent_id):
    """Per-agent eval-history detail page. Renders the most-recent run's
    per-sample results so the operator can see which scenarios failed and
    what the agent voted."""
    if agent_id not in ('casper', 'melchior', 'balthasar'):
        return ('Unknown agent', 404)
    from database import get_recent_eval_runs
    rows = get_recent_eval_runs(agent_id, limit=10)
    if not rows:
        return render_template_string(
            "<html><body style='background:#0a0a0a;color:#ccc;"
            "font-family:monospace;padding:24px;'>"
            "<h2>{{ a|upper }} — no eval runs yet</h2>"
            "<p>Run <code>/root/xrp_grid/evals/run_all.sh</code> to "
            "populate.</p><a href='/' style='color:#66ccff;'>← dashboard</a>"
            "</body></html>", a=agent_id)
    latest = rows[0]
    results_path = latest['raw_results_path'] or ''
    samples = []
    try:
        import json as _json
        rp = os.path.join(results_path, 'results.jsonl')
        if os.path.exists(rp):
            with open(rp) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    obj = _json.loads(line)
                    if obj.get('type') != 'result':
                        continue
                    r = obj.get('result') or {}
                    s = r.get('sample') or {}
                    grade = (r.get('grade') or {}).get('score')
                    if grade is None:
                        gs = r.get('grades') or []
                        if gs:
                            grade = gs[0].get('score')
                    md = s.get('metadata') or {}
                    samples.append({
                        'id': s.get('id'),
                        'ground_truth': s.get('ground_truth'),
                        'submission': r.get('submission'),
                        'passed': (grade or 0.0) >= 1.0,
                        'tags': s.get('tags') or [],
                        'rule_cited': md.get('persona_rule_cited'),
                        'source': md.get('source'),
                        'error': r.get('error'),
                    })
    except Exception as e:
        app.logger.warning("eval_detail read failed: %r", e)
    return render_template_string(EVAL_DETAIL_TEMPLATE,
                                  agent_id=agent_id, latest=latest,
                                  rows=rows, samples=samples)


EVAL_DETAIL_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<title>{{ agent_id|upper }} eval detail</title>
<style>
  body { background:#0a0a0a; color:#ccc; font-family:'Courier New',monospace;
         padding:24px; }
  h2 { color:#00ff88; }
  table { border-collapse:collapse; width:100%; margin-top:12px; }
  th, td { border:1px solid #333; padding:6px 10px; text-align:left;
           vertical-align:top; font-size:12px; }
  th { background:#222; color:#88cc88; }
  tr.pass td { background:#00ff8811; }
  tr.fail td { background:#ff000022; }
  .vote { font-weight:bold; }
  a { color:#66ccff; }
</style>
</head>
<body>
<a href="/">← dashboard</a>
<h2>{{ agent_id|upper }} — most recent eval run</h2>
<p>
  ts: <b>{{ latest.timestamp }}</b> ·
  suite: <code>{{ latest.suite_name }}</code> ·
  accuracy: <b style="color:{{ '#00ff88' if latest.gate_passed else '#ff4444' }};">
    {{ '%.3f' % latest.accuracy }}</b>
  ({{ latest.passed_samples }}/{{ latest.total_samples }}) ·
  gate: <b>{{ 'PASS' if latest.gate_passed else 'FAIL' }}</b> @ {{ '%.2f' % latest.gate_threshold }} ·
  cost: ${{ '%.4f' % (latest.cost_usd_estimate or 0) }} ·
  commit: {{ latest.git_commit_sha or '?' }}
</p>

<h3>Samples ({{ samples|length }})</h3>
<table>
  <tr>
    <th>id</th><th>tag</th><th>ground truth</th><th>vote</th>
    <th>passed</th><th>rule cited</th>
  </tr>
  {% for s in samples %}
  <tr class="{{ 'pass' if s.passed else 'fail' }}">
    <td>{{ s.id }}</td>
    <td>{{ s.tags|join(',') }}</td>
    <td class="vote">{{ s.ground_truth }}</td>
    <td class="vote">{{ s.submission or s.error or '(empty)' }}</td>
    <td>{{ 'PASS' if s.passed else 'FAIL' }}</td>
    <td style="max-width:480px;">{{ s.rule_cited or '' }}</td>
  </tr>
  {% endfor %}
</table>

<h3>Run history ({{ rows|length }})</h3>
<table>
  <tr><th>ts</th><th>accuracy</th><th>pass/total</th><th>gate</th><th>cost</th></tr>
  {% for r in rows %}
  <tr class="{{ 'pass' if r.gate_passed else 'fail' }}">
    <td>{{ r.timestamp }}</td>
    <td>{{ '%.3f' % r.accuracy }}</td>
    <td>{{ r.passed_samples }}/{{ r.total_samples }}</td>
    <td>{{ 'PASS' if r.gate_passed else 'FAIL' }}</td>
    <td>${{ '%.4f' % (r.cost_usd_estimate or 0) }}</td>
  </tr>
  {% endfor %}
</table>
</body>
</html>
"""


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


def _fetch_council_levers_safe():
    """Rolling distribution of the structural vote fields shipped on
    2026-05-22 (regime_action, geometry_veto). Counts only cycles
    from AFTER the post-recreation cutoff so the panel reflects the
    current agent generation, not historical cycles produced by
    different agents/personas/architectures.

    Returns shape consumed by the COUNCIL LEVERS chip panel and the
    /api/council_levers JSON endpoint. None on DB error.
    """
    POST_RECREATION_CUTOFF = '2026-05-22T16:00:00'
    try:
        from database import get_conn as _get_conn
        conn = _get_conn()
        rows = conn.execute(
            "SELECT regime_action, geometry_veto, "
            "       casper_r0_position, melchior_r0_position, "
            "       balthasar_r0_position, hard_rule_overrides "
            "FROM debate_records WHERE timestamp >= ? "
            "ORDER BY id DESC LIMIT 100",
            (POST_RECREATION_CUTOFF,),
        ).fetchall()
        conn.close()
    except Exception:
        return None

    n_total = len(rows)
    if n_total == 0:
        return {
            'n_cycles_post_recreation': 0,
            'cutoff': POST_RECREATION_CUTOFF,
            'regime_action': {},
            'geometry_veto': {},
            'veto_tag_counts': {},
            'cycles_with_council_downgrade': 0,
            'most_recent_window_n': 0,
        }
    import json as _j

    def _bucket(rows, key, defaults):
        counts = {d: 0 for d in defaults}
        counts['(missing)'] = 0
        for r in rows:
            v = r[key]
            if v in counts:
                counts[v] += 1
            elif v is None or v == '':
                counts['(missing)'] += 1
            else:
                counts[v] = counts.get(v, 0) + 1
        return counts

    regime_buckets = _bucket(
        rows, 'regime_action',
        ['EXECUTE', 'DEFER_STRUCTURAL', 'STAND_DOWN'],
    )
    veto_buckets = _bucket(
        rows, 'geometry_veto',
        ['PROCEED', 'HOLD_GEOMETRY', 'RISK_BLOCK'],
    )

    # Count cycles where a council veto tag actually fired (engine
    # downgrade attributable to the new structural vote fields)
    veto_tag_counts = {
        '[REGIME_DEFER]':           0,
        '[REGIME_STANDDOWN]':       0,
        '[BALTHASAR_HOLD_GEOMETRY]': 0,
        '[BALTHASAR_RISK_BLOCK]':   0,
    }
    cycles_with_downgrade = 0
    for r in rows:
        raw = r['hard_rule_overrides'] or '[]'
        try:
            tags = _j.loads(raw)
        except Exception:
            continue
        hit = False
        for tag in tags:
            if tag in veto_tag_counts:
                veto_tag_counts[tag] += 1
                hit = True
        if hit:
            cycles_with_downgrade += 1

    return {
        'n_cycles_post_recreation': n_total,
        'cutoff':                   POST_RECREATION_CUTOFF,
        'regime_action':            regime_buckets,
        'geometry_veto':            veto_buckets,
        'veto_tag_counts':          veto_tag_counts,
        'cycles_with_council_downgrade': cycles_with_downgrade,
        'most_recent_window_n':     min(n_total, 100),
    }


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


@app.route('/api/council_levers')
def api_council_levers():
    """Rolling distribution of regime_action / geometry_veto values
    across post-recreation cycles, plus how often each council-veto
    tag actually fired in hard_rule_overrides. Lets the operator see
    whether the new structural fields are being exercised at all."""
    data = _fetch_council_levers_safe()
    if data is None:
        return jsonify({'error': 'db read failed'})
    return jsonify(data)


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


@app.route('/api/council/evolution')
def api_council_evolution():
    """Time-series data for the evolution charts (30d). All JSON-serialisable."""
    data = _fetch_council_data()
    return jsonify({
        'agreement':         data['evolution_agreement'],
        'convictions':       data['evolution_convictions'],
        'overrides':         data['evolution_overrides'],
        'attribution_best':  data['attribution_best'],
        'attribution_worst': data['attribution_worst'],
        'action_summary':    data['action_summary'],
    })


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=False)

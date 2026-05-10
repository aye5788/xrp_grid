import sqlite3
import json
from datetime import datetime, date
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_conn()
    c = conn.cursor()

    # OHLCV candles from Coinbase
    c.execute('''CREATE TABLE IF NOT EXISTS candles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        UNIQUE(timestamp, timeframe)
    )''')

    # Computed technical indicators
    c.execute('''CREATE TABLE IF NOT EXISTS indicators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        ema_50 REAL, ema_200 REAL,
        adx REAL, adx_pos REAL, adx_neg REAL,
        roc_6h REAL,
        bb_width REAL, bb_upper REAL, bb_lower REAL,
        btc_ema_50 REAL, btc_ema_200 REAL,
        vwap REAL, vwap_dev_pct REAL,
        atr REAL, atr_percentile REAL,
        vol_regime TEXT,
        autocorr_1h REAL, autocorr_4h REAL,
        UNIQUE(timestamp, timeframe)
    )''')

    # Grid state
    c.execute('''CREATE TABLE IF NOT EXISTS grid_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        centre_price REAL,
        spacing_pct REAL,
        levels INTEGER,
        active INTEGER DEFAULT 1,
        pause_longs INTEGER DEFAULT 0,
        pause_shorts INTEGER DEFAULT 0,
        halt INTEGER DEFAULT 0,
        notes TEXT
    )''')

    # Grid orders
    c.execute('''CREATE TABLE IF NOT EXISTS grid_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        order_id TEXT,
        side TEXT,
        price REAL,
        size REAL,
        status TEXT,
        filled_at TEXT,
        fill_price REAL,
        fee REAL
    )''')

    # Inventory tracking
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        xrp_held REAL DEFAULT 0,
        usd_held REAL DEFAULT 0,
        net_position_usd REAL DEFAULT 0,
        inventory_skew REAL DEFAULT 0
    )''')

    # MAGI supervision decisions
    c.execute('''CREATE TABLE IF NOT EXISTS magi_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        trigger TEXT,
        melchior_action TEXT,
        melchior_conviction TEXT,
        melchior_reasoning TEXT,
        melchior_concerns TEXT,
        balthasar_action TEXT,
        balthasar_conviction TEXT,
        balthasar_reasoning TEXT,
        casper_action TEXT,
        casper_conviction TEXT,
        casper_reasoning TEXT,
        consensus_grid_action TEXT,
        consensus_risk_action TEXT,
        consensus_regime TEXT,
        applied INTEGER DEFAULT 0,
        notes TEXT
    )''')

    # Daily P&L
    c.execute('''CREATE TABLE IF NOT EXISTS pnl_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE,
        gross_pnl REAL DEFAULT 0,
        fees_paid REAL DEFAULT 0,
        net_pnl REAL DEFAULT 0,
        trades_count INTEGER DEFAULT 0,
        fill_rate REAL DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS token_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        agent TEXT NOT NULL,
        model TEXT,
        prompt_tokens INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        estimated_cost_usd REAL DEFAULT 0,
        source TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS shadow_grid_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        level_count INTEGER NOT NULL UNIQUE,
        state_blob TEXT,
        fill_count INTEGER DEFAULT 0,
        rolling_pnl_pct REAL DEFAULT 0,
        updated_at TEXT
    )''')

    conn.commit()
    conn.close()
    print("Database initialised.")


# --- Candle helpers ---

def insert_candle(timestamp, timeframe, o, h, l, c_price, volume):
    conn = get_conn()
    try:
        conn.execute('''INSERT OR IGNORE INTO candles
            (timestamp, timeframe, open, high, low, close, volume)
            VALUES (?,?,?,?,?,?,?)''',
            (timestamp, timeframe, o, h, l, c_price, volume))
        conn.commit()
    finally:
        conn.close()


def get_candles(timeframe, limit=500):
    conn = get_conn()
    rows = conn.execute('''SELECT * FROM candles WHERE timeframe=?
        ORDER BY timestamp DESC LIMIT ?''', (timeframe, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Indicator helpers ---

def upsert_indicators(timestamp, timeframe, data: dict):
    conn = get_conn()
    fields = ', '.join(data.keys())
    placeholders = ', '.join(['?' for _ in data])
    updates = ', '.join([f"{k}=excluded.{k}" for k in data.keys()])
    values = list(data.values())
    conn.execute(f'''INSERT INTO indicators (timestamp, timeframe, {fields})
        VALUES (?, ?, {placeholders})
        ON CONFLICT(timestamp, timeframe) DO UPDATE SET {updates}''',
        [timestamp, timeframe] + values)
    conn.commit()
    conn.close()


def get_latest_indicators(timeframe='1h'):
    conn = get_conn()
    row = conn.execute('''SELECT * FROM indicators WHERE timeframe=?
        ORDER BY timestamp DESC LIMIT 1''', (timeframe,)).fetchone()
    conn.close()
    return dict(row) if row else None


# --- Grid state helpers ---

def insert_grid_state(centre_price, spacing_pct, levels, pause_longs=0, pause_shorts=0, notes=None):
    conn = get_conn()
    conn.execute('''INSERT INTO grid_state
        (timestamp, centre_price, spacing_pct, levels, pause_longs, pause_shorts, notes)
        VALUES (?,?,?,?,?,?,?)''',
        (datetime.utcnow().isoformat(), centre_price, spacing_pct, levels,
         pause_longs, pause_shorts, notes))
    conn.commit()
    conn.close()


def get_current_grid_state():
    conn = get_conn()
    row = conn.execute('''SELECT * FROM grid_state
        ORDER BY timestamp DESC LIMIT 1''').fetchone()
    conn.close()
    return dict(row) if row else None


# --- Grid order helpers ---

def insert_grid_order(timestamp, order_id, side, price, size, status,
                      fee=0.0, filled_at=None, fill_price=None):
    conn = get_conn()
    conn.execute('''INSERT INTO grid_orders
        (timestamp, order_id, side, price, size, status, fee, filled_at, fill_price)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        (timestamp, order_id, side, price, size, status,
         fee, filled_at, fill_price))
    conn.commit()
    conn.close()


def update_grid_order_status(order_id, status,
                              filled_at=None, fill_price=None, fee=None):
    conn = get_conn()
    sets = ['status=?']
    vals = [status]
    if filled_at is not None:
        sets.append('filled_at=?')
        vals.append(filled_at)
    if fill_price is not None:
        sets.append('fill_price=?')
        vals.append(fill_price)
    if fee is not None:
        sets.append('fee=?')
        vals.append(fee)
    vals.append(order_id)
    conn.execute(f"UPDATE grid_orders SET {', '.join(sets)} WHERE order_id=?", vals)
    conn.commit()
    conn.close()


def get_recent_grid_orders(limit=50):
    conn = get_conn()
    rows = conn.execute('''SELECT * FROM grid_orders
        ORDER BY timestamp DESC LIMIT ?''', (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_open_orders_summary():
    """Return open order counts, prices, and recent fills for agent context."""
    from datetime import timedelta
    conn = get_conn()

    open_rows = conn.execute(
        "SELECT side, price, size FROM grid_orders WHERE status='open' "
        "ORDER BY price ASC"
    ).fetchall()

    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    fill_rows = conn.execute(
        "SELECT side, fill_price, price, size, filled_at FROM grid_orders "
        "WHERE status='filled' AND filled_at >= ? "
        "ORDER BY filled_at DESC LIMIT 10",
        (cutoff,)
    ).fetchall()

    conn.close()

    buys  = [dict(r) for r in open_rows if r['side'] == 'buy']
    sells = [dict(r) for r in open_rows if r['side'] == 'sell']
    fills = [dict(r) for r in fill_rows]

    return {
        'open_buys':     buys,
        'open_sells':    sells,
        'recent_fills':  fills,
        'buy_count':     len(buys),
        'sell_count':    len(sells),
        'highest_buy':   max((b['price'] for b in buys),  default=None),
        'lowest_sell':   min((s['price'] for s in sells), default=None),
    }


def get_trajectory_context():
    """
    Compute trajectory and positional context from recent history.
    Returns a dict of derived metrics for agent context injection.
    All values gracefully degrade to None if insufficient history exists.
    """
    from datetime import timedelta
    conn = get_conn()

    # Last 5 MAGI decisions for trajectory
    decisions = conn.execute(
        "SELECT timestamp, melchior_action, balthasar_action, "
        "casper_action, consensus_risk_action, consensus_grid_action "
        "FROM magi_decisions ORDER BY timestamp DESC LIMIT 5"
    ).fetchall()

    # Last 5 inventory snapshots for skew trajectory
    inv_rows = conn.execute(
        "SELECT timestamp, inventory_skew FROM inventory "
        "ORDER BY timestamp DESC LIMIT 5"
    ).fetchall()

    # Fills since last MAGI cycle
    last_decision_ts = decisions[0]['timestamp'] if decisions else None
    if last_decision_ts:
        fills = conn.execute(
            "SELECT side, COUNT(*) as count FROM grid_orders "
            "WHERE status='filled' AND filled_at >= ? "
            "GROUP BY side",
            (last_decision_ts,)
        ).fetchall()
    else:
        fills = []

    # Current grid state pause flags
    grid_row = conn.execute(
        "SELECT pause_longs, pause_shorts, timestamp FROM grid_state "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()

    conn.close()

    # Compute derived metrics
    result = {
        'regime_consecutive': None,
        'melchior_blocked_cycles': None,
        'skew_delta': None,
        'skew_trend': None,
        'fills_since_last_magi_buys': 0,
        'fills_since_last_magi_sells': 0,
        'cycles_since_structural_change': None,
        'pause_longs_active': 0,
        'pause_shorts_active': 0,
    }

    if grid_row:
        result['pause_longs_active'] = grid_row['pause_longs'] or 0
        result['pause_shorts_active'] = grid_row['pause_shorts'] or 0

    for f in fills:
        if f['side'] == 'buy':
            result['fills_since_last_magi_buys'] = f['count']
        elif f['side'] == 'sell':
            result['fills_since_last_magi_sells'] = f['count']

    if len(inv_rows) >= 2:
        current_skew = inv_rows[0]['inventory_skew'] or 0
        prior_skew = inv_rows[1]['inventory_skew'] or 0
        result['skew_delta'] = round(current_skew - prior_skew, 4)
        if len(inv_rows) >= 3:
            oldest_skew = inv_rows[-1]['inventory_skew'] or 0
            if current_skew > oldest_skew + 0.05:
                result['skew_trend'] = 'worsening_long'
            elif current_skew < oldest_skew - 0.05:
                result['skew_trend'] = 'worsening_short'
            else:
                result['skew_trend'] = 'stable'

    if decisions:
        # How many consecutive cycles has Casper called the same regime
        current_regime = decisions[0]['casper_action']
        count = 0
        for d in decisions:
            if d['casper_action'] == current_regime:
                count += 1
            else:
                break
        result['regime_consecutive'] = count

        # How many consecutive cycles has Melchior's recommendation been blocked
        # (grid action was MAINTAIN but Melchior didn't say MAINTAIN)
        blocked = 0
        for d in decisions:
            if (d['consensus_grid_action'] == 'MAINTAIN' and
                    d['melchior_action'] != 'MAINTAIN'):
                blocked += 1
            else:
                break
        result['melchior_blocked_cycles'] = blocked

        # How many consecutive cycles since grid structure actually changed
        stable = 0
        for d in decisions:
            if d['consensus_grid_action'] == 'MAINTAIN':
                stable += 1
            else:
                break
        result['cycles_since_structural_change'] = stable

    return result


def get_fills_today_count():
    conn = get_conn()
    today = date.today().isoformat()
    row = conn.execute('''SELECT COUNT(*) as cnt FROM grid_orders
        WHERE status='filled' AND (filled_at >= ? OR timestamp >= ?)''',
        (today, today)).fetchone()
    conn.close()
    return row['cnt'] if row else 0


# --- MAGI decision helpers ---

def insert_magi_decision(data: dict):
    conn = get_conn()
    data['timestamp'] = datetime.utcnow().isoformat()
    fields = ', '.join(data.keys())
    placeholders = ', '.join(['?' for _ in data])
    conn.execute(f'INSERT INTO magi_decisions ({fields}) VALUES ({placeholders})',
        list(data.values()))
    conn.commit()
    conn.close()


def get_recent_magi_decisions(limit=10):
    conn = get_conn()
    rows = conn.execute('''SELECT * FROM magi_decisions
        ORDER BY timestamp DESC LIMIT ?''', (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Inventory helpers ---

def upsert_inventory(xrp_held, usd_held, net_position_usd, inventory_skew):
    conn = get_conn()
    conn.execute('''INSERT INTO inventory
        (timestamp, xrp_held, usd_held, net_position_usd, inventory_skew)
        VALUES (?,?,?,?,?)''',
        (datetime.utcnow().isoformat(), xrp_held, usd_held,
         net_position_usd, inventory_skew))
    conn.commit()
    conn.close()


def get_latest_inventory():
    conn = get_conn()
    row = conn.execute('''SELECT * FROM inventory
        ORDER BY timestamp DESC LIMIT 1''').fetchone()
    conn.close()
    return dict(row) if row else None


# --- Shadow grid helpers ---

def upsert_shadow_grid_state(level_count, state_dict, fill_count=0, rolling_pnl_pct=0.0):
    conn = get_conn()
    conn.execute('''INSERT INTO shadow_grid_state
        (level_count, state_blob, fill_count, rolling_pnl_pct, updated_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(level_count) DO UPDATE SET
            state_blob=excluded.state_blob,
            fill_count=excluded.fill_count,
            rolling_pnl_pct=excluded.rolling_pnl_pct,
            updated_at=excluded.updated_at''',
        (level_count, json.dumps(state_dict), fill_count, rolling_pnl_pct,
         datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_shadow_grid_state(level_count):
    conn = get_conn()
    row = conn.execute('SELECT state_blob FROM shadow_grid_state WHERE level_count=?',
        (level_count,)).fetchone()
    conn.close()
    if row and row['state_blob']:
        return json.loads(row['state_blob'])
    return None


def get_all_shadow_states():
    conn = get_conn()
    rows = conn.execute('''SELECT level_count, fill_count, rolling_pnl_pct, updated_at
        FROM shadow_grid_state ORDER BY level_count''').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_best_shadow_from_db():
    """Return (level_count, rolling_pnl_pct) for best shadow variant with fills > 0."""
    rows = get_all_shadow_states()
    candidates = [r for r in rows if (r['fill_count'] or 0) > 0]
    if not candidates:
        return None, None
    best = max(candidates, key=lambda r: r['rolling_pnl_pct'] or 0)
    return best['level_count'], best['rolling_pnl_pct']


# --- Token usage helpers ---

def insert_token_usage(agent, model, prompt_tokens, completion_tokens, total_tokens, cost_usd, source='direct'):
    conn = get_conn()
    conn.execute('''INSERT INTO token_usage
        (timestamp, agent, model, prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd, source)
        VALUES (?,?,?,?,?,?,?,?)''',
        (datetime.utcnow().isoformat(), agent, model, prompt_tokens,
         completion_tokens, total_tokens, cost_usd, source))
    conn.commit()
    conn.close()


def get_cost_summary(days_back=30):
    from datetime import timedelta
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(days=days_back)).isoformat()
    rows = conn.execute('''SELECT agent, model,
        SUM(prompt_tokens) as prompt_tokens,
        SUM(completion_tokens) as completion_tokens,
        SUM(total_tokens) as total_tokens,
        SUM(estimated_cost_usd) as cost,
        COUNT(*) as calls
        FROM token_usage WHERE timestamp > ?
        GROUP BY agent, model''', (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_cost_today():
    conn = get_conn()
    today = date.today().isoformat()
    row = conn.execute('''SELECT
        SUM(estimated_cost_usd) as cost,
        SUM(total_tokens) as tokens,
        COUNT(*) as calls
        FROM token_usage WHERE timestamp > ?''', (today,)).fetchone()
    conn.close()
    return dict(row) if row else {'cost': 0, 'tokens': 0, 'calls': 0}


if __name__ == "__main__":
    init_db()

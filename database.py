import sqlite3
import json
from datetime import datetime
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

def insert_grid_state(centre_price, spacing_pct, levels, notes=None):
    conn = get_conn()
    conn.execute('''INSERT INTO grid_state
        (timestamp, centre_price, spacing_pct, levels, notes)
        VALUES (?,?,?,?,?)''',
        (datetime.utcnow().isoformat(), centre_price, spacing_pct, levels, notes))
    conn.commit()
    conn.close()


def get_current_grid_state():
    conn = get_conn()
    row = conn.execute('''SELECT * FROM grid_state
        ORDER BY timestamp DESC LIMIT 1''').fetchone()
    conn.close()
    return dict(row) if row else None


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
    from datetime import date
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

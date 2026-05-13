import sqlite3, json, time, os

DB_PATH = os.environ.get('TRADES_DB', '/tmp/trades.db')


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS trades (
            order_id    TEXT PRIMARY KEY,
            symbol      TEXT,
            action      TEXT,
            qty         INTEGER,
            price       REAL,
            analysis    TEXT,
            sources     TEXT,
            decision    TEXT DEFAULT 'pending',
            consumed    INTEGER DEFAULT 0,
            mode        TEXT DEFAULT 'paper',
            ts_created  REAL,
            ts_decided  REAL
        )''')


init_db()


def upsert_report(order_id, symbol, action, qty, price, analysis, sources, mode='paper'):
    with _conn() as c:
        c.execute(
            '''INSERT INTO trades
                 (order_id, symbol, action, qty, price, analysis, sources, mode, ts_created)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(order_id) DO UPDATE SET
                 symbol=excluded.symbol, action=excluded.action, qty=excluded.qty,
                 price=excluded.price, analysis=excluded.analysis, sources=excluded.sources''',
            (order_id, symbol, action, qty, price, analysis,
             json.dumps(sources), mode, time.time()),
        )


def set_decision(order_id, decision):
    """Record a decision. Returns False if already decided (first-decision-wins)."""
    with _conn() as c:
        row = c.execute(
            'SELECT decision FROM trades WHERE order_id=?', (order_id,)
        ).fetchone()
        if row and row['decision'] != 'pending':
            return False
        if not row:
            c.execute(
                'INSERT INTO trades (order_id, decision, ts_created, ts_decided) VALUES (?,?,?,?)',
                (order_id, decision, time.time(), time.time()),
            )
        else:
            c.execute(
                'UPDATE trades SET decision=?, ts_decided=? WHERE order_id=?',
                (decision, time.time(), order_id),
            )
        return True


def consume_decision(order_id):
    """Mark as consumed so the polling loop sees 'pending' on next call."""
    with _conn() as c:
        c.execute('UPDATE trades SET consumed=1 WHERE order_id=?', (order_id,))


def get_decision_status(order_id):
    """Returns {'decision': ...} dict, or None if order unknown."""
    with _conn() as c:
        row = c.execute(
            'SELECT decision, ts_decided, consumed FROM trades WHERE order_id=?', (order_id,)
        ).fetchone()
        if not row:
            return None
        if row['consumed']:
            return {'decision': 'pending'}
        return {'decision': row['decision'], 'ts': row['ts_decided']}


def get_trade(order_id):
    with _conn() as c:
        row = c.execute('SELECT * FROM trades WHERE order_id=?', (order_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d['sources'] = json.loads(d['sources'] or '[]')
        return d


def get_all_trades():
    with _conn() as c:
        rows = c.execute('SELECT * FROM trades ORDER BY ts_created DESC').fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d['sources'] = json.loads(d['sources'] or '[]')
            result.append(d)
        return result

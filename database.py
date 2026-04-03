
import sqlite3
import hashlib
from config import DB_NAME


TICKET_COLUMNS = {
    "ticket_id",
    "channel_id",
    "buyer_id",
    "seller_id",
    "crypto",
    "amount",
    "status",
    "wallet_address",
    "encrypted_private",
    "seller_address",
    "message_id",
    "description",
    "deal_id",
    "locked_amount_crypto",
}


def _ensure_column(cursor, table, column, definition):
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

def init():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS tickets (
        ticket_id INTEGER PRIMARY KEY,
        channel_id INTEGER,
        buyer_id INTEGER,
        seller_id INTEGER,
        crypto TEXT,
        amount REAL,
        status TEXT,
        wallet_address TEXT,
        encrypted_private TEXT,
        seller_address TEXT,
        message_id INTEGER,
        description TEXT,
        deal_id TEXT,
        locked_amount_crypto REAL
    )""")
    _ensure_column(c, "tickets", "description", "TEXT")
    _ensure_column(c, "tickets", "deal_id", "TEXT")
    _ensure_column(c, "tickets", "locked_amount_crypto", "REAL")
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER,
            event TEXT,
            details TEXT,
            prev_hash TEXT,
            event_hash TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _ensure_column(c, "ticket_events", "prev_hash", "TEXT")
    _ensure_column(c, "ticket_events", "event_hash", "TEXT")
    c.execute("CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER)")
    c.execute("INSERT OR IGNORE INTO counters VALUES ('ticket', 0)")
    conn.commit()
    conn.close()

def get_next_ticket_id():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE counters SET value = value + 1 WHERE name = 'ticket'")
    c.execute("SELECT value FROM counters WHERE name = 'ticket'")
    ticket_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return ticket_id

def save_ticket(ticket_id, channel_id, buyer_id, seller_id, crypto, amount, wallet_address, encrypted_private, message_id, description=None, deal_id=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tickets (
            ticket_id, channel_id, buyer_id, seller_id, crypto, amount, status,
            wallet_address, encrypted_private, seller_address, message_id, description, deal_id, locked_amount_crypto
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ticket_id,
            channel_id,
            buyer_id,
            seller_id,
            crypto,
            amount,
            "waiting",
            wallet_address,
            encrypted_private,
            None,
            message_id,
            description,
            deal_id,
            None,
        ),
    )
    conn.commit()
    conn.close()

def update_ticket(ticket_id, **kwargs):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    for key, value in kwargs.items():
        if key not in TICKET_COLUMNS:
            continue
        c.execute(f"UPDATE tickets SET {key}=? WHERE ticket_id=?", (value, ticket_id))
    conn.commit()
    conn.close()

def get_ticket(ticket_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,))
    result = c.fetchone()
    conn.close()
    return result


def get_ticket_by_channel(channel_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM tickets WHERE channel_id=?", (channel_id,))
    result = c.fetchone()
    conn.close()
    return result


def get_tickets_by_status(statuses):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    placeholders = ",".join("?" for _ in statuses)
    c.execute(f"SELECT * FROM tickets WHERE status IN ({placeholders})", tuple(statuses))
    results = c.fetchall()
    conn.close()
    return results


def log_event(ticket_id, event, details=""):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT event_hash FROM ticket_events WHERE ticket_id=? ORDER BY id DESC LIMIT 1",
        (ticket_id,),
    )
    last_row = c.fetchone()
    prev_hash = last_row[0] if last_row and last_row[0] else "GENESIS"
    payload = f"{ticket_id}|{event}|{details}|{prev_hash}".encode("utf-8")
    event_hash = hashlib.sha256(payload).hexdigest()
    c.execute(
        "INSERT INTO ticket_events (ticket_id, event, details, prev_hash, event_hash) VALUES (?,?,?,?,?)",
        (ticket_id, event, details, prev_hash, event_hash),
    )
    conn.commit()
    conn.close()


def get_ticket_events(ticket_id, limit=20):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT event, details, created_at FROM ticket_events WHERE ticket_id=? ORDER BY id DESC LIMIT ?",
        (ticket_id, limit),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def verify_ticket_audit_chain(ticket_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT event, details, prev_hash, event_hash FROM ticket_events WHERE ticket_id=? ORDER BY id ASC",
        (ticket_id,),
    )
    rows = c.fetchall()
    conn.close()

    prev_hash = "GENESIS"
    for index, (event, details, row_prev_hash, row_event_hash) in enumerate(rows, start=1):
        if row_prev_hash != prev_hash:
            return False, index
        payload = f"{ticket_id}|{event}|{details}|{row_prev_hash}".encode("utf-8")
        expected_hash = hashlib.sha256(payload).hexdigest()
        if row_event_hash != expected_hash:
            return False, index
        prev_hash = row_event_hash

    return True, None

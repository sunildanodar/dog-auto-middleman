
import sqlite3
import hashlib
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography.fernet import Fernet
from config import (
    DB_NAME,
    DB_BACKUP_DIR,
    DB_BACKUP_RETENTION_DAYS,
    DB_BACKUP_MAX_FILES,
    BACKUP_EXPORT_DIR,
    BACKUP_EXPORT_MAX_FILES,
    BACKUP_ENCRYPTION_KEY,
    ENCRYPTION_KEY,
    STRICT_KEY_FINGERPRINT,
)


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


def _key_fingerprint():
    return hashlib.sha256(ENCRYPTION_KEY).hexdigest()


def _set_meta(cursor, key, value):
    cursor.execute(
        "INSERT OR REPLACE INTO security_meta (key, value) VALUES (?, ?)",
        (key, value),
    )


def get_meta(key):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT value FROM security_meta WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def verify_or_store_key_fingerprint():
    current = _key_fingerprint()
    stored = get_meta("encryption_key_fingerprint")
    if not stored:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        _set_meta(c, "encryption_key_fingerprint", current)
        _set_meta(c, "encryption_key_fingerprint_set_at", datetime.now(timezone.utc).isoformat())
        conn.commit()
        conn.close()
        return True
    if stored != current:
        if STRICT_KEY_FINGERPRINT:
            raise RuntimeError(
                "ENCRYPTION_KEY fingerprint does not match this database. "
                "Refusing to start to avoid permanent escrow key loss."
            )
        return False
    return True

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
    c.execute("CREATE TABLE IF NOT EXISTS security_meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER)")
    c.execute("INSERT OR IGNORE INTO counters VALUES ('ticket', 0)")
    conn.commit()
    conn.close()
    verify_or_store_key_fingerprint()

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


def _backup_filename():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"data_{stamp}.db"


def _backup_export_filename():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"data_{stamp}.db.enc"


def create_db_backup():
    db_path = Path(DB_NAME)
    if not db_path.exists():
        raise RuntimeError(f"Database file not found: {db_path}")

    backup_dir = Path(DB_BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / _backup_filename()

    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup_path))
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()

    prune_old_backups()
    return str(backup_path)


def prune_old_backups():
    backup_dir = Path(DB_BACKUP_DIR)
    if not backup_dir.exists():
        return

    backups = sorted(
        [p for p in backup_dir.iterdir() if p.is_file() and p.suffix.lower() == ".db"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(DB_BACKUP_RETENTION_DAYS, 1))
    for path in backups:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified < cutoff:
            try:
                path.unlink()
            except OSError:
                pass

    backups = sorted(
        [p for p in backup_dir.iterdir() if p.is_file() and p.suffix.lower() == ".db"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    max_files = max(DB_BACKUP_MAX_FILES, 10)
    for path in backups[max_files:]:
        try:
            path.unlink()
        except OSError:
            pass


def database_safety_snapshot():
    db_path = Path(DB_NAME)
    backup_dir = Path(DB_BACKUP_DIR)
    db_exists = db_path.exists()
    db_size = db_path.stat().st_size if db_exists else 0
    backups = []
    if backup_dir.exists():
        backups = sorted(
            [p for p in backup_dir.iterdir() if p.is_file() and p.suffix.lower() == ".db"],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    last_backup_age_seconds = None
    if backups:
        last_backup_age_seconds = int(time.time() - backups[0].stat().st_mtime)

    return {
        "db_path": str(db_path.resolve()) if db_exists else str(db_path),
        "db_exists": db_exists,
        "db_size_bytes": db_size,
        "backup_dir": str(backup_dir.resolve()) if backup_dir.exists() else str(backup_dir),
        "backup_count": len(backups),
        "last_backup_age_seconds": last_backup_age_seconds,
        "key_fingerprint_ok": verify_or_store_key_fingerprint(),
    }


def prune_old_export_files():
    export_dir = Path(BACKUP_EXPORT_DIR)
    if not export_dir.exists():
        return

    files = sorted(
        [p for p in export_dir.iterdir() if p.is_file() and p.suffix.lower() == ".enc"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    max_files = max(BACKUP_EXPORT_MAX_FILES, 10)
    for path in files[max_files:]:
        try:
            path.unlink()
        except OSError:
            pass


def create_encrypted_backup_export():
    if not BACKUP_ENCRYPTION_KEY:
        raise RuntimeError(
            "BACKUP_ENCRYPTION_KEY is missing. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    backup_path = Path(create_db_backup())
    plaintext = backup_path.read_bytes()

    fernet = Fernet(BACKUP_ENCRYPTION_KEY)
    encrypted = fernet.encrypt(plaintext)

    export_dir = Path(BACKUP_EXPORT_DIR)
    export_dir.mkdir(parents=True, exist_ok=True)
    export_path = export_dir / _backup_export_filename()
    export_path.write_bytes(encrypted)

    checksum = hashlib.sha256(plaintext).hexdigest()
    checksum_path = export_path.with_suffix(export_path.suffix + ".sha256")
    checksum_path.write_text(checksum + "\n", encoding="utf-8")

    prune_old_export_files()
    return {
        "backup_path": str(backup_path),
        "export_path": str(export_path),
        "sha256": checksum,
    }

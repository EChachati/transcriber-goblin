import sqlite3
import threading

from .config import settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    used_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS docs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    owner_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS doc_access (
    doc_id TEXT NOT NULL REFERENCES docs(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL DEFAULT 'editor',
    PRIMARY KEY (doc_id, user_id)
);
CREATE TABLE IF NOT EXISTS attachments (
    sha256 TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    uploaded_by TEXT REFERENCES users(id),
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS aliases (
    doc_id TEXT NOT NULL REFERENCES docs(id),
    alias TEXT NOT NULL COLLATE NOCASE,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (doc_id, alias)
);
CREATE TABLE IF NOT EXISTS linker_proposals (
    doc_id TEXT NOT NULL REFERENCES docs(id),
    target_id TEXT NOT NULL REFERENCES docs(id),
    alias TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (doc_id, target_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_doc_access_user ON doc_access(user_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON linker_proposals(status);
"""


def get_db() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            _conn.commit()
    return _conn

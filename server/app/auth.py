import hashlib
import hmac
import secrets
import sqlite3

from fastapi import Depends, HTTPException, Request

from .config import settings
from .db import get_db


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_admin(request: Request) -> None:
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, settings.admin_token):
        raise HTTPException(status_code=401, detail="admin token required")


def get_current_user(request: Request) -> sqlite3.Row:
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not supplied:
        raise HTTPException(status_code=401, detail="missing bearer token")
    row = get_db().execute(
        "SELECT * FROM users WHERE token_hash = ?", (hash_token(supplied),)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="invalid token")
    return row


def require_admin(admin_dep=Depends(is_admin)):
    return admin_dep


def create_invite(db: sqlite3.Connection, days_valid: int | None = 7) -> dict:
    code = "goblin-" + secrets.token_urlsafe(9)
    expires_at = None
    if days_valid:
        db.execute(
            "INSERT INTO invites (code, expires_at) VALUES (?, datetime('now', ?))",
            (code, f"+{days_valid} days"),
        )
    else:
        db.execute("INSERT INTO invites (code) VALUES (?)", (code,))
        expires_at = None
    db.commit()
    row = db.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone()
    return dict(row)


def redeem_invite(db: sqlite3.Connection, code: str, name: str) -> dict:
    invite = db.execute("SELECT * FROM invites WHERE code = ?", (code.strip(),)).fetchone()
    if invite is None:
        raise HTTPException(status_code=404, detail="invite not found")
    if invite["used_by"] is not None:
        raise HTTPException(status_code=409, detail="invite already used")
    expired = db.execute(
        "SELECT 1 FROM invites WHERE code = ? AND expires_at IS NOT NULL AND expires_at < datetime('now')",
        (invite["code"],),
    ).fetchone()
    if expired:
        raise HTTPException(status_code=410, detail="invite expired")

    existing = db.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="name already taken")

    user_id = secrets.token_hex(8)
    token = secrets.token_urlsafe(24)
    db.execute(
        "INSERT INTO users (id, name, token_hash) VALUES (?, ?, ?)",
        (user_id, name, hash_token(token)),
    )
    db.execute("UPDATE invites SET used_by = ? WHERE code = ?", (user_id, code))
    db.commit()
    return {"user_id": user_id, "name": name, "token": token}

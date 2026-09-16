import secrets
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import get_current_user
from .config import settings
from .db import get_db
from .ysweet import ysweet_manager

router = APIRouter(prefix="/docs", tags=["docs"])


class DocCreate(BaseModel):
    title: str


def _rewrite_urls(token: dict, public_url: str) -> dict:
    rewritten = dict(token)
    for key in ("url", "baseUrl"):
        if key in rewritten:
            tail = rewritten[key].split("://", 1)[-1]
            suffix = "/" + tail.split("/", 1)[1] if "/" in tail else ""
            rewritten[key] = f"{public_url.rstrip('/')}{suffix}"
    return rewritten


def get_doc_or_404(db: sqlite3.Connection, doc_id: str) -> sqlite3.Row:
    doc = db.execute("SELECT * FROM docs WHERE id = ?", (doc_id,)).fetchone()
    if doc is None:
        raise HTTPException(status_code=404, detail="doc not found")
    return doc


def has_access(db: sqlite3.Connection, doc_id: str, user_id: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM doc_access WHERE doc_id = ? AND user_id = ?",
            (doc_id, user_id),
        ).fetchone()
        is not None
    )


@router.post("")
def create_doc(body: DocCreate, user=Depends(get_current_user)):
    db = get_db()
    doc_id = secrets.token_hex(8)
    ysweet_manager().create_doc(doc_id)
    db.execute(
        "INSERT INTO docs (id, title, owner_id) VALUES (?, ?, ?)",
        (doc_id, body.title, user["id"]),
    )
    db.execute(
        "INSERT INTO doc_access (doc_id, user_id, role) VALUES (?, ?, 'owner')",
        (doc_id, user["id"]),
    )
    db.commit()
    return {"id": doc_id, "title": body.title}


@router.get("")
def list_docs(user=Depends(get_current_user)):
    rows = get_db().execute(
        """
        SELECT d.id, d.title, d.created_at, a.role
        FROM docs d JOIN doc_access a ON a.doc_id = d.id
        WHERE a.user_id = ?
        ORDER BY d.created_at DESC
        """,
        (user["id"],),
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("/{doc_id}/token")
def get_doc_token(doc_id: str, user=Depends(get_current_user)):
    db = get_db()
    get_doc_or_404(db, doc_id)
    if not has_access(db, doc_id, user["id"]):
        raise HTTPException(status_code=403, detail="no access to this doc")
    token = ysweet_manager().get_or_create_client_token(doc_id)
    return _rewrite_urls(token, settings.ysweet_public_url)

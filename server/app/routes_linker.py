import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import get_current_user
from .db import get_db
from .linker import extract_keywords, run_linker
from .routes_docs import get_doc_or_404, has_access

router = APIRouter(prefix="/linker", tags=["linker"])


class AliasAdd(BaseModel):
    doc_id: str
    alias: str


class AliasDel(BaseModel):
    doc_id: str
    alias: str


@router.get("/graph")
def graph(user=Depends(get_current_user)):
    """Nodos (docs) y edges (links declarados en el espejo)."""
    db = get_db()
    rows = db.execute(
        """
        SELECT d.id, d.title
        FROM docs d JOIN doc_access a ON a.doc_id = d.id
        WHERE a.user_id = ?
        """,
        (user["id"],),
    ).fetchall()
    nodes = [{"id": r["id"], "title": r["title"]} for r in rows]

    edges = []
    for r in rows:
        path = _mirror_path(r["id"])
        if not path.exists():
            continue
        import re

        for m in re.finditer(r"\[\[([^\]]+)\]\]", path.read_text(encoding="utf-8")):
            edges.append({"source": r["id"], "target_title": m.group(1)})
    return {"nodes": nodes, "edges": edges}


@router.get("/{doc_id}/keywords")
def keywords(doc_id: str, user=Depends(get_current_user), top_n: int = 10):
    db = get_db()
    get_doc_or_404(db, doc_id)
    if not has_access(db, doc_id, user["id"]):
        raise HTTPException(status_code=403, detail="no access to this doc")
    path = _mirror_path(doc_id)
    if not path.exists():
        return {"doc_id": doc_id, "keywords": []}
    return {"doc_id": doc_id, "keywords": extract_keywords(path.read_text(encoding="utf-8"), top_n)}


@router.get("/{doc_id}/aliases")
def list_aliases(doc_id: str, user=Depends(get_current_user)):
    db = get_db()
    get_doc_or_404(db, doc_id)
    if not has_access(db, doc_id, user["id"]):
        raise HTTPException(status_code=403, detail="no access to this doc")
    rows = db.execute(
        "SELECT alias, source FROM aliases WHERE doc_id = ? ORDER BY alias", (doc_id,)
    ).fetchall()
    return {"doc_id": doc_id, "aliases": [dict(r) for r in rows]}


@router.post("/{doc_id}/aliases")
def add_alias(doc_id: str, body: AliasAdd, user=Depends(get_current_user)):
    db = get_db()
    get_doc_or_404(db, doc_id)
    if not has_access(db, doc_id, user["id"]):
        raise HTTPException(status_code=403, detail="no access to this doc")
    alias = body.alias.strip()
    if not alias:
        raise HTTPException(status_code=400, detail="alias cannot be empty")
    try:
        db.execute(
            "INSERT INTO aliases (doc_id, alias, source) VALUES (?, ?, 'manual')",
            (doc_id, alias),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="alias already exists")
    return {"doc_id": doc_id, "alias": alias}


@router.delete("/{doc_id}/aliases")
def remove_alias(doc_id: str, body: AliasDel, user=Depends(get_current_user)):
    db = get_db()
    get_doc_or_404(db, doc_id)
    if not has_access(db, doc_id, user["id"]):
        raise HTTPException(status_code=403, detail="no access to this doc")
    db.execute(
        "DELETE FROM aliases WHERE doc_id = ? AND alias = ?",
        (doc_id, body.alias),
    )
    db.commit()
    return {"removed": True}


@router.get("/proposals")
def list_proposals(status: str = "pending", user=Depends(get_current_user)):
    db = get_db()
    rows = db.execute(
        """
        SELECT p.rowid AS id, p.doc_id, p.target_id, p.alias, p.confidence, p.reason,
               d.title AS target_title
        FROM linker_proposals p JOIN docs d ON d.id = p.target_id
        WHERE p.status = ? AND p.doc_id IN (
            SELECT doc_id FROM doc_access WHERE user_id = ?
        )
        ORDER BY p.confidence DESC
        """,
        (status, user["id"]),
    ).fetchall()
    return [dict(r) for r in rows]


class ProposalAction(BaseModel):
    doc_id: str
    action: str  # 'apply' | 'dismiss'


@router.post("/proposals/{proposal_id}")
def act_proposal(proposal_id: str, body: ProposalAction, user=Depends(get_current_user)):
    db = get_db()
    row = db.execute(
        "SELECT doc_id, target_id, alias FROM linker_proposals WHERE rowid = ?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    if not has_access(db, row["doc_id"], user["id"]):
        raise HTTPException(status_code=403, detail="no access to this doc")

    target = db.execute("SELECT title FROM docs WHERE id = ?", (row["target_id"],)).fetchone()
    if body.action == "apply":
        from .linker import _apply_link

        wikilink = f"[[{target['title']}]]"
        _apply_link(row["doc_id"], row["alias"], wikilink)
        db.execute(
            "UPDATE linker_proposals SET status = 'applied' WHERE rowid = ?",
            (proposal_id,),
        )
        db.commit()
    elif body.action == "dismiss":
        db.execute(
            "UPDATE linker_proposals SET status = 'dismissed' WHERE rowid = ?",
            (proposal_id,),
        )
        db.commit()
    else:
        raise HTTPException(status_code=400, detail="action must be 'apply' or 'dismiss'")
    return {"ok": True}


@router.post("/run")
def run(user=Depends(get_current_user), target: str = "crdt"):
    """Ejecuta el linker sobre todas las notas a las que el usuario tiene acceso.

    target por defecto 'crdt' (fuente canónica). 'mirror' es útil para tests/dev
    sin y-sweet.
    """
    result = run_linker(apply_auto=True, target=target)
    # filtrar a docs del user
    db = get_db()
    allowed = {
        r["doc_id"]
        for r in db.execute(
            "SELECT doc_id FROM doc_access WHERE user_id = ?", (user["id"],)
        ).fetchall()
    }
    result["auto"] = [a for a in result["auto"] if a["doc_id"] in allowed]
    result["proposals"] = [
        p for p in result["proposals"] if p["doc_id"] in allowed
    ]
    return result


def _mirror_path(doc_id: str):
    from .config import settings

    return settings.mirror_dir / f"{doc_id}.md"

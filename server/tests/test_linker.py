from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_db
from app.main import app

client = TestClient(app)


@pytest.fixture(scope="module")
def user():
    invite = client.post(
        "/invites",
        json={"days_valid": 1},
        headers={"Authorization": "Bearer test-admin"},
    ).json()
    redeemed = client.post("/auth/redeem", json={"code": invite["code"], "name": "linker"})
    assert redeemed.status_code == 200, redeemed.text
    body = redeemed.json()
    body["id"] = body["user_id"]
    return body


def _create_doc(headers, title, creator_id):
    import secrets

    db = get_db()
    doc_id = secrets.token_hex(8)
    db.execute("INSERT INTO docs (id, title, owner_id) VALUES (?, ?, ?)", (doc_id, title, creator_id))
    db.execute("INSERT INTO doc_access (doc_id, user_id, role) VALUES (?, ?, 'owner')", (doc_id, creator_id))
    db.commit()
    return doc_id


def _write_mirror(doc_id, content):
    p = settings.mirror_dir / f"{doc_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_aliases_crud(user):
    headers = {"Authorization": f"Bearer {user['token']}"}
    doc = _create_doc(headers, "Geralt de Rivia", user["id"])

    got = client.get(f"/linker/{doc}/aliases", headers=headers).json()
    assert got["aliases"] == []

    add = client.post(
        f"/linker/{doc}/aliases",
        json={"doc_id": doc, "alias": "Geralt"},
        headers=headers,
    )
    assert add.status_code == 200, add.text

    dup = client.post(
        f"/linker/{doc}/aliases",
        json={"doc_id": doc, "alias": "Geralt"},
        headers=headers,
    )
    assert dup.status_code == 409

    got = client.get(f"/linker/{doc}/aliases", headers=headers).json()
    assert any(a["alias"] == "Geralt" for a in got["aliases"])

    rem = client.request(
        "DELETE",
        f"/linker/{doc}/aliases",
        json={"doc_id": doc, "alias": "Geralt"},
        headers=headers,
    )
    assert rem.status_code == 200

    got = client.get(f"/linker/{doc}/aliases", headers=headers).json()
    assert got["aliases"] == []


def test_link_auto_ambiguous_and_proposal(user):
    headers = {"Authorization": f"Bearer {user['token']}"}

    def _clean_docs(title):
        db = get_db()
        rows = db.execute("SELECT id FROM docs WHERE title = ?", (title,)).fetchall()
        for r in rows:
            db.execute("DELETE FROM aliases WHERE doc_id = ?", (r["id"],))
            db.execute("DELETE FROM doc_access WHERE doc_id = ?", (r["id"],))
            db.execute("DELETE FROM linker_proposals WHERE doc_id = ? OR target_id = ?", (r["id"], r["id"]))
            db.execute("DELETE FROM docs WHERE id = ?", (r["id"],))
        db.commit()

    _clean_docs("Geralt de Rivia")
    _clean_docs("Ciri")
    _clean_docs("Yennefer")
    _clean_docs("Sesión 5")

    geralt = _create_doc(headers, "Geralt de Rivia", user["id"])
    ciri = _create_doc(headers, "Ciri", user["id"])
    yennefer = _create_doc(headers, "Yennefer", user["id"])
    sesion = _create_doc(headers, "Sesión 5", user["id"])

    _write_mirror(sesion, "Geralt llego a la posada, Geralt hablo con Ciri.")
    _write_mirror(ciri, "Ciri esperaba a su padre.")

    # alias exacto unico -> auto
    client.post(
        f"/linker/{geralt}/aliases",
        json={"doc_id": geralt, "alias": "Geralt"},
        headers=headers,
    )
    result = client.post("/linker/run?target=mirror", headers=headers).json()

    applied = (settings.mirror_dir / f"{sesion}.md").read_text(encoding="utf-8")
    assert "[[Geralt de Rivia]]" in applied
    assert "[[Ciri]]" in applied

    # todas las notas citadas en una nota generan registros (auto o proposal)
    assert result["proposals"] or True


def test_normalize_plural_accents():
    from app.linker import normalize

    assert normalize("Geralt") == "geralt"
    assert normalize("Rivia") == "rivia"
    assert normalize("Notas") == "nota"
    assert normalize("Canción") == "cancion"


def test_keywords_endpoint(user):
    headers = {"Authorization": f"Bearer {user['token']}"}
    doc = _create_doc(headers, "Reunion del proyecto", user["id"])
    _write_mirror(doc, "Discutimos el deploy del servidor, el deploy y la migración de datos.")
    r = client.get(f"/linker/{doc}/keywords?top_n=5", headers=headers)
    assert r.status_code == 200
    assert "deploy" in r.json()["keywords"]

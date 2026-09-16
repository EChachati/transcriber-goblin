import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ysweet import ysweet_manager

client = TestClient(app)


@pytest.fixture(scope="module")
def user():
    invite = client.post(
        "/invites",
        json={"days_valid": 1},
        headers={"Authorization": "Bearer test-admin"},
    ).json()
    redeemed = client.post("/auth/redeem", json={"code": invite["code"], "name": "tester"})
    assert redeemed.status_code == 200, redeemed.text
    return redeemed.json()


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_admin_endpoints_reject_bad_token():
    r = client.post("/invites", json={"days_valid": 1}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_invite_lifecycle(user):
    r = client.post(
        "/invites",
        json={"days_valid": None},
        headers={"Authorization": "Bearer test-admin"},
    )
    assert r.status_code == 200
    code = r.json()["code"]

    dup_name = client.post("/auth/redeem", json={"code": code, "name": "tester"})
    assert dup_name.status_code == 409

    second = client.post("/auth/redeem", json={"code": code, "name": "second"})
    assert second.status_code == 200

    reuse = client.post("/auth/redeem", json={"code": code, "name": "third"})
    assert reuse.status_code == 409


def test_me_requires_token():
    assert client.get("/me").status_code == 401
    r = client.get("/me", headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 401


def test_attachment_roundtrip_and_dedupe(user):
    headers = {"Authorization": f"Bearer {user['token']}"}
    content = b"\x89PNG-fake-image-data" * 10
    r1 = client.post(
        "/attachments",
        files={"file": ("foto.png", content, "image/png")},
        headers=headers,
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()

    r2 = client.post(
        "/attachments",
        files={"file": ("copia.png", content, "image/png")},
        headers=headers,
    )
    assert r2.json()["sha256"] == body1["sha256"]

    dl = client.get(f"/attachments/{body1['sha256']}", headers=headers)
    assert dl.status_code == 200
    assert dl.content == content
    assert dl.headers["content-type"].startswith("image/png")

    no_auth = client.get(f"/attachments/{body1['sha256']}")
    assert no_auth.status_code == 401


def _ysweet_available() -> bool:
    try:
        ysweet_manager().check_store()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ysweet_available(), reason="y-sweet server not running on :7700")
def test_doc_flow_with_ysweet(user):
    headers = {"Authorization": f"Bearer {user['token']}"}

    created = client.post("/docs", json={"title": "Primera nota"}, headers=headers)
    assert created.status_code == 200, created.text
    doc_id = created.json()["id"]

    listing = client.get("/docs", headers=headers)
    assert any(d["id"] == doc_id for d in listing.json())

    token = client.post(f"/docs/{doc_id}/token", headers=headers)
    assert token.status_code == 200, token.text
    body = token.json()
    assert body["docId"] == doc_id
    assert body["url"].startswith(os.environ["YSWEET_PUBLIC_URL"])

    other = client.post("/auth/redeem", json={"code": _fresh_invite()["code"], "name": "intruder"})
    intruder_headers = {"Authorization": f"Bearer {other.json()['token']}"}
    forbidden = client.post(f"/docs/{doc_id}/token", headers=intruder_headers)
    assert forbidden.status_code == 403


def _fresh_invite():
    return client.post(
        "/invites",
        json={"days_valid": 1},
        headers={"Authorization": "Bearer test-admin"},
    ).json()

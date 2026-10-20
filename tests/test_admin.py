"""Admin: POST /admin/codes (local only, bearer token) and the tools/codes.py CLI."""
from __future__ import annotations

import importlib.util
import io
import re

import pytest
from conftest import ADMIN_TOKEN, ROOT

from backend.codes import CODE_ALPHABET, CodeStore, generate_code

_spec = importlib.util.spec_from_file_location("bys_tools_codes", ROOT / "tools" / "codes.py")
codes_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(codes_cli)

LOCAL = ("127.0.0.1", 40000)
AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
CODE_RE = re.compile(rf"BYS-[{CODE_ALPHABET}]{{5}}-[{CODE_ALPHABET}]{{5}}")


def test_admin_is_disabled_without_a_token(make_client):
    client = make_client(client=LOCAL, admin_token="")
    assert client.post("/admin/codes", json={}, headers=AUTH).status_code == 404


def test_admin_refuses_remote_clients(make_client):
    client = make_client(admin_token=ADMIN_TOKEN)  # default test client address is remote
    assert client.post("/admin/codes", json={}, headers=AUTH).status_code == 404


@pytest.mark.parametrize("header", [{"X-Forwarded-For": "198.51.100.4"}, {"Forwarded": "for=198.51.100.4"}])
def test_admin_refuses_proxied_requests(make_client, header):
    client = make_client(client=LOCAL, admin_token=ADMIN_TOKEN)
    assert client.post("/admin/codes", json={}, headers={**AUTH, **header}).status_code == 404


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": ADMIN_TOKEN}])
def test_admin_refuses_missing_or_wrong_tokens(make_client, headers):
    client = make_client(client=LOCAL, admin_token=ADMIN_TOKEN)
    response = client.post("/admin/codes", json={}, headers=headers)
    assert response.status_code == 401
    assert client.app.state.store.list() == []


def test_admin_creates_a_trial_code_shown_once(make_client):
    client = make_client(client=LOCAL, admin_token=ADMIN_TOKEN)
    response = client.post("/admin/codes", json={"plan": "trial", "label": "mario@example.com"}, headers=AUTH)
    assert response.status_code == 201
    body = response.json()
    assert CODE_RE.fullmatch(body["code"])
    assert body["plan"] == "trial" and body["quota"] == 30 and body["expiresAt"] is None
    assert body["label"] == "mario@example.com"
    assert response.headers["cache-control"] == "no-store"
    assert client.post("/api/access/unlock", json={"code": body["code"]}).status_code == 200


def test_admin_creates_paid_and_expiring_codes(make_client):
    client = make_client(client=LOCAL, admin_token=ADMIN_TOKEN)
    paid = client.post("/admin/codes", json={"plan": "paid", "days": 30}, headers=AUTH).json()
    assert paid["quota"] is None and paid["expiresAt"]
    trial = client.post("/admin/codes", json={"plan": "trial", "quota": 5}, headers=AUTH).json()
    assert trial["quota"] == 5
    assert client.post("/admin/codes", json={"plan": "gold"}, headers=AUTH).status_code == 422
    assert client.post("/admin/codes", json={"unexpected": 1}, headers=AUTH).status_code == 422


def _cli(*argv: str, db) -> str:
    out = io.StringIO()
    assert codes_cli.main(["--db", str(db), *argv], out=out) == 0
    return out.getvalue()


def test_cli_manages_codes(tmp_path):
    db = tmp_path / "codes.db"
    created = _cli("create", "--plan", "trial", "--label", "anna@example.com", "--count", "2", db=db)
    found = CODE_RE.findall(created)
    assert len(found) == 2
    store = CodeStore(db)
    first, second = store.list()
    assert (first.plan, first.quota, first.label) == ("trial", 30, "anna@example.com")
    assert store.find(found[0]).id == first.id

    store.consume(first.id)
    listing = _cli("list", db=db)
    assert "1/30" in listing and "anna@example.com" in listing
    assert not CODE_RE.search(listing)  # codes are never shown again

    assert "Revoked" in _cli("revoke", str(second.id), db=db)
    assert store.get(second.id).status() == "revoked"

    _cli("extend", str(first.id), "--until", "2030-01-31", db=db)
    assert store.get(first.id).expires_at == 1896134400  # 2030-02-01 00:00 UTC
    _cli("extend", str(first.id), "--never", db=db)
    assert store.get(first.id).expires_at is None

    _cli("convert", str(first.id), db=db)
    converted = store.get(first.id)
    assert converted.plan == "paid" and converted.quota is None and converted.used_count == 1


def test_cli_rejects_unknown_ids(tmp_path):
    assert codes_cli.main(["--db", str(tmp_path / "codes.db"), "revoke", "999"], out=io.StringIO()) == 1


def test_generated_codes_are_human_typable():
    samples = {generate_code() for _ in range(200)}
    assert len(samples) == 200
    for code in samples:
        assert CODE_RE.fullmatch(code)
        assert not set(code[4:].replace("-", "")) & set("01OIL")

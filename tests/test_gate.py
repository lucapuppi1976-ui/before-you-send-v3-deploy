"""Unlocking, the signed cookie, epoch revocation, and gate checks that run before the body is read."""
from __future__ import annotations

import asyncio
import re
import time

from conftest import SECRET, unlock
from fastapi.testclient import TestClient

from backend.server import create_app

TOKEN_RE = re.compile(r"^v2\.\d+\.\d+\.[0-9a-f]{64}$")


def test_wrong_code_gives_403_then_429_after_the_limit(make_client):
    client = make_client()
    for _ in range(5):
        response = client.post("/api/access/unlock", json={"code": "BYS-WRONG-CODE1"})
        assert response.status_code == 403
    blocked = client.post("/api/access/unlock", json={"code": "BYS-WRONG-CODE1"})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1


def test_unlock_limit_also_applies_per_hour(make_client):
    client = make_client(unlock_per_minute=1000, unlock_per_hour=30)
    statuses = [client.post("/api/access/unlock", json={"code": "nope"}).status_code for _ in range(31)]
    assert statuses[:30] == [403] * 30
    assert statuses[30] == 429


def test_correct_code_sets_a_hardened_host_only_cookie(make_client):
    client = make_client()
    code, record = client.app.state.store.create(plan="trial", quota=30)
    response = client.post("/api/access/unlock", json={"code": code})
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "enabled": True,
        "unlocked": True,
        "accessPage": "https://bb1studio.com/before-you-send/access/",
    }
    cookie = response.headers["set-cookie"]
    lowered = cookie.lower()
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert "path=/" in lowered
    assert "domain=" not in lowered  # host-only
    assert "secure" not in lowered  # plain http request
    value = cookie.split(";", 1)[0].split("=", 1)[1].strip('"')
    assert TOKEN_RE.match(value)
    assert value.split(".")[1] == str(record.id)  # the cookie carries the code id ...
    assert code not in cookie and code.replace("-", "") not in cookie  # ... never the code itself


def test_cookie_is_secure_over_https(make_client):
    client = make_client(base_url="https://testserver")
    code, _ = client.app.state.store.create(plan="trial", quota=30)
    cookie = client.post("/api/access/unlock", json={"code": code}).headers["set-cookie"]
    assert "secure" in cookie.lower()
    assert client.post("/api/decode/text", json={"text": "ciao"}).status_code == 200


def test_codes_match_case_spacing_and_missing_prefix(make_client):
    client = make_client(unlock_per_minute=100)
    code, _ = client.app.state.store.create(plan="trial", quota=30)
    for variant in (code.lower(), code.replace("-", " "), code[4:], f"  {code}  "):
        assert client.post("/api/access/unlock", json={"code": variant}).status_code == 200, variant


def test_access_code_longer_than_128_characters_is_rejected(make_client):
    response = make_client().post("/api/access/unlock", json={"code": "A" * 129})
    assert response.status_code == 422


def test_shared_tester_code_is_unlimited(make_client):
    client = make_client(access_code="tester-code-2026")
    response = client.post("/api/access/unlock", json={"code": "TESTER-CODE-2026"})
    assert response.status_code == 200
    for _ in range(3):
        decoded = client.post("/api/decode/text", json={"text": "ciao"})
        assert decoded.status_code == 200
        assert "x-bys-remaining" not in decoded.headers
    assert client.get("/api/config").json()["access"] == {"plan": "tester", "remaining": None}


def test_changing_the_shared_code_revokes_its_cookies(make_settings, claude):
    settings = make_settings(access_code="tester-code-2026")
    client = TestClient(create_app(settings))
    assert client.post("/api/access/unlock", json={"code": "tester-code-2026"}).status_code == 200
    rotated = TestClient(create_app(make_settings(access_code="another-code-2027")), cookies=client.cookies)
    assert rotated.post("/api/decode/text", json={"text": "ciao"}).status_code == 401


def test_token_signed_with_the_old_epoch_is_rejected(make_settings, claude):
    old = TestClient(create_app(make_settings(gate_epoch="1")))
    unlock(old)
    assert old.post("/api/decode/text", json={"text": "ciao"}).status_code == 200
    new = TestClient(create_app(make_settings(gate_epoch="2")), cookies=old.cookies)
    assert new.get("/api/access/status").json()["unlocked"] is False
    assert new.get("/api/config").status_code == 401
    assert new.post("/api/decode/text", json={"text": "ciao"}).status_code == 401


def test_token_from_a_different_secret_is_rejected(make_settings, claude):
    first = TestClient(create_app(make_settings()))
    unlock(first)
    other = TestClient(create_app(make_settings(gate_secret="another-secret-" + "z" * 32)), cookies=first.cookies)
    assert other.post("/api/decode/text", json={"text": "ciao"}).status_code == 401


def test_expired_and_tampered_tokens_are_rejected(make_client):
    client = make_client()
    gate = client.app.state.gate
    _, record = client.app.state.store.create(plan="trial", quota=30)
    expired = gate.issue_token(record.id, now=time.time() - 31 * 86400)
    assert gate.verify_token(expired) is None
    valid = gate.issue_token(record.id)
    assert gate.verify_token(valid).code_id == record.id
    version, code_id, expiry, signature = valid.split(".")
    assert gate.verify_token(f"{version}.{int(code_id) + 1}.{expiry}.{signature}") is None
    assert gate.verify_token(f"{version}.{code_id}.{int(expiry) + 999}.{signature}") is None
    assert gate.verify_token("garbage") is None
    assert gate.verify_token(f"{version}.0.{expiry}.{signature}") is None  # no shared code configured
    client.cookies.set("bys_access", expired)
    assert client.post("/api/decode/text", json={"text": "ciao"}).status_code == 401


def test_old_style_cookie_from_the_previous_version_is_rejected(make_client):
    client = make_client()
    client.cookies.set("bys_access", "YWNjZXNzOjk5OTk5OTk5OTk6ZGVhZGJlZWY=")
    assert client.get("/api/access/status").json()["unlocked"] is False


def test_logout_clears_the_cookie(unlocked):
    response = unlocked.post("/api/access/logout")
    assert response.status_code == 200
    assert 'bys_access=""' in response.headers["set-cookie"] or "max-age=0" in response.headers["set-cookie"].lower()


def test_status_reports_lock_state(make_client):
    client = make_client()
    assert client.get("/api/access/status").json()["unlocked"] is False
    unlock(client)
    assert client.get("/api/access/status").json()["unlocked"] is True


def test_unauthenticated_ai_request_is_rejected_before_the_body_is_read(make_settings, claude):
    app = create_app(make_settings())
    body_reads = 0

    async def receive():
        nonlocal body_reads
        body_reads += 1
        return {"type": "http.request", "body": b"x" * 65536, "more_body": True}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/decode/image",
        "raw_path": b"/api/decode/image",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"multipart/form-data; boundary=x"),
            (b"content-length", str(30 * 1024 * 1024).encode()),
        ],
        "client": ("203.0.113.7", 50000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 401
    assert body_reads == 0


def test_unauthenticated_streamed_upload_is_not_consumed(make_client):
    client = make_client()
    consumed = []

    def body():
        consumed.append(True)
        yield b"x" * (1024 * 1024)

    response = client.post(
        "/api/decode/audio", content=body(), headers={"content-type": "multipart/form-data; boundary=x"}
    )
    assert response.status_code == 401
    assert consumed == []


def test_secret_used_by_tests_is_long_enough():
    assert len(SECRET) >= 32

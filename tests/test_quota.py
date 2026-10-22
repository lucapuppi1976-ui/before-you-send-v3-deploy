"""Per-code quotas: atomic consumption, refunds on provider failure, ended codes."""
from __future__ import annotations

import threading
import time

import anthropic
import httpx2
import pytest
from conftest import OGG_BYTES, fake_response, unlock

from backend.codes import CodeStore

ACCESS_PAGE = "https://bb1studio.com/before-you-send/access/"


def _used(client, code_id: int) -> int:
    return client.app.state.store.get(code_id).used_count


def _server_error() -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.InternalServerError(
        "UPSTREAM-SECRET-DETAIL", response=httpx2.Response(500, request=request), body={"error": {"type": "api_error"}}
    )


def test_each_analysis_consumes_one_unit_and_reports_the_remainder(make_client):
    client = make_client()
    _, code_id = unlock(client)
    first = client.post("/api/decode/text", json={"text": "ciao", "lang": "it"})
    second = client.post("/api/score/text", json={"text": "ciao", "lang": "it"})
    assert first.headers["x-bys-remaining"] == "29"
    assert second.headers["x-bys-remaining"] == "28"
    assert _used(client, code_id) == 2
    assert client.get("/api/config").json()["access"] == {"plan": "trial", "remaining": 28}


@pytest.mark.parametrize(
    "lang,headline",
    [
        ("it", "Prova terminata"),
        ("en", "Trial ended"),
        ("es", "Prueba terminada"),
        ("fr", "Essai terminé"),
        ("de", "Test beendet"),
        ("ru", "Пробный доступ закончился"),
    ],
)
def test_the_31st_analysis_of_a_trial_is_blocked(make_client, claude, lang, headline):
    client = make_client()
    _, code_id = unlock(client)  # default trial quota: 30
    for index in range(30):
        response = client.post("/api/decode/text", json={"text": f"messaggio {index}", "lang": lang})
        assert response.status_code == 200
    calls_before = len(claude.calls)
    blocked = client.post("/api/decode/text", json={"text": "uno di troppo", "lang": lang})
    assert blocked.status_code == 403
    detail = blocked.json()["detail"]
    assert detail["code"] == "trial_ended"
    assert detail["message"].startswith(headline)
    # One code per email: who has used the trial writes to us (the access page stays in detail["accessPage"]).
    assert "hola@bb1studio.com" in detail["message"]
    assert detail["accessPage"] == ACCESS_PAGE
    assert blocked.headers["x-bys-remaining"] == "0"
    assert len(claude.calls) == calls_before  # the provider was not called
    assert _used(client, code_id) == 30
    assert client.get("/api/config").json()["access"]["remaining"] == 0


def test_a_voice_note_counts_as_one_analysis(make_client, groq):
    client = make_client()
    _, code_id = unlock(client)
    response = client.post(
        "/api/decode/audio", files={"file": ("nota.ogg", OGG_BYTES, "audio/ogg")}, data={"lang": "it"}
    )
    assert response.status_code == 200
    assert len(groq.calls) == 1
    assert _used(client, code_id) == 1


def test_claude_failure_refunds_the_unit(make_client, claude):
    client = make_client()
    _, code_id = unlock(client)
    claude.reply_with(_server_error())
    response = client.post("/api/decode/text", json={"text": "ciao"})
    assert response.status_code == 502
    assert "UPSTREAM-SECRET-DETAIL" not in response.text
    assert _used(client, code_id) == 0


def test_groq_failure_refunds_the_unit(make_client, groq):
    client = make_client()
    _, code_id = unlock(client)
    groq.status_code = 500
    groq.payload = {"error": {"message": "GROQ-SECRET-DETAIL", "type": "server_error"}}
    response = client.post("/api/decode/audio", files={"file": ("a.ogg", OGG_BYTES, "audio/ogg")})
    assert response.status_code == 502
    assert "GROQ-SECRET-DETAIL" not in response.text
    assert _used(client, code_id) == 0


def test_an_overlong_transcript_is_not_analyzed_and_refunds_the_unit(make_client, claude, groq):
    client = make_client()
    _, code_id = unlock(client)
    groq.payload = {"text": "parola " * 1000}  # 7000 characters, over the 6000 limit for typed text
    calls_before = len(claude.calls)
    response = client.post(
        "/api/decode/audio", files={"file": ("lungo.ogg", OGG_BYTES, "audio/ogg")}, data={"lang": "it"}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "voice_too_long"
    assert "6000" in detail["message"]
    assert len(claude.calls) == calls_before  # the transcript never reached Claude
    assert _used(client, code_id) == 0


def test_refusal_refunds_the_unit(make_client, claude):
    client = make_client()
    _, code_id = unlock(client)
    claude.reply_with(fake_response(stop_reason="refusal", content=[]))
    assert client.post("/api/decode/text", json={"text": "ciao"}).status_code == 422
    assert _used(client, code_id) == 0


def test_rejected_input_does_not_consume(make_client):
    client = make_client()
    _, code_id = unlock(client)
    assert client.post("/api/decode/text", json={"text": "   "}).status_code == 400
    assert client.post("/api/decode/text", json={"text": "x" * 6001}).status_code == 422
    assert client.post("/api/decode/image", files={"file": ("a.bmp", b"BM" + b"\x00" * 64, "image/bmp")}).status_code == 415
    assert _used(client, code_id) == 0


def test_revoked_code_is_rejected(make_client):
    client = make_client()
    code, code_id = unlock(client)
    assert client.app.state.store.revoke(code_id)
    response = client.post("/api/decode/text", json={"text": "ciao", "lang": "en"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "trial_ended"
    assert client.get("/api/access/status").json()["unlocked"] is False
    assert client.get("/api/config").status_code == 401
    again = client.post("/api/access/unlock", json={"code": code, "lang": "es"})
    assert again.status_code == 403
    assert again.json()["detail"]["message"].startswith("Prueba terminada")


def test_expired_code_is_rejected(make_client):
    client = make_client()
    code, code_id = unlock(client, expires_at=int(time.time()) + 3600)
    client.app.state.store.set_expiry(code_id, int(time.time()) - 1)
    assert client.post("/api/decode/text", json={"text": "ciao"}).json()["detail"]["code"] == "trial_ended"
    assert client.post("/api/access/unlock", json={"code": code}).status_code == 403


def test_used_up_code_cannot_unlock(make_client):
    client = make_client()
    code, record = client.app.state.store.create(plan="trial", quota=1)
    assert client.app.state.store.consume(record.id).ok
    response = client.post("/api/access/unlock", json={"code": code, "lang": "it"})
    assert response.status_code == 403
    assert response.json()["detail"]["message"].startswith("Prova terminata")


def test_paid_code_without_quota_is_unlimited(make_client):
    client = make_client()
    _, code_id = unlock(client, plan="paid", quota=None)
    for _ in range(3):
        response = client.post("/api/decode/text", json={"text": "ciao"})
        assert response.status_code == 200
        assert "x-bys-remaining" not in response.headers
    assert _used(client, code_id) == 3
    assert client.get("/api/config").json()["access"] == {"plan": "paid", "remaining": None}


def test_consume_is_atomic_under_concurrency(tmp_path):
    store = CodeStore(tmp_path / "bys.db")
    store.initialize()
    _, record = store.create(plan="trial", quota=5)
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        outcome = store.consume(record.id)
        with lock:
            results.append(outcome.ok)

    threads = [threading.Thread(target=worker) for _ in range(25)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 5
    assert store.get(record.id).used_count == 5


def test_refund_never_goes_below_zero(tmp_path):
    store = CodeStore(tmp_path / "bys.db")
    store.initialize()
    _, record = store.create(plan="trial", quota=5)
    store.refund(record.id)
    assert store.get(record.id).used_count == 0


def test_database_keeps_only_hashes(tmp_path):
    store = CodeStore(tmp_path / "bys.db")
    store.initialize()
    code, _ = store.create(plan="trial", quota=30, label="mario@example.com")
    raw = b"".join(path.read_bytes() for path in tmp_path.glob("bys.db*"))
    assert code.encode() not in raw
    assert code.replace("-", "").encode() not in raw
    assert store.find(code) is not None

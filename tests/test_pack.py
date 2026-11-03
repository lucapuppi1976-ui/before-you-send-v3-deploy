"""The pack of analyses paid with Paddle (backend/payments.py, Luca 22/10: 200 analyses for 9,99 €). Paddle's API
is faked: nothing leaves the machine."""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from typing import Any

import pytest
from conftest import unlock

from backend.codes import CodeStore
from backend.config import ConfigError, Settings, validate_settings

PRICE = "pri_01byspack200"
TXN = "txn_01bystest00000001"
PADDLE = {
    "paddle_api_key": "pdl_sdbx_apikey_for_tests",
    "paddle_client_token": "test_clienttokenfortests",
    "paddle_price_pack": PRICE,
}


def transaction(code_id: int, *, status: str = "completed", price: str = PRICE) -> dict[str, Any]:
    return {
        "id": TXN,
        "status": status,
        "custom_data": {"bys_code": str(code_id)},
        "items": [{"price": {"id": price}, "quantity": 1}],
    }


class FakePaddleApi:
    """Answers GET /transactions/<id> with the queued answers, the last one again and again."""

    def __init__(self) -> None:
        self.answers: list[Any] = []
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, Any]:
        self.calls.append((url, dict(headers)))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def paddle_api() -> FakePaddleApi:
    return FakePaddleApi()


@pytest.fixture
def selling(make_client, paddle_api):
    """A client with the pack for sale and a free trial whose 3 analyses are used up."""
    client = make_client(**PADDLE)
    client.app.state.paddle._fetch = paddle_api
    _, code_id = unlock(client, quota=3)
    use_up(client, code_id)
    return client, code_id


def use_up(client, code_id: int) -> None:
    with sqlite3.connect(client.app.state.store.path) as conn:
        conn.execute("UPDATE access_codes SET used_count = quota WHERE id = ?", (code_id,))


def decode(client):
    """One analysis of a text message: it uses one analysis of the code."""
    return client.post("/api/decode/text", json={"text": "Ci vediamo domani?", "lang": "it"})


def pay(client, transaction_id: str = TXN):
    return client.post("/api/pack/paid", json={"transaction_id": transaction_id, "lang": "it"})


def test_config_offers_the_pack_only_when_paddle_is_set(make_client):
    client = make_client()
    _, code_id = unlock(client, quota=3)
    body = client.get("/api/config").json()
    assert body["payment"] == {"enabled": False} and "codeId" not in body["access"]

    client = make_client(**PADDLE)
    client.cookies.clear()
    _, code_id = unlock(client, quota=3)
    body = client.get("/api/config").json()
    assert body["access"] == {"plan": "trial", "remaining": 3, "codeId": code_id}
    assert body["payment"] == {
        "enabled": True, "environment": "sandbox", "clientToken": "test_clienttokenfortests", "priceId": PRICE,
        "analyses": 200, "priceCents": 999, "currency": "EUR",
    }
    assert "pdl_sdbx" not in client.get("/api/config").text  # the API key never leaves the server


def test_a_paid_pack_adds_its_analyses_once(selling, paddle_api):
    client, code_id = selling
    paddle_api.answers = [(200, {"data": transaction(code_id)})]
    assert decode(client).status_code == 403  # trial ended

    first = pay(client)
    assert first.status_code == 200, first.text
    assert first.json() == {"status": "added", "remaining": 200}
    assert first.headers["x-bys-remaining"] == "200"
    record = client.app.state.store.get(code_id)
    assert (record.plan, record.quota, record.used_count, record.expires_at) == ("paid", 203, 3, None)
    url, headers = paddle_api.calls[0]
    assert url == f"https://sandbox-api.paddle.com/transactions/{TXN}" and headers["Authorization"].startswith("Bearer ")

    again = pay(client)  # the app asks again (a page reloaded, a second tap): nothing more is added
    assert again.json() == {"status": "already", "remaining": 200}
    assert client.app.state.store.get(code_id).quota == 203
    assert client.get("/api/config").json()["access"]["remaining"] == 200
    assert decode(client).status_code == 200


def test_two_packs_in_one_transaction_add_both(selling, paddle_api):
    client, code_id = selling
    paid = transaction(code_id)
    paid["items"][0]["quantity"] = 2
    paddle_api.answers = [(200, {"data": paid})]
    assert pay(client).json() == {"status": "added", "remaining": 400}


def test_a_payment_still_being_confirmed_asks_to_wait(selling, paddle_api):
    client, code_id = selling
    paddle_api.answers = [(200, {"data": transaction(code_id, status="ready")})]
    response = pay(client)
    assert response.status_code == 202 and response.headers["retry-after"] == "2"
    assert response.json()["detail"]["code"] == "payment_pending"
    assert client.app.state.store.get(code_id).quota == 3


@pytest.mark.parametrize("answer", [
    lambda code_id: (200, {"data": transaction(code_id + 1)}),  # paid for another code
    lambda code_id: (200, {"data": transaction(code_id, price="pri_01someotherprice")}),
    lambda code_id: (200, {"data": transaction(code_id, status="canceled")}),
    lambda code_id: (404, {"error": {"code": "not_found"}}),  # Paddle does not know it
])
def test_a_payment_that_is_not_this_pack_adds_nothing(selling, paddle_api, answer):
    client, code_id = selling
    paddle_api.answers = [answer(code_id)]
    response = pay(client)
    assert response.status_code == 402
    assert response.json()["detail"]["code"] == "payment_mismatch"
    assert "hola@bb1studio.com" in response.json()["detail"]["message"]
    assert client.app.state.store.get(code_id).quota == 3


def test_paddle_unreachable_or_refusing_our_key(selling, paddle_api):
    client, code_id = selling
    paddle_api.answers = [OSError("timed out")]
    assert pay(client).status_code == 502
    paddle_api.answers = [(401, {"error": {"code": "authentication_malformed"}})]
    response = pay(client)
    assert response.status_code == 502 and response.json()["detail"]["code"] == "payment_check_failed"
    assert client.app.state.store.get(code_id).quota == 3


def test_bad_requests(make_client, selling, paddle_api):
    client, _ = selling
    assert pay(client, "txn_<script>").status_code == 400
    assert paddle_api.calls == []
    client.cookies.clear()
    assert pay(client).status_code == 401  # no code, no payment

    plain = make_client()  # Paddle not set: nothing to buy
    unlock(plain, quota=3)
    response = pay(plain)
    assert response.status_code == 503 and response.json()["detail"]["code"] == "payment_unavailable"


def test_a_code_with_no_analyses_left_opens_the_app_only_while_the_pack_is_for_sale(make_client):
    client = make_client()
    code, code_id = unlock(client, quota=3)
    use_up(client, code_id)
    client.cookies.clear()
    ended = client.post("/api/access/unlock", json={"code": code})
    assert ended.status_code == 403 and ended.json()["detail"]["reason"] == "exhausted"

    selling = make_client(**PADDLE)  # same database
    opened = selling.post("/api/access/unlock", json={"code": code})
    assert opened.status_code == 200
    assert selling.get("/api/config").json()["access"]["remaining"] == 0


def test_the_page_lets_paddle_in_only_while_the_pack_is_for_sale(make_client):
    plain = make_client().get("/").headers
    assert "paddle" not in plain["content-security-policy"] and "payment=()" in plain["permissions-policy"]
    headers = make_client(**PADDLE).get("/").headers
    csp = headers["content-security-policy"]
    assert "script-src 'self' https://*.paddle.com" in csp and "frame-src https://*.paddle.com" in csp
    assert "frame-ancestors 'none'" in csp
    assert 'payment=(self "https://sandbox-buy.paddle.com" "https://buy.paddle.com")' in headers["permissions-policy"]


def test_client_token_must_match_the_paddle_environment(make_settings):
    validate_settings(make_settings(**PADDLE))
    with pytest.raises(ConfigError):
        validate_settings(make_settings(**{**PADDLE, "paddle_client_token": "live_token"}))
    with pytest.raises(ConfigError):
        validate_settings(make_settings(**PADDLE, paddle_environment="production"))
    assert Settings().pack_analyses == 200 and Settings().pack_price_cents == 999


WEBHOOK_SECRET = "pdl_ntfset_secret_for_tests"


def signed(client, event: dict[str, Any], *, secret: str = WEBHOOK_SECRET, stamp: int | None = None):
    """POST the event as Paddle does: Paddle-Signature ts=…;h1=HMAC-SHA256(secret, "ts:body"), no access cookie."""
    body = json.dumps(event).encode("utf-8")
    ts = str(int(time.time()) if stamp is None else stamp)
    h1 = hmac.new(secret.encode("utf-8"), ts.encode("ascii") + b":" + body, hashlib.sha256).hexdigest()
    return client.post("/api/paddle/webhook", content=body,
                       headers={"Paddle-Signature": f"ts={ts};h1={h1}", "Content-Type": "application/json"})


@pytest.fixture
def hooked(make_client):
    """The pack for sale with a webhook secret, a used-up trial, and Paddle calling without the app's cookie."""
    client = make_client(**PADDLE, paddle_webhook_secret=WEBHOOK_SECRET)
    _, code_id = unlock(client, quota=3)
    use_up(client, code_id)
    client.cookies.clear()
    return client, code_id


def test_paddles_webhook_adds_the_pack_once_even_if_the_app_never_asks(hooked):
    client, code_id = hooked
    assert signed(client, {"event_type": "transaction.completed", "data": transaction(code_id)}).json() == {"ok": True}
    record = client.app.state.store.get(code_id)
    assert (record.plan, record.quota, record.remaining) == ("paid", 203, 200)
    # transaction.paid and transaction.completed of the same payment, or a retry by Paddle, count once.
    assert signed(client, {"event_type": "transaction.paid", "data": transaction(code_id)}).status_code == 200
    assert client.app.state.store.get(code_id).quota == 203


def test_the_webhook_refuses_bad_signatures_and_old_notifications(hooked):
    client, code_id = hooked
    event = {"event_type": "transaction.completed", "data": transaction(code_id)}
    assert signed(client, event, secret="another-secret").status_code == 401
    assert signed(client, event, stamp=int(time.time()) - 3600).status_code == 401  # replayed an hour later
    assert client.post("/api/paddle/webhook", json=event).status_code == 401  # no signature at all
    assert client.app.state.store.get(code_id).quota == 3


@pytest.mark.parametrize("event", [
    lambda code_id: {"event_type": "transaction.created", "data": transaction(code_id)},  # not a payment
    lambda code_id: {"event_type": "transaction.completed",  # a Plantatam payment on the same Paddle account
                     "data": {**transaction(code_id), "custom_data": {"plantatam_code": str(code_id)}}},
    lambda code_id: {"event_type": "transaction.completed", "data": transaction(code_id, price="pri_01someotherprice")},
    lambda code_id: {"event_type": "transaction.completed", "data": transaction(code_id, status="ready")},
])
def test_the_webhook_ignores_what_is_not_a_paid_pack(hooked, event):
    client, code_id = hooked
    response = signed(client, event(code_id))
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert client.app.state.store.get(code_id).quota == 3


def test_without_a_webhook_secret_every_notification_is_refused(make_client):
    client = make_client(**PADDLE)
    _, code_id = unlock(client, quota=3)
    assert signed(client, {"event_type": "transaction.completed", "data": transaction(code_id)}).status_code == 401


def test_store_adds_a_pack_once_and_only_to_live_codes(tmp_path):
    store = CodeStore(tmp_path / "codes.db")
    store.initialize()
    _, trial = store.create(plan="trial", quota=3, expires_at=4_000_000_000)
    _, other = store.create(plan="trial", quota=3)
    _, revoked = store.create(plan="trial", quota=3)
    store.revoke(revoked.id)
    _, unlimited = store.create(plan="paid", quota=None)

    added = store.add_pack(trial.id, "txn_a", 20)
    assert added.status == "added" and added.record.remaining == 23 and added.record.expires_at is None
    assert store.add_pack(trial.id, "txn_a", 20).status == "already"
    assert store.add_pack(other.id, "txn_a", 20).status == "refused"  # one transaction, one code
    assert store.add_pack(revoked.id, "txn_b", 20).status == "refused"
    assert store.add_pack(unlimited.id, "txn_c", 20).status == "refused"
    assert store.add_pack(999, "txn_d", 20).status == "refused"
    assert store.get(trial.id).quota == 23 and store.get(other.id).quota == 3

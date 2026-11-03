"""tools/paddle_webhook.py with a fake Paddle API: one destination per address, the secret never printed."""
from __future__ import annotations

import io
from typing import Any, Optional

from tools import paddle_webhook

URL = "https://bys.bb1studio.com/api/paddle/webhook"
SECRET = "pdl_ntfset_01secretfortests"


class FakeNotifications:
    def __init__(self, existing: Optional[list[dict[str, Any]]] = None) -> None:
        self.existing = existing or []
        self.created: list[dict[str, Any]] = []

    def __call__(self, method: str, path: str, body: Optional[dict[str, Any]]) -> tuple[int, Any]:
        if method == "GET":
            return 200, {"data": self.existing}
        self.created.append(body or {})
        return 201, {"data": {"id": "ntfset_01new", **(body or {}), "endpoint_secret_key": SECRET}}


def run(monkeypatch, tmp_path, fake: FakeNotifications, *args: str) -> str:
    monkeypatch.setenv("BYS_PADDLE_API_KEY", "pdl_sdbx_apikey_secret")
    out = io.StringIO()
    assert paddle_webhook.main(list(args), out=out, call=fake, env_path=tmp_path / ".env") == 0
    return out.getvalue()


def test_creates_the_destination_for_paid_packs_and_writes_the_secret(monkeypatch, tmp_path):
    fake = FakeNotifications()
    printed = run(monkeypatch, tmp_path, fake, "--write-env")
    assert fake.created == [{
        "description": "Before You Send: pacchetti di analisi pagati", "type": "url", "destination": URL,
        "subscribed_events": ["transaction.paid", "transaction.completed"], "include_sensitive_fields": False,
    }]
    assert f"BYS_PADDLE_WEBHOOK_SECRET={SECRET}" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert SECRET not in printed and "pdl_sdbx_apikey_secret" not in printed


def test_a_second_run_creates_nothing(monkeypatch, tmp_path):
    fake = FakeNotifications([{"id": "ntfset_01old", "destination": URL, "endpoint_secret_key": SECRET}])
    printed = run(monkeypatch, tmp_path, fake)
    assert fake.created == [] and "already there" in printed and SECRET not in printed


def test_the_address_must_be_the_webhook_of_the_app(monkeypatch, tmp_path):
    monkeypatch.setenv("BYS_PADDLE_API_KEY", "pdl_sdbx_apikey_secret")
    fake = FakeNotifications()
    assert paddle_webhook.main(["--url", "http://example.com/x"], out=io.StringIO(), call=fake,
                               env_path=tmp_path / ".env") == 2
    assert fake.created == []

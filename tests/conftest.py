"""Shared fixtures. No test touches the network: Claude and Groq are faked, sockets are guarded."""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import analysis, transcription  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.server import create_app  # noqa: E402

SECRET = "test-gate-secret-" + "x" * 32
ADMIN_TOKEN = "test-admin-token-" + "y" * 32

DECODE_PAYLOAD: dict[str, Any] = {
    "verdict": "Rimanda con gentilezza",
    "meaning": "Ti rassicura, ma sposta la cosa più avanti senza una data.",
    "flags": ["Rimanda", "Tono rassicurante", "Bassa urgenza"],
    "guardrails": ["Non è un rifiuto definitivo.", "Non è nemmeno una promessa."],
    "tones": {"warmth": 72, "clarity": 38, "interest": 45, "respect": 81, "urgency": 12},
    "replies": [
        {"style": "Neutra", "text": "Va bene, sentiamoci più avanti."},
        {"style": "Calda", "text": "Nessun problema, quando vuoi ci sentiamo."},
        {"style": "Diretta", "text": "Ok, ti va di sentirci la prossima settimana?"},
    ],
}
IMAGE_PAYLOAD: dict[str, Any] = {
    "extracted_text": "No tranquilla, vediamo più avanti :)",
    "extraction_confidence": 91,
    **DECODE_PAYLOAD,
}
SCORE_PAYLOAD: dict[str, Any] = {
    "score": 42,
    "label": "Da rivedere",
    "issue": "Suona più frustrato che lucido.",
    "breakdown": {"frustration": 78, "clarity": 40, "warmth": 20, "pressure": 66},
    "rewrites": {
        "clear": "Mi dici cosa preferisci? Così capisco meglio.",
        "warm": "Ehi, mi aiuti a capire cosa preferisci? Ci tengo.",
        "firm": "Ho bisogno che mi dica cosa preferisci.",
        "short": "Cosa preferisci?",
    },
}

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
OGG_BYTES = b"OggS" + b"\x00" * 64


def text_reply(payload: Any, **fields: Any) -> SimpleNamespace:
    """A fake Messages API response whose text block carries `payload` as JSON."""
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return fake_response(
        content=[SimpleNamespace(type="thinking", thinking="", signature="sig"), SimpleNamespace(type="text", text=raw)],
        **fields,
    )


def fake_response(**fields: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "stop_reason": "end_turn",
        "stop_details": None,
        "model": "claude-sonnet-5-5",
        "content": [],
        "usage": SimpleNamespace(
            input_tokens=321, output_tokens=123, cache_creation_input_tokens=0, cache_read_input_tokens=0, iterations=None
        ),
        "_request_id": "req_test",
    }
    values.update(fields)
    return SimpleNamespace(**values)


def default_reply(params: dict[str, Any]) -> SimpleNamespace:
    properties = params["output_config"]["format"]["schema"]["properties"]
    if "rewrites" in properties:
        payload = SCORE_PAYLOAD
    elif "extracted_text" in properties:
        payload = IMAGE_PAYLOAD
    else:
        payload = DECODE_PAYLOAD
    return text_reply(payload, model=params["model"])


class FakeClaude:
    """Stands in for anthropic.Anthropic; records constructor kwargs and every create() call."""

    def __init__(self) -> None:
        self.client_kwargs: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.queue: list[Any] = []  # responses or exceptions to return next; empty means default_reply

    def reply_with(self, *items: Any) -> None:
        self.queue.extend(items)

    def factory(self, **kwargs: Any) -> SimpleNamespace:
        self.client_kwargs.append(kwargs)
        return SimpleNamespace(
            messages=SimpleNamespace(create=self._creator(beta=False)),
            beta=SimpleNamespace(messages=SimpleNamespace(create=self._creator(beta=True))),
        )

    def _creator(self, *, beta: bool) -> Callable[..., Any]:
        def create(**params: Any) -> Any:
            self.calls.append({"beta": beta, **params})
            item = self.queue.pop(0) if self.queue else default_reply(params)
            if isinstance(item, BaseException):
                raise item
            return item

        return create

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]

    @property
    def last_system(self) -> str:
        """Text of the last call's system prompt, which is sent as a single cached text block."""
        (block,) = self.last["system"]
        return block["text"]


class FakeGroq:
    """Stands in for requests.post in backend.transcription."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status_code = 200
        self.payload: Any = {
            "text": "No tranquilla, vediamo più avanti :)",
            "language": "Italian",
            "segments": [{"start": 0.0, "end": 2.0, "avg_logprob": -0.1}],
        }
        self.payloads: list[Any] = []  # one per call, in order, before falling back to payload
        self.error: Optional[BaseException] = None

    def post(self, url: str, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        payload = self.payloads.pop(0) if self.payloads else self.payload

        def as_json() -> Any:
            if isinstance(payload, Exception):
                raise payload
            return payload

        return SimpleNamespace(status_code=self.status_code, json=as_json, headers={"x-request-id": "groq_req"})


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = socket.socket.connect

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError(f"tests must not use the network (tried {address!r})")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> FakeClaude:
    fake = FakeClaude()
    monkeypatch.setattr(analysis.anthropic, "Anthropic", fake.factory)
    return fake


@pytest.fixture
def groq(monkeypatch: pytest.MonkeyPatch) -> FakeGroq:
    fake = FakeGroq()
    monkeypatch.setattr(transcription.requests, "post", fake.post)
    return fake


@pytest.fixture
def make_settings(tmp_path: Path) -> Callable[..., Settings]:
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "anthropic_api_key": "test-anthropic-key",
            "groq_api_key": "test-groq-key",
            "gate_secret": SECRET,
            "db_path": tmp_path / "data" / "bys.db",
            "ai_per_minute": 1000,
            "ai_per_day": 10000,
            "log_level": "WARNING",
        }
        values.update(overrides)
        return Settings(**values)

    return factory


@pytest.fixture
def make_client(make_settings: Callable[..., Settings], claude: FakeClaude, groq: FakeGroq) -> Callable[..., TestClient]:
    def factory(*, base_url: str = "http://testserver", client: tuple[str, int] = ("203.0.113.7", 50000), **overrides: Any) -> TestClient:
        return TestClient(create_app(make_settings(**overrides)), base_url=base_url, client=client)

    return factory


def unlock(client: TestClient, *, plan: str = "trial", quota: Optional[int] = 30, **fields: Any) -> tuple[str, int]:
    """Create a code in the app's DB and unlock the client with it. Returns (code, code id)."""
    code, record = client.app.state.store.create(plan=plan, quota=quota, **fields)
    response = client.post("/api/access/unlock", json={"code": code})
    assert response.status_code == 200, response.text
    return code, record.id


@pytest.fixture
def unlocked(make_client: Callable[..., TestClient]) -> TestClient:
    client = make_client()
    unlock(client)
    return client

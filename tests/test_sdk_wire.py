"""The real anthropic SDK on a mocked HTTP transport: what actually goes on the wire (no network)."""
from __future__ import annotations

import json
from typing import Any, Callable

import anthropic
import httpx2
import pytest
from conftest import DECODE_PAYLOAD, unlock
from fastapi.testclient import TestClient

from backend import analysis
from backend.server import create_app


def message(content: list[dict[str, Any]], *, stop_reason: str = "end_turn", **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": extra.pop("model", "claude-sonnet-5-5"),
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 410, "output_tokens": 230},
        **extra,
    }


DECODE_MESSAGE = message(
    [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": json.dumps(DECODE_PAYLOAD)}]
)


@pytest.fixture
def wire(monkeypatch):
    """Route the real SDK client to a handler; returns (requests seen, setter for the handler)."""
    seen: list[httpx2.Request] = []
    state: dict[str, Callable[[httpx2.Request], httpx2.Response]] = {
        "handler": lambda request: httpx2.Response(200, json=DECODE_MESSAGE, headers={"request-id": "req_wire"})
    }

    def transport(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return state["handler"](request)

    real_client = anthropic.Anthropic

    def factory(**kwargs: Any) -> anthropic.Anthropic:
        return real_client(**kwargs, http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(transport)))

    monkeypatch.setattr(analysis.anthropic, "Anthropic", factory)
    return seen, state


def _client(make_settings, **overrides) -> TestClient:
    client = TestClient(create_app(make_settings(**overrides)), client=("203.0.113.9", 50000))
    unlock(client)
    return client


def test_sonnet_request_on_the_wire(make_settings, wire):
    seen, _ = wire
    response = _client(make_settings).post("/api/decode/text", json={"text": "Vediamo più avanti", "lang": "it"})
    assert response.status_code == 200
    assert response.json()["verdict"] == DECODE_PAYLOAD["verdict"]
    (request,) = seen
    assert request.url.host == "api.anthropic.com" and request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "test-anthropic-key"
    assert "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
    body = json.loads(request.content)
    assert body["model"] == "claude-sonnet-5-5"
    assert body["fallbacks"] == "default"
    assert body["max_tokens"] == 16000
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
    (system,) = body["system"]
    assert system["type"] == "text" and "italiano" in system["text"]
    assert system["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in body  # an explicit marker on the system prompt, not automatic caching
    assert body["messages"][0]["role"] == "user"
    for absent in ("thinking", "temperature", "top_p", "top_k", "tool_choice", "tools"):
        assert absent not in body


def test_haiku_request_has_no_fallback_on_the_wire(make_settings, wire):
    seen, _ = wire
    assert _client(make_settings, model="claude-haiku-5-5").post("/api/decode/text", json={"text": "ciao"}).status_code == 200
    (request,) = seen
    body = json.loads(request.content)
    assert body["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in body
    assert "server-side-fallback" not in request.headers.get("anthropic-beta", "")


def test_refusal_from_the_api_is_handled(make_settings, wire):
    _, state = wire
    refusal = message([], stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": "UPSTREAM-SECRET"})
    state["handler"] = lambda request: httpx2.Response(200, json=refusal)
    response = _client(make_settings).post("/api/decode/text", json={"text": "ciao", "lang": "it"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "refused"
    assert "UPSTREAM-SECRET" not in response.text


def test_server_side_fallback_response_is_parsed(make_settings, wire):
    _, state = wire
    rescued = message(
        [
            {"type": "fallback", "from": {"model": "claude-sonnet-5-5"}, "to": {"model": "claude-sonnet-5"}},
            {"type": "text", "text": json.dumps(DECODE_PAYLOAD)},
        ],
        model="claude-sonnet-5",
        usage={
            "input_tokens": 410,
            "output_tokens": 230,
            "iterations": [
                {"type": "message", "input_tokens": 400, "output_tokens": 0},
                {"type": "fallback_message", "input_tokens": 410, "output_tokens": 230},
            ],
        },
    )
    state["handler"] = lambda request: httpx2.Response(200, json=rescued)
    settings = make_settings()
    analyzer = analysis.Analyzer.from_settings(settings)
    result, call = analyzer.decode_text("ciao", "it")
    assert result["verdict"] == DECODE_PAYLOAD["verdict"]
    assert call.fallback_used is True
    assert call.model == "claude-sonnet-5"


def test_sdk_retries_overloaded_responses_then_succeeds(make_settings, wire):
    seen, state = wire
    attempts = {"count": 0}

    def flaky(request: httpx2.Request) -> httpx2.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}, headers={"retry-after-ms": "1"})
        return httpx2.Response(200, json=DECODE_MESSAGE)

    state["handler"] = flaky
    assert _client(make_settings).post("/api/decode/text", json={"text": "ciao"}).status_code == 200
    assert len(seen) == 3  # two SDK retries (max_retries=2), then success


def test_persistent_overload_becomes_a_busy_error(make_settings, wire):
    seen, state = wire
    state["handler"] = lambda request: httpx2.Response(
        529, json={"type": "error", "error": {"type": "overloaded_error", "message": "UPSTREAM-SECRET"}}, headers={"retry-after-ms": "1"}
    )
    client = _client(make_settings)
    response = client.post("/api/decode/text", json={"text": "ciao", "lang": "en"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "busy"
    assert "UPSTREAM-SECRET" not in response.text
    assert len(seen) == 3
    assert client.get("/api/config").json()["access"]["remaining"] == 30  # refunded

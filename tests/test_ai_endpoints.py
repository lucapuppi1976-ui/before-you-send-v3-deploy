"""AI endpoints: Claude request shape, response shapes app.js consumes, errors and limits."""
from __future__ import annotations

import base64
import json

import anthropic
import httpx2
import pytest
from conftest import DECODE_PAYLOAD, JPEG_BYTES, PNG_BYTES, SCORE_PAYLOAD, fake_response, text_reply, unlock

from backend.analysis import FALLBACK_BETA

DECODE_KEYS = {"verdict", "meaning", "flags", "guardrails", "tones", "replies", "source", "input", "lang"}
SCORE_KEYS = {"score", "label", "issue", "breakdown", "rewrites", "input", "lang"}
TONE_KEYS = ["warmth", "clarity", "interest", "respect", "urgency"]
BREAKDOWN_KEYS = ["frustration", "clarity", "warmth", "pressure"]
REWRITE_KEYS = {"clear", "warm", "firm", "short"}


def _request(code: int, kind) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return kind("UPSTREAM-SECRET-DETAIL", response=httpx2.Response(code, request=request), body=None)


def assert_decode_shape(body: dict) -> None:
    assert isinstance(body["verdict"], str) and body["verdict"]
    assert isinstance(body["meaning"], str) and body["meaning"]
    assert len(body["flags"]) == 3 and all(isinstance(flag, str) and flag for flag in body["flags"])
    assert len(body["guardrails"]) == 2 and all(isinstance(item, str) and item for item in body["guardrails"])
    assert list(body["tones"]) == TONE_KEYS
    assert all(isinstance(value, int) and 0 <= value <= 100 for value in body["tones"].values())
    assert len(body["replies"]) == 3
    for reply in body["replies"]:
        assert set(reply) == {"style", "text"}
        assert isinstance(reply["style"], str) and reply["style"]
        assert isinstance(reply["text"], str) and reply["text"]


def test_decode_text_returns_the_shape_app_js_expects(unlocked, claude):
    response = unlocked.post("/api/decode/text", json={"text": "No tranquilla, vediamo più avanti :)", "lang": "it"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == DECODE_KEYS
    assert_decode_shape(body)
    assert body["source"] == "text"
    assert body["input"] == "No tranquilla, vediamo più avanti :)"
    assert body["lang"] == "it"
    assert body["verdict"] == DECODE_PAYLOAD["verdict"]
    assert body["replies"] == DECODE_PAYLOAD["replies"]


def test_claude_request_uses_structured_output_effort_and_the_fallback_branch(unlocked, claude):
    unlocked.post("/api/decode/text", json={"text": "Ci vediamo domani?", "lang": "en"})
    call = claude.last
    assert call["model"] == "claude-sonnet-5-5"
    assert call["beta"] is True  # the default model is in FALLBACK_DEFAULT_MODELS
    assert call["betas"] == [FALLBACK_BETA] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert "thinking" not in call and "tool_choice" not in call and "temperature" not in call
    assert call["output_config"]["effort"] == "low"
    output_format = call["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    schema = output_format["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"verdict", "meaning", "flags", "guardrails", "tones", "replies"}
    serialized = json.dumps(schema)
    for unsupported in ("minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength"):
        assert unsupported not in serialized
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "English" in claude.last_system
    assert call["messages"] == [{"role": "user", "content": call["messages"][0]["content"]}]
    assert "<message>\nCi vediamo domani?\n</message>" in call["messages"][0]["content"]
    assert call["max_tokens"] == 16000
    client_kwargs = claude.client_kwargs[-1]
    assert client_kwargs["api_key"] == "test-anthropic-key"
    assert client_kwargs["max_retries"] == 2
    assert client_kwargs["timeout"].read == 60.0


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-sonnet-5", "claude-opus-4-8"])
def test_models_without_default_fallback_use_the_plain_endpoint(make_client, claude, model):
    client = make_client(model=model)
    unlock(client)
    assert client.post("/api/decode/text", json={"text": "ciao"}).status_code == 200
    call = claude.last
    assert call["beta"] is False
    assert call["model"] == model
    assert "fallbacks" not in call and "betas" not in call


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"])
def test_other_supported_models_get_the_default_fallback(make_client, claude, model):
    client = make_client(model=model, effort="medium")
    unlock(client)
    client.post("/api/score/text", json={"text": "ciao"})
    assert claude.last["beta"] is True and claude.last["fallbacks"] == "default"
    assert claude.last["output_config"]["effort"] == "medium"


@pytest.mark.parametrize(
    "lang,word",
    [("it", "italiano"), ("en", "English"), ("es", "español"), ("fr", "français"), ("de", "Deutsch"), ("ru", "русском")],
)
def test_prompts_follow_the_ui_language(unlocked, claude, lang, word):
    unlocked.post("/api/decode/text", json={"text": "ciao", "lang": lang})
    assert word in claude.last_system


def test_unknown_language_falls_back_to_italian(unlocked, claude):
    body = unlocked.post("/api/decode/text", json={"text": "ciao", "lang": "pt"}).json()
    assert body["lang"] == "it"
    assert "italiano" in claude.last_system


@pytest.mark.parametrize(
    "lang,rule",
    [
        ("it", "nella stessa lingua del messaggio ricevuto"),
        ("en", "in the same language as the received message"),
        ("es", "en el mismo idioma que el mensaje recibido"),
        ("fr", "dans la même langue que le message reçu"),
        ("de", "in derselben Sprache wie die erhaltene Nachricht"),
        ("ru", "на том же языке, что и полученное сообщение"),
    ],
)
def test_ready_replies_follow_the_language_of_the_message(unlocked, claude, lang, rule):
    # The reading is in the app language; the replies go to the sender, so they use the sender's language.
    unlocked.post("/api/decode/text", json={"text": "See you on Saturday?", "lang": lang})
    assert rule in claude.last_system


def test_screenshot_replies_follow_the_language_of_the_chat(unlocked, claude):
    unlocked.post("/api/decode/image", files={"file": ("chat.png", PNG_BYTES, "image/png")}, data={"lang": "it"})
    assert "nella stessa lingua del messaggio ricevuto" in claude.last_system


@pytest.mark.parametrize(
    "lang,rule",
    [
        ("it", "nella sua stessa lingua"),
        ("en", "in its own language"),
        ("es", "en su mismo idioma"),
        ("fr", "dans sa propre langue"),
        ("de", "in ihrer eigenen Sprache"),
        ("ru", "на его собственном языке"),
    ],
)
def test_rewrites_keep_the_language_of_the_draft(unlocked, claude, lang, rule):
    unlocked.post("/api/score/text", json={"text": "See you on Saturday?", "lang": lang})
    assert rule in claude.last_system


def test_score_text_returns_the_shape_app_js_expects(unlocked, claude):
    response = unlocked.post("/api/score/text", json={"text": "Ok allora fammi sapere tu", "lang": "it"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == SCORE_KEYS
    assert body["score"] == SCORE_PAYLOAD["score"] and isinstance(body["score"], int)
    assert body["label"] == "Da rivedere"
    assert body["issue"] == SCORE_PAYLOAD["issue"]
    assert list(body["breakdown"]) == BREAKDOWN_KEYS
    assert set(body["rewrites"]) == REWRITE_KEYS
    assert body["rewrites"] == SCORE_PAYLOAD["rewrites"]
    assert body["input"] == "Ok allora fammi sapere tu" and body["lang"] == "it"
    label_schema = claude.last["output_config"]["format"]["schema"]["properties"]["label"]
    assert label_schema["enum"] == ["Molto forte", "Buona base", "Da rivedere", "Da riscrivere"]


def test_normalization_clamps_and_fixes_list_lengths(unlocked, claude):
    claude.reply_with(
        text_reply(
            {
                "verdict": "  Tono   affettuoso ",
                "meaning": "Ti vuole vedere stasera.",
                "flags": ["Affetto", "Affetto", ""],
                "guardrails": ["Uno", "Due", "Tre", "Quattro"],
                "tones": {"warmth": 140, "clarity": -3, "interest": 55.6, "respect": "80", "urgency": None},
                "replies": [{"style": "", "text": "Anch'io! A stasera."}],
            }
        )
    )
    body = unlocked.post("/api/decode/text", json={"text": "Ti amo! Ci vediamo più tardi a cena"}).json()
    assert_decode_shape(body)
    assert body["verdict"] == "Tono affettuoso"
    assert body["flags"][0] == "Affetto" and len(set(body["flags"])) == 3
    assert body["guardrails"] == ["Uno", "Due"]
    assert body["tones"] == {"warmth": 100, "clarity": 0, "interest": 56, "respect": 80, "urgency": 0}
    assert body["replies"][0] == {"style": "Neutra", "text": "Anch'io! A stasera."}


def test_score_normalization_clamps_and_fills_missing_rewrites(unlocked, claude):
    claude.reply_with(
        text_reply(
            {
                "score": 150,
                "label": "",
                "issue": "",
                "breakdown": {"frustration": -10, "clarity": 101, "warmth": 50, "pressure": 20},
                "rewrites": {"clear": "Versione chiara.", "warm": "", "firm": "Versione ferma.", "short": ""},
            }
        )
    )
    body = unlocked.post("/api/score/text", json={"text": "bozza", "lang": "en"}).json()
    assert body["score"] == 100
    assert body["label"] == "Very strong"
    assert body["issue"]
    assert body["breakdown"] == {"frustration": 0, "clarity": 100, "warmth": 50, "pressure": 20}
    assert body["rewrites"] == {"clear": "Versione chiara.", "warm": "Versione chiara.", "firm": "Versione ferma.", "short": "Versione chiara."}


@pytest.mark.parametrize(
    "message,payload_patch",
    [
        ("Ti amo! Ci vediamo più tardi a cena", {"verdict": "Affettuoso e sereno"}),
        ("Maria ha detto che l'aria condizionata Daikin costa 1.200 €", {"verdict": "Informa con dettagli"}),
        ("I'm disregarding the matters we discussed", {"meaning": "They are disregarding the matters raised."}),
    ],
)
def test_model_output_is_not_overwritten_by_demo_rules(unlocked, claude, message, payload_patch):
    claude.reply_with(text_reply({**DECODE_PAYLOAD, **payload_patch}))
    body = unlocked.post("/api/decode/text", json={"text": message, "lang": "it"}).json()
    for key, value in payload_patch.items():
        assert body[key] == value


def test_score_label_and_rewrites_are_not_canned(unlocked, claude):
    rewrites = {
        "clear": "Can we talk soon to clear this up?",
        "warm": "Hey, could we talk soon? I'd like to clear this up together.",
        "firm": "Let's talk soon and clear this up.",
        "short": "Talk soon to clear this up?",
    }
    claude.reply_with(
        text_reply({**SCORE_PAYLOAD, "score": 64, "label": "Needs work", "issue": "Could sound more natural.", "rewrites": rewrites})
    )
    body = unlocked.post("/api/score/text", json={"text": "We can meet soon to resolve, talk soon, clear?", "lang": "en"}).json()
    assert body["label"] == "Needs work"
    assert body["issue"] == "Could sound more natural."
    assert body["rewrites"] == rewrites


def test_image_upload_is_sent_as_a_base64_block(unlocked, claude):
    response = unlocked.post(
        "/api/decode/image", files={"file": ("chat.png", PNG_BYTES, "image/png")}, data={"lang": "it"}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == DECODE_KEYS | {"extracted_text", "extraction_confidence"}
    assert_decode_shape(body)
    assert body["source"] == "image"
    assert body["input"] == body["extracted_text"] == "No tranquilla, vediamo più avanti :)"
    assert body["extraction_confidence"] == 91
    image_block, text_block = claude.last["messages"][0]["content"]
    assert image_block == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(PNG_BYTES).decode()},
    }
    assert text_block["type"] == "text"
    assert list(claude.last["output_config"]["format"]["schema"]["properties"])[:2] == ["extracted_text", "extraction_confidence"]


def test_image_type_comes_from_the_bytes(unlocked, claude):
    unlocked.post("/api/decode/image", files={"file": ("photo.png", JPEG_BYTES, "image/png")})
    assert claude.last["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"


@pytest.mark.parametrize(
    "filename,data,content_type",
    [
        ("a.bmp", b"BM" + b"\x00" * 64, "image/bmp"),
        ("a.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64, "image/heic"),
        ("a.txt", PNG_BYTES, "text/plain"),
    ],
)
def test_unsupported_images_are_rejected(unlocked, claude, filename, data, content_type):
    response = unlocked.post("/api/decode/image", files={"file": (filename, data, content_type)}, data={"lang": "en"})
    assert response.status_code == 415
    assert "PNG, JPEG, WebP or GIF" in response.json()["detail"]["message"]
    assert claude.calls == []


def test_image_over_5_mb_gets_a_clear_413(unlocked, claude):
    big = PNG_BYTES + b"\x00" * (5 * 1024 * 1024)
    response = unlocked.post("/api/decode/image", files={"file": ("big.png", big, "image/png")}, data={"lang": "it"})
    assert response.status_code == 413
    assert "5 MB" in response.json()["detail"]["message"]
    huge = PNG_BYTES + b"\x00" * (6 * 1024 * 1024)
    assert unlocked.post("/api/decode/image", files={"file": ("huge.png", huge, "image/png")}).status_code == 413
    assert claude.calls == []


def test_extracted_text_without_a_file_uses_the_text_flow(unlocked, claude):
    body = unlocked.post("/api/decode/image", data={"extracted_text": "Vediamo", "lang": "it"}).json()
    assert body["source"] == "image"
    assert body["extracted_text"] == body["input"] == "Vediamo"
    assert body["extraction_confidence"] == 100
    assert isinstance(claude.last["messages"][0]["content"], str)


def test_refusal_gives_a_friendly_error_without_upstream_text(unlocked, claude):
    details = type("Details", (), {"category": "cyber", "explanation": "UPSTREAM-EXPLANATION"})()
    partial = [type("Block", (), {"type": "text", "text": "UPSTREAM-PARTIAL"})()]
    claude.reply_with(fake_response(stop_reason="refusal", stop_details=details, content=partial))
    response = unlocked.post("/api/decode/text", json={"text": "ciao", "lang": "en"})
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "refused", "message": "I can't analyze this content. Try a different message."}}
    assert "UPSTREAM" not in response.text


@pytest.mark.parametrize(
    "error,status,code",
    [
        (_request(500, anthropic.InternalServerError), 502, "analysis_failed"),
        (_request(400, anthropic.BadRequestError), 502, "analysis_failed"),
        (_request(401, anthropic.AuthenticationError), 502, "analysis_failed"),
        (_request(429, anthropic.RateLimitError), 503, "busy"),
        (_request(529, anthropic.OverloadedError), 503, "busy"),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")), 502, "analysis_failed"),
    ],
)
def test_upstream_errors_are_generic(unlocked, claude, error, status, code):
    claude.reply_with(error)
    response = unlocked.post("/api/decode/text", json={"text": "ciao"})
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert "UPSTREAM" not in response.text and "anthropic" not in response.text.lower()


def test_truncated_or_invalid_output_is_an_error(unlocked, claude):
    claude.reply_with(fake_response(stop_reason="max_tokens", content=[]), text_reply("not json"), text_reply("[1, 2]"))
    for _ in range(3):
        assert unlocked.post("/api/decode/text", json={"text": "ciao"}).status_code == 502


def test_text_over_6000_characters_is_rejected(unlocked, claude):
    assert unlocked.post("/api/decode/text", json={"text": "x" * 6000}).status_code == 200
    response = unlocked.post("/api/score/text", json={"text": "x" * 6001, "lang": "en"})
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "too_long", "message": "The text is too long: 6000 characters at most."}}
    assert unlocked.post("/api/decode/audio", data={"transcript": "x" * 6001}).status_code == 422
    assert unlocked.post("/api/decode/image", data={"extracted_text": "x" * 6001}).status_code == 422


def test_json_body_over_64_kb_is_rejected(unlocked, claude):
    payload = json.dumps({"text": "x" * 70000}).encode()
    declared = unlocked.post("/api/decode/text", content=payload, headers={"content-type": "application/json"})
    assert declared.status_code == 413

    def chunks():
        yield payload[:40000]
        yield payload[40000:]

    streamed = unlocked.post("/api/decode/text", content=chunks(), headers={"content-type": "application/json"})
    assert streamed.status_code == 413
    assert claude.calls == []


def test_empty_text_is_a_localized_400(unlocked):
    response = unlocked.post("/api/decode/text", json={"text": "  ", "lang": "es"})
    assert response.status_code == 400
    assert response.json()["detail"]["message"] == "Pega un mensaje para analizar."


def test_ai_rate_limit_returns_429_with_retry_after(make_client):
    client = make_client(ai_per_minute=2)
    unlock(client)
    assert client.post("/api/decode/text", json={"text": "uno"}).status_code == 200
    assert client.post("/api/score/text", json={"text": "due"}).status_code == 200
    blocked = client.post("/api/decode/text", json={"text": "tre"})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1


def test_without_an_anthropic_key_analysis_is_unavailable(make_client, claude):
    client = make_client(anthropic_api_key="")
    unlock(client)
    assert client.get("/api/config").json()["supports"] == {"text": False, "voice": False, "image": False, "shareCard": True}
    response = client.post("/api/decode/text", json={"text": "ciao"})
    assert response.status_code == 503
    assert claude.calls == []

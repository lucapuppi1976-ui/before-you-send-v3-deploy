"""Voice notes: the Groq request, the transcript flowing into Claude, and voice being optional."""
from __future__ import annotations

import pytest
import requests
from conftest import OGG_BYTES, PNG_BYTES, unlock

from backend import transcription
from backend.transcription import GROQ_TRANSCRIPTION_URL, TranscriptionEmpty, TranscriptionFailed


def test_groq_request_is_built_correctly_and_the_transcript_is_analyzed(unlocked, claude, groq):
    groq.payload = {"text": "  Ciao, sentiamoci la settimana prossima.  "}
    response = unlocked.post(
        "/api/decode/audio", files={"file": ("Nota vocale di Maria.ogg", OGG_BYTES, "audio/ogg")}, data={"lang": "it"}
    )
    assert response.status_code == 200
    (call,) = groq.calls
    assert call["url"] == GROQ_TRANSCRIPTION_URL == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert call["headers"] == {"Authorization": "Bearer test-groq-key"}
    assert call["data"] == {"model": "whisper-large-v3-turbo", "response_format": "json"}
    assert call["files"] == {"file": ("voice.ogg", OGG_BYTES, "audio/ogg")}  # the user's filename is not forwarded
    assert call["timeout"] == (10, 60.0)
    assert "<message>\nCiao, sentiamoci la settimana prossima.\n</message>" in claude.last["messages"][0]["content"]
    body = response.json()
    assert body["source"] == "voice"
    assert body["transcript"] == body["input"] == "Ciao, sentiamoci la settimana prossima."
    assert {"verdict", "meaning", "flags", "guardrails", "tones", "replies", "lang"} <= set(body)


def test_custom_transcription_model(make_client, groq):
    client = make_client(transcribe_model="whisper-large-v3")
    unlock(client)
    client.post("/api/decode/audio", files={"file": ("a.mp3", OGG_BYTES, "audio/mpeg")})
    assert groq.calls[0]["data"]["model"] == "whisper-large-v3"
    assert groq.calls[0]["files"]["file"][0] == "voice.mp3"


@pytest.mark.parametrize(
    "filename,content_type,expected",
    [
        ("chat.opus", "application/octet-stream", ("voice.ogg", "audio/ogg")),
        ("memo.m4a", "audio/x-m4a", ("voice.m4a", "audio/x-m4a")),
        ("blob", "audio/webm;codecs=opus", ("voice.webm", "audio/webm")),
        ("note.wav", "", ("voice.wav", "audio/wav")),
        ("picture.png", "image/png", None),
        ("clip.mp4", "video/mp4", None),
        ("unknown.bin", "application/octet-stream", None),
    ],
)
def test_only_audio_uploads_are_accepted(filename, content_type, expected):
    assert transcription.resolve_audio_upload(filename, content_type) == expected


def test_non_audio_upload_gets_415(unlocked, groq):
    response = unlocked.post("/api/decode/audio", files={"file": ("x.png", PNG_BYTES, "image/png")}, data={"lang": "en"})
    assert response.status_code == 415
    assert groq.calls == []


def test_audio_over_the_limit_gets_413(make_client, groq):
    client = make_client(max_audio_bytes=1024 * 1024)
    unlock(client)
    big = OGG_BYTES + b"\x00" * (1024 * 1024)
    response = client.post("/api/decode/audio", files={"file": ("a.ogg", big, "audio/ogg")}, data={"lang": "en"})
    assert response.status_code == 413
    assert "1 MB" in response.json()["detail"]["message"]
    assert groq.calls == []


def test_default_audio_limit_is_15_mb_at_the_middleware(unlocked, groq):
    huge = OGG_BYTES + b"\x00" * (16 * 1024 * 1024)
    assert unlocked.post("/api/decode/audio", files={"file": ("a.ogg", huge, "audio/ogg")}).status_code == 413
    assert groq.calls == []


def test_without_a_groq_key_voice_is_reported_unavailable(make_client, groq, claude):
    client = make_client(groq_api_key="")
    unlock(client)
    assert client.get("/api/config").json()["supports"]["voice"] is False
    upload = client.post("/api/decode/audio", files={"file": ("a.ogg", OGG_BYTES, "audio/ogg")}, data={"lang": "it"})
    assert upload.status_code == 503
    assert upload.json()["detail"]["code"] == "voice_unavailable"
    typed = client.post("/api/decode/audio", data={"transcript": "Vediamo più avanti", "lang": "it"})
    assert typed.status_code == 200
    assert typed.json()["transcript"] == "Vediamo più avanti"
    assert groq.calls == []


def test_typed_transcript_skips_groq(unlocked, groq):
    response = unlocked.post(
        "/api/decode/audio",
        files={"file": ("a.ogg", OGG_BYTES, "audio/ogg")},
        data={"transcript": "Testo già trascritto", "lang": "it"},
    )
    assert response.status_code == 200
    assert response.json()["source"] == "voice"
    assert groq.calls == []


def test_missing_audio_and_transcript_is_a_400(unlocked):
    response = unlocked.post("/api/decode/audio", data={"lang": "it"})
    assert response.status_code == 400
    assert response.json()["detail"]["message"] == "Carica un file audio o incolla una trascrizione."


def test_empty_transcript_is_reported(unlocked, groq):
    groq.payload = {"text": "   "}
    response = unlocked.post("/api/decode/audio", files={"file": ("a.ogg", OGG_BYTES, "audio/ogg")})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "empty_audio"


def test_groq_rate_limit_is_reported_as_busy(unlocked, groq):
    groq.status_code = 429
    groq.payload = {"error": {"message": "GROQ-SECRET", "type": "rate_limit_exceeded"}}
    response = unlocked.post("/api/decode/audio", files={"file": ("a.ogg", OGG_BYTES, "audio/ogg")})
    assert response.status_code == 503
    assert "GROQ-SECRET" not in response.text


def test_groq_network_error_is_generic(unlocked, groq):
    groq.error = requests.ConnectionError("GROQ-SECRET connection detail")
    response = unlocked.post("/api/decode/audio", files={"file": ("a.ogg", OGG_BYTES, "audio/ogg")})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "transcription_failed"
    assert "GROQ-SECRET" not in response.text


def test_transcribe_sends_the_optional_language_only_when_given(groq):
    transcription.transcribe(
        OGG_BYTES, filename="voice.ogg", content_type="audio/ogg", api_key="k", model="m", timeout_seconds=5, language="es"
    )
    transcription.transcribe(OGG_BYTES, filename="voice.ogg", content_type="audio/ogg", api_key="k", model="m", timeout_seconds=5)
    assert groq.calls[0]["data"] == {"model": "m", "response_format": "json", "language": "es"}
    assert "language" not in groq.calls[1]["data"]


def test_transcribe_rejects_bad_responses(groq):
    groq.payload = ValueError("not json")
    with pytest.raises(TranscriptionFailed):
        transcription.transcribe(OGG_BYTES, filename="v.ogg", content_type="audio/ogg", api_key="k", model="m", timeout_seconds=5)
    groq.payload = {"text": ""}
    with pytest.raises(TranscriptionEmpty):
        transcription.transcribe(OGG_BYTES, filename="v.ogg", content_type="audio/ogg", api_key="k", model="m", timeout_seconds=5)

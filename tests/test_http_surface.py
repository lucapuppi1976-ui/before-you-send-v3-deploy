"""Static files, security headers, and what the HTTP surface does and does not expose."""
from __future__ import annotations

from pathlib import Path

import pytest
from conftest import ROOT, unlock
from fastapi.testclient import TestClient

from backend.middleware import CONTENT_SECURITY_POLICY
from backend.server import create_app


def test_root_serves_the_app(make_client):
    response = make_client().get("/")
    assert response.status_code == 200
    assert '<script src="app.js"></script>' in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/app.js", "/styles.css", "/sw.js", "/manifest.webmanifest", "/locales/it.json", "/locales/en.json",
        "/locales/es.json", "/assets/icon-192.png", "/assets/icon-512.png", "/assets/chat_demo_final.png",
        "/favicon.ico", "/robots.txt",
    ],
)
def test_frontend_urls_are_unchanged(make_client, path):
    assert make_client().get(path).status_code == 200


def test_robots_txt_disallows_api(make_client):
    body = make_client().get("/robots.txt").text
    assert "Disallow: /api/" in body


@pytest.mark.parametrize(
    "path",
    [
        "/app.py", "/render.yaml", "/README_START.txt", "/requirements.txt", "/backend/server.py",
        "/tools/codes.py", "/.git/config", "/.gitignore", "/data/bys.db", "/public/index.html",
    ],
)
def test_source_and_config_files_are_not_served(make_client, path):
    assert make_client().get(path).status_code == 404


def test_env_file_in_project_root_is_not_served(make_client):
    env_file = ROOT / ".env"
    created = not env_file.exists()
    if created:
        env_file.write_text("ANTHROPIC_API_KEY=sk-ant-should-never-leak\n", encoding="utf-8")
    try:
        response = make_client().get("/.env")
        assert response.status_code == 404
        assert "sk-ant" not in response.text
    finally:
        if created:
            env_file.unlink()


def test_dotfiles_inside_public_are_not_served(make_settings, claude, tmp_path: Path):
    public = tmp_path / "public"
    (public / "assets").mkdir(parents=True)
    (public / "index.html").write_text("<!doctype html><title>ok</title>", encoding="utf-8")
    (public / "assets" / "icon-192.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (public / ".env").write_text("SECRET=1", encoding="utf-8")
    (public / ".hidden").mkdir()
    (public / ".hidden" / "file.txt").write_text("x", encoding="utf-8")
    client = TestClient(create_app(make_settings(), public_dir=public))
    assert client.get("/index.html").status_code == 200
    assert client.get("/.env").status_code == 404
    assert client.get("/.hidden/file.txt").status_code == 404


@pytest.mark.parametrize("path", ["/", "/api/health", "/does-not-exist", "/api/config"])
def test_security_headers_on_every_response(make_client, path):
    response = make_client().get(path)
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "microphone=()" in response.headers["permissions-policy"]
    assert response.headers["x-frame-options"] == "DENY"


def test_csp_matches_the_required_policy():
    assert CONTENT_SECURITY_POLICY == (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )


def test_security_headers_on_rejections(make_client):
    response = make_client().post("/api/decode/text", json={"text": "ciao"})
    assert response.status_code == 401
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_api_docs_are_disabled(make_client, path):
    assert make_client().get(path).status_code == 404


def test_health_is_minimal(make_client):
    response = make_client().get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_no_cors_headers_for_foreign_origins(make_client):
    client = make_client()
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers
    preflight = client.options(
        "/api/decode/text",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in preflight.headers
    assert "access-control-allow-credentials" not in preflight.headers


def test_api_responses_are_not_cacheable_but_static_files_are_revalidated(make_client):
    client = make_client()
    assert client.get("/api/health").headers["cache-control"] == "no-store"
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/app.js").headers["cache-control"] == "no-cache"
    assert "max-age" in client.get("/assets/icon-192.png").headers["cache-control"]


def test_config_exposes_capability_booleans_only(make_client):
    client = make_client()
    assert client.get("/api/config").status_code == 401
    unlock(client)
    body = client.get("/api/config").json()
    assert body["supports"] == {"text": True, "voice": True, "image": True, "shareCard": True}
    assert body["access"] == {"plan": "trial", "remaining": 30}
    raw = client.get("/api/config").text.lower()
    for leak in ("claude", "sonnet", "whisper", "groq", "anthropic", "key"):
        assert leak not in raw

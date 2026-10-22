"""Static checks on the frontend: syntax, CSP compatibility, locales, service worker rules."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
from conftest import ROOT

PUBLIC = ROOT / "public"
LOCALES = {lang: json.loads((PUBLIC / "locales" / f"{lang}.json").read_text(encoding="utf-8")) for lang in ("it", "en", "es", "fr", "de", "ru")}
INDEX = (PUBLIC / "index.html").read_text(encoding="utf-8")
APP_JS = (PUBLIC / "app.js").read_text(encoding="utf-8")
SW_JS = (PUBLIC / "sw.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("name", ["app.js", "sw.js"])
def test_javascript_parses(name):
    result = subprocess.run(["node", "--check", str(PUBLIC / name)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_index_has_no_inline_script_or_handlers():
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", INDEX, flags=re.S | re.I)
    assert scripts and all('src="' in attrs and not body.strip() for attrs, body in scripts)
    assert not re.search(r"\son[a-z]+\s*=", INDEX, flags=re.I)
    assert "javascript:" not in INDEX.lower()


def test_locales_have_the_same_keys():
    keys = {lang: set(data) for lang, data in LOCALES.items()}
    assert all(k == keys["it"] for k in keys.values())


def test_names_blur_claim_is_gone():
    for data in LOCALES.values():
        assert not [key for key in data if key.startswith("settings.blur") or key.startswith("misc.names_")]
    assert "blurNames" not in APP_JS and "toggleBlur" not in INDEX and "names_yes" not in APP_JS


def test_onboarding_notice_discloses_the_processors():
    for lang, data in LOCALES.items():
        notice = data["onb3.notice"]
        assert "Anthropic (Claude)" in notice and "Groq" in notice, lang
        assert "BYS" in notice, lang


def test_every_translation_key_used_by_the_page_exists():
    used = set(re.findall(r'data-i18n(?:-placeholder)?="([^"]+)"', INDEX))
    used |= set(re.findall(r"\bt\('([a-z0-9_.]+)'", APP_JS))
    used |= set(re.findall(r"'((?:rewrite|metric)\.[a-z_]+)'", APP_JS))  # LABEL_KEYS, looked up dynamically
    used |= {f"source.{name}" for name in ("text", "voice", "image")}
    for lang, data in LOCALES.items():
        missing = sorted(key for key in used if key not in data)
        assert not missing, (lang, missing)


def test_the_email_link_opens_the_app_unlocked():
    # #code=…&lang=… from the code email: read once, removed from the address bar, never sent to the server.
    assert "new URLSearchParams(location.hash.slice(1))" in APP_JS
    assert "history.replaceState(null, '', location.pathname + location.search)" in APP_JS
    assert re.search(r'id="gateCodeInput"[^>]*type="text"', INDEX)


def test_access_page_comes_from_the_server():
    assert "/it/before-you-send" not in APP_JS and "/es/before-you-send" not in APP_JS
    assert "gateState.accessPage" in APP_JS


def test_service_worker_rules():
    assert "voice_note_demo_27s.wav" not in SW_JS
    assert "bys-v3-public-gate-v1-2" not in SW_JS
    assert "'/api/'" in SW_JS and "response.status === 200" in SW_JS
    precache = re.search(r"const PRECACHE = \[(.*?)\];", SW_JS, flags=re.S).group(1)
    for path in re.findall(r"'\./([^']*)'", precache):
        assert (PUBLIC / (path or "index.html")).exists(), path


def test_text_inputs_match_the_server_limit():
    for field in ("decodeTextInput", "voiceTranscript", "imageExtractedText", "sendInput"):
        assert re.search(rf'<textarea id="{field}" maxlength="6000"', INDEX), field
    assert 'maxlength="128"' in INDEX

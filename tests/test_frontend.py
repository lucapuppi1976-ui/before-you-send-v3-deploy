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
    used = set(re.findall(r'data-i18n(?:-placeholder|-aria-label)?="([^"]+)"', INDEX))
    used |= set(re.findall(r"\bt\('([a-zA-Z0-9_.]+)'", APP_JS))
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


def test_the_demo_recording_if_any_exists_and_is_not_precached():
    demo = re.search(r"const DEMO_VOICE = '([^']*)'", APP_JS).group(1)
    if demo:
        assert (PUBLIC / demo).exists()
        assert demo.rsplit("/", 1)[-1] not in SW_JS


def test_service_worker_rules():
    assert "voice_note_demo_27s.wav" not in SW_JS
    assert "bys-v3-public-gate-v1-2" not in SW_JS
    assert "'/api/'" in SW_JS and "response.status === 200" in SW_JS
    precache = re.search(r"const PRECACHE = \[(.*?)\];", SW_JS, flags=re.S).group(1)
    for path in re.findall(r"'\./([^']*)'", precache):
        assert (PUBLIC / (path or "index.html")).exists(), path


def test_text_inputs_match_the_server_limit():
    for field in ("decodeTextInput", "voiceTranscript", "imageExtractedText", "sendInput", "sendReplyTo"):
        assert re.search(rf'<textarea id="{field}" maxlength="6000"', INDEX), field
    assert 'maxlength="128"' in INDEX


EXAMPLE_KINDS = ("decodeText", "voice", "image", "send")


def test_free_examples_exist_in_every_language_with_the_shape_the_app_renders():
    inputs = None
    for lang in LOCALES:
        data = json.loads((PUBLIC / "samples" / f"{lang}.json").read_text(encoding="utf-8"))
        assert set(data) == set(EXAMPLE_KINDS), lang
        # Same Spanish messages in every language: only the reading changes.
        current = {kind: data[kind]["input"] for kind in EXAMPLE_KINDS}
        assert inputs in (None, current), lang
        inputs = current
        for kind in ("decodeText", "voice", "image"):
            result = data[kind]["result"]
            assert result["lang"] == lang and result["verdict"] and result["meaning"], (lang, kind)
            assert len(result["flags"]) == 3 and len(result["guardrails"]) == 2 and len(result["replies"]) == 3
            assert all(reply["style"] and reply["text"] for reply in result["replies"])
            assert set(result["tones"]) and all(0 <= value <= 100 for value in result["tones"].values())
        assert data["voice"]["result"]["transcript"] == inputs["voice"]
        assert data["image"]["result"]["extracted_text"] == inputs["image"]
        send = data["send"]["result"]
        assert 0 <= send["score"] <= 100 and send["label"] and send["issue"], lang
        assert set(send["rewrites"]) == {"clear", "warm", "firm", "short"} and all(send["rewrites"].values())


def test_examples_are_free_and_precached():
    # An example is shown from samples/<lang>.json; an empty field gets a hint instead of analyzing the example.
    assert "fetch(`samples/${lang}.json`)" in APP_JS
    assert "|| samples." not in APP_JS and "const samples" not in APP_JS
    assert APP_JS.count("isExampleInput(") >= 5
    precache = re.search(r"const PRECACHE = \[(.*?)\];", SW_JS, flags=re.S).group(1)
    for lang in LOCALES:
        assert f"'./samples/{lang}.json'" in precache, lang


def test_one_analysis_at_a_time():
    buttons = re.search(r"const ANALYSIS_BUTTONS = \[(.*?)\];", APP_JS, flags=re.S).group(1)
    for button in ("decodeTextAnalyze", "decodeVoiceAnalyze", "decodeImageAnalyze", "sendAnalyze", "runQuickDemo"):
        assert f"'#{button}'" in buttons, button
    for button in ("decodeTextAnalyze", "decodeVoiceAnalyze", "decodeImageAnalyze", "sendAnalyze"):
        assert re.search(rf"\$\('#{button}'\)\?\.addEventListener\('click', \(\) => withAnalysisLock\(", APP_JS), button


def test_the_home_starts_with_the_two_functions():
    home = re.search(r'<section id="screen-home".*?(?=<section id="screen-decode")', INDEX, flags=re.S).group(0)
    decode, send = home.index('data-open="screen-decode"'), home.index('data-open="screen-send"')
    assert decode < send < home.index('id="runQuickDemo"') < home.index('id="installBanner"')
    # No technical connection check or counters: a notice appears only when the service has a problem.
    for gone in ("apiStatusBadge", "statAnalyses", "statLastMode", "home.hero", "home.fast"):
        assert gone not in INDEX, gone
    assert 'id="serviceNotice"' in home and "showServiceNotice(" in APP_JS


def test_texts_for_users_not_for_developers():
    assert "IT / EN / ES" not in INDEX and 'data-i18n="send.context"' not in INDEX
    for data in LOCALES.values():
        assert not [key for key in data if key.startswith(("home.health.", "home.stat.", "misc."))]
        assert "onboarding" not in data["settings.onboarding.title"].lower()
    # The page's own (Italian) texts never flash while the app opens: hidden until translated.
    assert re.search(r'<html lang="[a-z]+" class="i18n-pending">', INDEX)
    assert "classList.remove('i18n-pending')" in APP_JS


def test_the_score_can_include_the_message_it_replies_to():
    assert 'id="sendReplyTo"' in INDEX and "reply_to: replyTo" in APP_JS


def test_every_reply_and_version_can_be_copied():
    assert "copyCard(r.style, r.text, i)" in APP_JS
    assert "copyCard(displayLabel(key), result.rewrites[key], i)" in APP_JS
    assert "t('results.copied')" in APP_JS
    assert "copyFirst" not in APP_JS and "rewrite-tab" not in APP_JS


def test_share_uses_the_phone_menu_and_falls_back_to_a_download():
    assert "navigator.canShare?.({ files: [file] })" in APP_JS and "navigator.share(" in APP_JS
    assert "link.download = filename" in APP_JS


def test_after_a_result_another_message_and_the_analyses_left():
    assert "startOver('decode')" in APP_JS and "startOver('send')" in APP_JS
    assert INDEX.count('class="quota-pill quota-mirror hidden"') == 2  # decode and send screens


def test_history_cards_open_the_result_and_one_entry_can_be_deleted():
    assert "data-history-open" in APP_JS and "data-history-delete" in APP_JS
    assert "window.confirm(t('confirm.deleteOne'))" in APP_JS
    assert "data-history-share" not in APP_JS


def test_voice_notes_and_screenshots_have_big_buttons_not_the_browser_file_field():
    for field in ("voiceUpload", "imageUpload"):
        assert re.search(rf'<label class="pick-btn" for="{field}">', INDEX), field
        assert re.search(rf'<input type="file" id="{field}" class="file-input"', INDEX), field
    assert 'id="voiceRecordBtn"' in INDEX and "new MediaRecorder(" in APP_JS and "MAX_RECORDING_SECONDS" in APP_JS
    # The chosen, recorded or shared file is what gets analyzed, not a pasted text left behind.
    assert "const file = capabilities.voice ? voiceFile : null;" in APP_JS and "const file = imageFile;" in APP_JS


def test_share_from_whatsapp_goes_through_the_service_worker():
    manifest = json.loads((PUBLIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    target = manifest["share_target"]
    assert (target["action"], target["method"], target["enctype"]) == ("/share-target", "POST", "multipart/form-data")
    accept = target["params"]["files"][0]["accept"]
    assert "audio/*" in accept and "image/*" in accept and ".opus" in accept
    assert "const SHARE_PATH = '/share-target';" in SW_JS and "receiveShare(request)" in SW_JS
    assert "key !== CACHE && key !== SHARE_CACHE" in SW_JS  # an update never drops what was just shared
    share_cache = re.search(r"const SHARE_CACHE = '([^']+)'", SW_JS).group(1)
    assert f"const SHARE_CACHE = '{share_cache}';" in APP_JS
    assert "takeSharedContent()" in APP_JS and "applySharedContent(share)" in APP_JS


def test_screen_readers_get_names_tabs_and_a_tappable_address():
    assert INDEX.count('aria-label="Indietro" data-i18n-aria-label="onb.back">←</button>') == 2
    assert 'class="tabbar" role="tablist"' in INDEX and INDEX.count('role="tab" aria-selected=') == 3
    assert INDEX.count('role="tabpanel"') == 3 and "tab.setAttribute('aria-selected'" in APP_JS
    assert 'data-i18n="gate.note" data-link-email' in INDEX and "link.href = `mailto:${CONTACT_EMAIL}`" in APP_JS


def test_only_the_language_in_use_is_loaded():
    assert "await loadLocales([currentLang]);" in APP_JS and "await loadLocales([next]);" in APP_JS
    assert "const langs = LANGS;" not in APP_JS


def test_installed_app_icon_fills_the_android_shape():
    manifest = json.loads((PUBLIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    maskable = [icon for icon in manifest["icons"] if icon.get("purpose") == "maskable"]
    assert {icon["sizes"] for icon in maskable} == {"192x192", "512x512"}
    assert all((PUBLIC / icon["src"]).exists() for icon in manifest["icons"])
    assert manifest["lang"] == "es"


def test_the_old_demo_voice_note_is_gone():
    assert not (PUBLIC / "assets" / "voice_note_demo_27s.wav").exists()


def test_deleting_and_signing_out_ask_first():
    assert 'id="resetAllBtn"' not in INDEX  # no reset button on the Home, only in the settings
    for key in ("confirm.reset", "confirm.clearHistory", "confirm.lock"):
        assert f"window.confirm(t('{key}'))" in APP_JS, key
    assert 'id="gateContactBtn"' in INDEX and 'id="trialEndedHome"' in INDEX

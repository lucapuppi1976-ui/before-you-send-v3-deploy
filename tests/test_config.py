"""The gate fails closed: unsafe settings stop the server from starting."""
from __future__ import annotations

import logging
import os
import subprocess
import sys

import pytest
from conftest import ROOT, SECRET
from fastapi.testclient import TestClient

from backend.config import DEFAULT_MODEL, ConfigError, load_settings, validate_settings
from backend.server import create_app


def test_gate_is_on_when_the_flag_is_missing():
    assert load_settings({}).gate_enabled is True


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", " Off "])
def test_gate_is_off_only_when_explicitly_false(value):
    assert load_settings({"BYS_GATE_ENABLED": value}).gate_enabled is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "", "maybe", "disabled"])
def test_any_other_flag_value_keeps_the_gate_on(value):
    assert load_settings({"BYS_GATE_ENABLED": value}).gate_enabled is True


def test_startup_refused_without_a_secret(make_settings):
    with pytest.raises(ConfigError, match="BYS_GATE_SECRET"):
        create_app(make_settings(gate_secret=""))


def test_startup_refused_with_a_short_secret(make_settings):
    with pytest.raises(ConfigError, match="at least 32"):
        create_app(make_settings(gate_secret="x" * 31))


def test_secret_never_falls_back_to_api_keys_or_a_default():
    settings = load_settings({"ANTHROPIC_API_KEY": "sk-ant-" + "k" * 40, "OPENAI_API_KEY": "sk-" + "o" * 40})
    assert settings.gate_secret == ""
    with pytest.raises(ConfigError):
        validate_settings(settings)


def test_missing_shared_code_is_fine_because_codes_live_in_the_database(make_settings):
    app = create_app(make_settings(access_code=""))
    assert TestClient(app).get("/api/access/status").json() == {
        "enabled": True,
        "unlocked": False,
        "accessPage": "https://bb1studio.com/before-you-send/access/",
    }


def test_gate_off_logs_a_loud_warning_and_opens_the_ai_endpoints(make_settings, claude, caplog):
    with caplog.at_level(logging.WARNING, logger="bys"):
        app = create_app(make_settings(gate_enabled=False, gate_secret=""))
    assert any("SECURITY WARNING" in record.getMessage() and "gate is OFF" in record.getMessage() for record in caplog.records)
    client = TestClient(app)
    assert client.get("/api/access/status").json()["unlocked"] is True
    assert client.post("/api/decode/text", json={"text": "ciao"}).status_code == 200
    assert client.get("/api/config").json()["access"] == {"plan": "open", "remaining": None}


def test_short_admin_token_is_refused(make_settings):
    with pytest.raises(ConfigError, match="BYS_ADMIN_TOKEN"):
        create_app(make_settings(admin_token="short"))


def test_invalid_effort_is_refused(make_settings):
    with pytest.raises(ConfigError, match="BYS_EFFORT"):
        create_app(make_settings(effort="extreme"))


def test_defaults():
    settings = load_settings({})
    assert DEFAULT_MODEL == "claude-sonnet-5-5"
    assert settings.model == "claude-sonnet-5-5"
    assert settings.effort == "low"
    assert settings.transcribe_model == "whisper-large-v3-turbo"
    assert settings.trial_quota == 30
    assert settings.max_image_bytes == 5 * 1024 * 1024
    assert settings.max_audio_bytes == 15 * 1024 * 1024
    assert (settings.unlock_per_minute, settings.unlock_per_hour) == (5, 30)
    assert (settings.ai_per_minute, settings.ai_per_day) == (10, 200)
    assert settings.db_path == ROOT / "data" / "bys.db"


def test_relative_db_path_is_resolved_from_the_project_folder():
    assert load_settings({"BYS_DB_PATH": "var/codes.db"}).db_path == ROOT / "var" / "codes.db"


def test_unusable_database_path_refuses_to_start(make_settings, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError, match="database"):
        create_app(make_settings(db_path=blocker / "bys.db"))


def _run_import(env_overrides: dict[str, str], tmp_path) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("BYS_", "ANTHROPIC_", "GROQ_"))}
    # Explicit values win over any developer .env file, which never overrides existing variables.
    env.update({"BYS_GATE_SECRET": "", "BYS_ACCESS_CODE": "", "BYS_DB_PATH": str(tmp_path / "bys.db"), "BYS_GATE_ENABLED": ""})
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import app"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60
    )


def test_server_process_refuses_to_start_with_a_clear_message(tmp_path):
    result = _run_import({"BYS_GATE_SECRET": "too-short"}, tmp_path)
    assert result.returncode == 1
    assert "Startup refused" in result.stderr
    assert "BYS_GATE_SECRET" in result.stderr


def test_server_process_starts_with_a_long_secret(tmp_path):
    result = _run_import({"BYS_GATE_SECRET": SECRET}, tmp_path)
    assert result.returncode == 0, result.stderr


def test_env_example_has_placeholders_only_and_works_with_an_appended_secret(tmp_path, monkeypatch):
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "sk-ant" not in template and "gsk_" not in template
    assert "BYS_MODEL=claude-sonnet-5-5" in template
    env_file = tmp_path / ".env"
    env_file.write_text(template + f"BYS_GATE_SECRET={SECRET}\n", encoding="utf-8")  # what launch_windows.bat does
    for key in [key for key in os.environ if key.startswith(("BYS_", "ANTHROPIC_", "GROQ_"))]:
        monkeypatch.delenv(key)
    from backend.config import load_env_file

    load_env_file(env_file)
    settings = load_settings()
    for key in [key for key in os.environ if key.startswith(("BYS_", "ANTHROPIC_", "GROQ_"))]:
        monkeypatch.delenv(key)  # undo what the .env file loaded
    assert settings.gate_secret == SECRET
    assert settings.model == "claude-sonnet-5-5" and settings.admin_token == "" and settings.access_code == ""
    assert validate_settings(settings)  # starts, with warnings about the missing API keys

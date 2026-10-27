"""tools/build_samples.py: the free examples are built like the answers of the app's endpoints, without the network."""
from __future__ import annotations

import json
import sys

from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import build_samples  # noqa: E402


class FakeAnalyzer:
    def __init__(self):
        self.calls = []

    def decode_text(self, message, lang):
        self.calls.append(("decode", message, lang))
        return {"verdict": f"v-{lang}", "meaning": "m", "flags": ["a", "b", "c"], "tones": {"warmth": 50},
                "guardrails": ["x", "y"], "replies": [{"style": "s", "text": "t"}] * 3}, None

    def score_text(self, draft, lang):
        self.calls.append(("score", draft, lang))
        return {"score": 40, "label": "l", "issue": "i", "breakdown": {"clarity": 40},
                "rewrites": {"clear": "c", "warm": "w", "firm": "f", "short": "s"}}, None


def test_one_language_is_four_calls_shaped_like_the_endpoints():
    analyzer = FakeAnalyzer()
    data = build_samples.build_language(analyzer, "fr")
    assert len(analyzer.calls) == 4 and all(call[2] == "fr" for call in analyzer.calls)
    assert {kind: data[kind]["input"] for kind in data} == build_samples.INPUTS
    assert data["decodeText"]["result"]["source"] == "text"
    assert data["voice"]["result"]["transcript"] == build_samples.INPUTS["voice"]
    assert data["image"]["result"]["extracted_text"] == build_samples.INPUTS["image"]
    assert data["send"]["result"]["input"] == build_samples.INPUTS["send"]


def test_files_are_written_as_utf8_json(tmp_path):
    path = build_samples.write_language("es", {"send": {"input": "¿Qué?", "result": {}}}, out_dir=tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["send"]["input"] == "¿Qué?"


def test_without_yes_nothing_is_called_or_written(capsys, monkeypatch):
    monkeypatch.setattr(build_samples, "write_language", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert build_samples.main([]) == 0
    assert "--yes" in capsys.readouterr().out


def test_the_image_example_is_the_text_of_the_demo_screenshot():
    app_js = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert "assets/chat_demo_final.png" in app_js
    assert build_samples.INPUTS["image"].startswith("Uf, esta semana voy fatal de trabajo")

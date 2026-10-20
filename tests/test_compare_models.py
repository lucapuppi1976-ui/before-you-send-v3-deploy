"""tools/compare_models.py: estimate first, --yes required, same code path as the app."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from collections import Counter

from conftest import ROOT

_spec = importlib.util.spec_from_file_location("bys_tools_compare", ROOT / "tools" / "compare_models.py")
compare = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = compare  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(compare)


def test_samples_are_balanced():
    samples = compare.load_samples(ROOT / "tools" / "samples.json")
    assert len(samples) == 24
    assert len({sample["id"] for sample in samples}) == 24
    assert Counter(sample["lang"] for sample in samples) == {"it": 8, "en": 8, "es": 8}
    assert Counter(sample["feature"] for sample in samples) == {"decode": 12, "score": 12}
    categories = {sample["category"] for sample in samples}
    assert {"ambiguous", "passive-aggressive", "affectionate", "postponing"} <= categories


def test_default_models_and_prices():
    assert compare.DEFAULT_MODELS == ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
    assert compare.PRICES_PER_MTOK["claude-opus-5-5"] == (4.00, 20.00)
    assert compare.PRICES_PER_MTOK["claude-sonnet-5-5"] == (2.00, 10.00)
    assert compare.PRICES_PER_MTOK["claude-haiku-5-5"] == (0.10, 0.50)


def test_without_yes_it_only_prints_the_estimate(claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    out = io.StringIO()
    assert compare.main([], out=out) == 0
    text = out.getvalue()
    assert text.startswith("Estimated cost for 24 samples x 3 models = 72 requests")
    assert "total" in text and "Re-run with --yes" in text
    assert claude.calls == [] and claude.client_kwargs == []


def test_yes_without_an_api_key_refuses(claude, monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert compare.main(["--yes", "--out-dir", str(tmp_path)], out=io.StringIO()) == 2
    assert claude.calls == []


def test_run_uses_the_app_pipeline_and_writes_reports(claude, monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("BYS_EFFORT", "low")
    out = io.StringIO()
    code = compare.main(
        ["--yes", "--limit", "13", "--models", "claude-sonnet-5-5", "claude-haiku-5-5", "--out-dir", str(tmp_path)], out=out
    )
    assert code == 0, out.getvalue()
    assert len(claude.calls) == 26
    models = Counter(call["model"] for call in claude.calls)
    assert models == {"claude-sonnet-5-5": 13, "claude-haiku-5-5": 13}
    # Same request building as the app: structured output, effort, and the fallback branch.
    sonnet = next(call for call in claude.calls if call["model"] == "claude-sonnet-5-5")
    haiku = next(call for call in claude.calls if call["model"] == "claude-haiku-5-5")
    assert sonnet["beta"] and sonnet["fallbacks"] == "default"
    assert not haiku["beta"] and "fallbacks" not in haiku
    assert sonnet["output_config"]["format"]["type"] == "json_schema"
    assert sonnet["output_config"]["effort"] == "low"
    markdown = next(tmp_path.glob("compare-*.md")).read_text(encoding="utf-8")
    page = next(tmp_path.glob("compare-*.html")).read_text(encoding="utf-8")
    for report in (markdown, page):
        assert "claude-sonnet-5-5" in report and "claude-haiku-5-5" in report
        assert "it-decode-postpone" in report and "it-score-frustrated" in report
        assert "Rimanda con gentilezza" in report  # normalized decode output
        assert "Da rivedere" in report  # normalized score output
    assert "321 in / 123 out" in markdown
    expected = 13 * compare.request_cost("claude-sonnet-5-5", 321, 123) + 13 * compare.request_cost("claude-haiku-5-5", 321, 123)
    assert f"actual cost ${expected:.4f}" in out.getvalue()


def test_errors_are_reported_not_raised(claude, monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    from conftest import fake_response

    claude.reply_with(fake_response(stop_reason="refusal", content=[]))
    out = io.StringIO()
    assert compare.main(["--yes", "--limit", "1", "--models", "claude-sonnet-5-5", "--concurrency", "1", "--out-dir", str(tmp_path)], out=out) == 0
    assert "1 errors" in out.getvalue()
    assert "AnalysisRefused" in next(tmp_path.glob("compare-*.md")).read_text(encoding="utf-8")


def test_samples_file_is_valid_json():
    json.loads((ROOT / "tools" / "samples.json").read_text(encoding="utf-8"))

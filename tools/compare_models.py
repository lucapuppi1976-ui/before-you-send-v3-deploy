"""Compare Claude models on BYS's own prompts, for quality, latency and cost.

Every sample in tools/samples.json goes through each model with the same request
building and normalization the app uses (backend.analysis.Analyzer). The script writes
a side-by-side Markdown and HTML report to tools/reports/.

This calls the paid Anthropic API. It prints an estimated cost first and only runs
with --yes. It needs ANTHROPIC_API_KEY (environment or the project's .env file).

  python tools/compare_models.py                    # estimate only
  python tools/compare_models.py --yes              # run all samples on the default models
  python tools/compare_models.py --yes --models claude-sonnet-5-5 claude-haiku-5-5 --limit 6
"""
from __future__ import annotations

import argparse
import html
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TextIO

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import analysis  # noqa: E402
from backend.config import EFFORT_LEVELS, ConfigError, load_env_file, load_settings  # noqa: E402

DEFAULT_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]

# USD per million tokens (input, output), standard Claude API rates as listed in the
# Claude API reference used for this project (cached 2026-10-06). Claude Haiku 5.5 is
# listed at the rate for prompts up to 100K tokens, which covers every BYS request.
PRICES_PER_MTOK = {
    "claude-fable-5-1": (10.00, 50.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Rough output budget per request for the estimate (low-effort thinking plus the JSON reply).
ESTIMATED_OUTPUT_TOKENS = {"decode": 1200, "score": 1000}
CHARS_PER_TOKEN = 3.0  # conservative: overestimates input tokens a little


@dataclass
class Outcome:
    sample: dict[str, Any]
    model: str
    result: Optional[dict[str, Any]] = None
    call: Optional[analysis.ClaudeResult] = None
    error: Optional[str] = None

    @property
    def cost(self) -> float:
        if self.call is None:
            return 0.0
        # Priced at the model that served the reply (a refusal fallback may switch models).
        # Top-level usage covers that final attempt only; declined attempts are not added.
        served = self.call.model if self.call.model in PRICES_PER_MTOK else self.model
        return request_cost(served, self.call.input_tokens, self.call.output_tokens)


def request_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    input_price, output_price = PRICES_PER_MTOK[model]
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def load_samples(path: Path) -> list[dict[str, Any]]:
    samples = json.loads(path.read_text(encoding="utf-8"))
    for sample in samples:
        if sample.get("feature") not in ("decode", "score") or not sample.get("text"):
            raise ValueError(f"invalid sample: {sample!r}")
    return samples


def build_request(sample: dict[str, Any]) -> analysis.ClaudeRequest:
    if sample["feature"] == "decode":
        return analysis.decode_text_request(sample["text"], sample["lang"])
    return analysis.score_request(sample["text"], sample["lang"])


def estimate(samples: list[dict[str, Any]], models: list[str]) -> dict[str, float]:
    per_model: dict[str, float] = {}
    for model in models:
        total = 0.0
        for sample in samples:
            request = build_request(sample)
            chars = len(request.system) + len(json.dumps(request.messages, ensure_ascii=False)) + len(json.dumps(request.schema))
            total += request_cost(model, int(chars / CHARS_PER_TOKEN), ESTIMATED_OUTPUT_TOKENS[sample["feature"]])
        per_model[model] = total
    return per_model


def run_one(analyzer: analysis.Analyzer, sample: dict[str, Any], model: str, effort: str) -> Outcome:
    try:
        if sample["feature"] == "decode":
            result, call = analyzer.decode_text(sample["text"], sample["lang"], model=model, effort=effort)
        else:
            result, call = analyzer.score_text(sample["text"], sample["lang"], model=model, effort=effort)
        return Outcome(sample, model, result=result, call=call)
    except analysis.AnalysisError as exc:
        return Outcome(sample, model, error=f"{type(exc).__name__}: {exc}")


# --- Reports ---------------------------------------------------------------------------


def _summary_rows(outcomes: list[Outcome], models: list[str]) -> list[dict[str, Any]]:
    rows = []
    for model in models:
        mine = [outcome for outcome in outcomes if outcome.model == model]
        ok = [outcome for outcome in mine if outcome.call]
        latencies = [outcome.call.latency_ms for outcome in ok]
        cost = sum(outcome.cost for outcome in ok)
        rows.append(
            {
                "model": model,
                "ok": len(ok),
                "errors": len(mine) - len(ok),
                "p50": int(statistics.median(latencies)) if latencies else 0,
                "max": max(latencies) if latencies else 0,
                "input": sum(outcome.call.input_tokens for outcome in ok),
                "output": sum(outcome.call.output_tokens for outcome in ok),
                "cost": cost,
                "per_1000": cost / len(ok) * 1000 if ok else 0.0,
                "fallbacks": sum(1 for outcome in ok if outcome.call.fallback_used),
            }
        )
    return rows


def _fields(outcome: Outcome) -> list[tuple[str, str]]:
    if outcome.error:
        return [("error", outcome.error)]
    result = outcome.result or {}
    if outcome.sample["feature"] == "decode":
        fields = [
            ("verdict", result["verdict"]),
            ("meaning", result["meaning"]),
            ("flags", " · ".join(result["flags"])),
            ("guardrails", " / ".join(result["guardrails"])),
            ("tones", ", ".join(f"{key} {value}" for key, value in result["tones"].items())),
        ]
        fields += [(f"reply {index}", f"[{reply['style']}] {reply['text']}") for index, reply in enumerate(result["replies"], 1)]
    else:
        fields = [
            ("score", f"{result['score']} ({result['label']})"),
            ("issue", result["issue"]),
            ("breakdown", ", ".join(f"{key} {value}" for key, value in result["breakdown"].items())),
        ]
        fields += [(f"rewrite: {key}", value) for key, value in result["rewrites"].items()]
    call = outcome.call
    served = f" (served by {call.model})" if call.model != outcome.model else ""
    fields.append(("latency / tokens / cost", f"{call.latency_ms} ms · {call.input_tokens} in / {call.output_tokens} out · ${outcome.cost:.5f}{served}"))
    return fields


def _md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", "<br>")


def write_markdown(path: Path, outcomes: list[Outcome], models: list[str], samples: list[dict[str, Any]], effort: str) -> None:
    lines = [
        "# BYS model comparison",
        "",
        f"Generated {datetime.now().isoformat(timespec='seconds')} · effort `{effort}` · {len(samples)} samples · "
        "prices: standard Claude API rates per million tokens, see PRICES_PER_MTOK in the script.",
        "",
        "| model | ok | errors | p50 latency | max latency | input tokens | output tokens | cost | per 1000 analyses | fallbacks |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in _summary_rows(outcomes, models):
        lines.append(
            f"| {row['model']} | {row['ok']} | {row['errors']} | {row['p50']} ms | {row['max']} ms | {row['input']} | "
            f"{row['output']} | ${row['cost']:.4f} | ${row['per_1000']:.2f} | {row['fallbacks']} |"
        )
    by_key = {(outcome.sample["id"], outcome.model): outcome for outcome in outcomes}
    for sample in samples:
        lines += ["", f"## {sample['id']} · {sample['feature']} · {sample['lang']} · {sample['category']}", "", f"> {_md(sample['text'])}", ""]
        columns = {model: dict(_fields(by_key[(sample["id"], model)])) for model in models}
        names: list[str] = []
        for model in models:
            names += [name for name in columns[model] if name not in names]
        lines.append("| field | " + " | ".join(models) + " |")
        lines.append("|---|" + "---|" * len(models))
        for name in names:
            lines.append(f"| {name} | " + " | ".join(_md(columns[model].get(name, "")) for model in models) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_html(path: Path, outcomes: list[Outcome], models: list[str], samples: list[dict[str, Any]], effort: str) -> None:
    esc = html.escape
    summary = "".join(
        f"<tr><td>{esc(row['model'])}</td><td>{row['ok']}</td><td>{row['errors']}</td><td>{row['p50']} ms</td>"
        f"<td>{row['max']} ms</td><td>{row['input']}</td><td>{row['output']}</td><td>${row['cost']:.4f}</td>"
        f"<td>${row['per_1000']:.2f}</td><td>{row['fallbacks']}</td></tr>"
        for row in _summary_rows(outcomes, models)
    )
    by_key = {(outcome.sample["id"], outcome.model): outcome for outcome in outcomes}
    sections = []
    for sample in samples:
        cards = []
        for model in models:
            outcome = by_key[(sample["id"], model)]
            rows = "".join(f"<dt>{esc(name)}</dt><dd>{esc(str(value))}</dd>" for name, value in _fields(outcome))
            cards.append(f"<div class='card{' error' if outcome.error else ''}'><h3>{esc(model)}</h3><dl>{rows}</dl></div>")
        sections.append(
            f"<section><h2>{esc(sample['id'])} <small>{esc(sample['feature'])} · {esc(sample['lang'])} · {esc(sample['category'])}</small></h2>"
            f"<blockquote>{esc(sample['text'])}</blockquote><div class='grid' style='--cols:{len(models)}'>{''.join(cards)}</div></section>"
        )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>BYS model comparison</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; color: #1d2433; background: #f6f7fb; }}
table {{ border-collapse: collapse; background: #fff; }} th, td {{ border: 1px solid #d8dce6; padding: 6px 10px; text-align: left; }}
section {{ margin-top: 32px; }} h2 small {{ color: #6b7385; font-weight: normal; }}
blockquote {{ background: #fff; border-left: 4px solid #8aa2ff; margin: 8px 0 12px; padding: 8px 12px; }}
.grid {{ display: grid; grid-template-columns: repeat(var(--cols), minmax(240px, 1fr)); gap: 12px; }}
.card {{ background: #fff; border: 1px solid #d8dce6; border-radius: 10px; padding: 12px; }} .card.error {{ border-color: #e06666; }}
.card h3 {{ margin: 0 0 8px; font-size: 1rem; }} dt {{ font-weight: 600; margin-top: 6px; font-size: .85rem; color: #4a5266; }} dd {{ margin: 0; }}
</style></head><body>
<h1>BYS model comparison</h1>
<p>Generated {esc(datetime.now().isoformat(timespec='seconds'))} · effort <code>{esc(effort)}</code> · {len(samples)} samples ·
prices: standard Claude API rates per million tokens (PRICES_PER_MTOK in the script).</p>
<table><tr><th>model</th><th>ok</th><th>errors</th><th>p50 latency</th><th>max latency</th><th>input tokens</th><th>output tokens</th><th>cost</th><th>per 1000 analyses</th><th>fallbacks</th></tr>{summary}</table>
{''.join(sections)}
</body></html>
"""
    path.write_text(page, encoding="utf-8")


# --- CLI -------------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None, out: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(description="Compare Claude models on BYS prompts (quality, latency, cost).")
    parser.add_argument("--yes", action="store_true", help="actually call the API (costs money)")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS, help=f"default: {' '.join(DEFAULT_MODELS)}")
    parser.add_argument("--samples", type=Path, default=ROOT / "tools" / "samples.json")
    parser.add_argument("--limit", type=int, help="use only the first N samples")
    parser.add_argument("--effort", choices=EFFORT_LEVELS, help="default: BYS_EFFORT (low)")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "tools" / "reports")
    args = parser.parse_args(argv)

    unknown = [model for model in args.models if model not in PRICES_PER_MTOK]
    if unknown:
        print(f"error: no price listed for {', '.join(unknown)}; add it to PRICES_PER_MTOK first.", file=sys.stderr)
        return 2
    samples = load_samples(args.samples)[: args.limit] if args.limit else load_samples(args.samples)
    load_env_file(ROOT / ".env")
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    effort = args.effort or settings.effort

    costs = estimate(samples, args.models)
    print(f"Estimated cost for {len(samples)} samples x {len(args.models)} models = {len(samples) * len(args.models)} requests "
          f"(effort {effort}, rough upper estimate):", file=out)
    for model, cost in costs.items():
        print(f"  {model:<20} ~${cost:.4f}", file=out)
    print(f"  {'total':<20} ~${sum(costs.values()):.4f}", file=out)
    if not args.yes:
        print("Nothing was sent. Re-run with --yes to call the Anthropic API.", file=out)
        return 0
    if not settings.anthropic_api_key:
        print("error: ANTHROPIC_API_KEY is not set (environment or .env).", file=sys.stderr)
        return 2

    analyzer = analysis.Analyzer.from_settings(settings)
    jobs = [(sample, model) for model in args.models for sample in samples]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        outcomes = list(pool.map(lambda job: run_one(analyzer, job[0], job[1], effort), jobs))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    markdown_path = args.out_dir / f"compare-{stamp}.md"
    html_path = args.out_dir / f"compare-{stamp}.html"
    write_markdown(markdown_path, outcomes, args.models, samples, effort)
    write_html(html_path, outcomes, args.models, samples, effort)
    total = sum(outcome.cost for outcome in outcomes)
    errors = sum(1 for outcome in outcomes if outcome.error)
    print(f"Done in {time.monotonic() - started:.0f} s: actual cost ${total:.4f}, {errors} errors.", file=out)
    print(f"Reports: {markdown_path}\n         {html_path}", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

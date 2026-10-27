"""Build the free examples of the app (public/samples/<lang>.json) with the same analyses the app runs.

«Try now» and the «Try an example» buttons show these ready results instead of calling Claude, so an example never
uses up one of the analyses of a code. Each file holds the four example inputs (Spanish messages, as in the demo) and
their results, with the reading in that language and the ready-to-send replies in Spanish.

Run it again after changing the prompts or the example texts. It makes 4 paid calls to Claude per language (24 for
all, about 20 cents) and needs ANTHROPIC_API_KEY:

    python tools/build_samples.py                 # only says what it would do
    python tools/build_samples.py --yes           # builds the six files
    python tools/build_samples.py --yes --lang es # only one language
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.analysis import Analyzer  # noqa: E402
from backend.config import ConfigError, load_env_file, load_settings  # noqa: E402
from backend.i18n import SUPPORTED_LANGS  # noqa: E402

OUT_DIR = ROOT / "public" / "samples"

# The example messages, in Spanish: the app is presented first in the Canary Islands. The image example is the text
# of assets/chat_demo_final.png, the screenshot shown next to it.
INPUTS = {
    "decodeText": "No te preocupes, ya lo vemos más adelante :)",
    "voice": "Hola, perdona que no te haya contestado antes, esta semana voy fatal de trabajo. Lo del sábado mejor lo "
             "dejamos para otro día, ¿vale? Ya te digo algo.",
    "image": "Uf, esta semana voy fatal de trabajo… No te preocupes, ya lo vemos más adelante :)",
    "send": "Vale, pues ya me dirás tú, porque nunca entiendo lo que quieres",
}


def build_language(analyzer: Analyzer, lang: str) -> dict[str, Any]:
    """The four examples for one app language, shaped exactly like the answers of the app's endpoints."""
    text, _ = analyzer.decode_text(INPUTS["decodeText"], lang)
    text.update(source="text", input=INPUTS["decodeText"], lang=lang)
    voice, _ = analyzer.decode_text(INPUTS["voice"], lang)
    voice.update(source="voice", input=INPUTS["voice"], lang=lang, transcript=INPUTS["voice"])
    image, _ = analyzer.decode_text(INPUTS["image"], lang)
    image.update(source="image", input=INPUTS["image"], lang=lang, extracted_text=INPUTS["image"], extraction_confidence=100)
    send, _ = analyzer.score_text(INPUTS["send"], lang)
    send.update(input=INPUTS["send"], lang=lang)
    results = {"decodeText": text, "voice": voice, "image": image, "send": send}
    return {key: {"input": INPUTS[key], "result": result} for key, result in results.items()}


def write_language(lang: str, data: dict[str, Any], out_dir: Path = OUT_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{lang}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="run the paid calls and write the files")
    parser.add_argument("--lang", action="append", choices=SUPPORTED_LANGS, help="only this language (repeatable)")
    args = parser.parse_args(argv)
    langs = args.lang or list(SUPPORTED_LANGS)
    if not args.yes:
        print(f"Would make {4 * len(langs)} calls to Claude ({', '.join(langs)}) and write public/samples/<lang>.json.")
        print("Run again with --yes to build them.")
        return 0

    load_env_file(ROOT / ".env")
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    analyzer = Analyzer.from_settings(settings)
    if not analyzer.available:
        print("error: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2
    with ThreadPoolExecutor(max_workers=len(langs)) as pool:
        built = dict(zip(langs, pool.map(lambda lang: build_language(analyzer, lang), langs)))
    for lang in langs:
        print(f"{lang}: {write_language(lang, built[lang]).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

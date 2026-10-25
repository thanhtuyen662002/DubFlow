"""Translate short Chinese terms (trend keywords) to Vietnamese for the desktop.

``python -m engine.dubflow.translation.terms --model-root DIR TERM ...`` prints
one JSON line: ``{"ok": true, "items": ["…", …]}`` in input order, or
``{"ok": false, "code": "…", "message": "…"}``. ``--engine hachimi`` uses the
opt-in HachimiMT-60 model (much better on short keywords; the desktop only asks
for it after the user accepted its licence warning); the default is Argos.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .adapter import TranslationError
from .hachimi_runtime import create_runtime

MAX_TERMS = 60
MAX_TERM_CHARS = 80
APP_ROOT = Path(__file__).resolve().parents[3]


def _app_root_last() -> None:
    """Let the runtime's third-party ``packaging`` win over the app's own
    ``packaging/`` folder, as the worker does (``-m`` puts the cwd first)."""
    root = os.path.normcase(os.path.realpath(APP_ROOT))
    kept = [entry for entry in sys.path if os.path.normcase(os.path.realpath(entry or os.getcwd())) != root]
    sys.path[:] = kept + [os.fspath(APP_ROOT)]


def translate_terms(terms: list[str], model_root: Path, *, engine: str = "argos", runtime_factory=None) -> list[str]:
    runtime = (runtime_factory or (lambda app, models, language: create_runtime(engine, app, models, language)))(APP_ROOT, model_root, "zh")
    runtime.prepare(model_root)
    translated = []
    for term in terms:
        try:
            translated.append(runtime.translate_text(term))
        except TranslationError:
            translated.append("")  # One odd keyword must not hide the others.
    return translated


def main(argv: list[str] | None = None, *, stdout=None, runtime_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--engine", choices=("argos", "hachimi"), default="argos")
    parser.add_argument("terms", nargs="+")
    args = parser.parse_args(argv)
    _app_root_last()
    terms = [" ".join(term.split())[:MAX_TERM_CHARS] for term in args.terms[:MAX_TERMS]]
    try:
        result = {"ok": True, "items": translate_terms(terms, Path(args.model_root), engine=args.engine, runtime_factory=runtime_factory)}
    except TranslationError as error:
        result = {"ok": False, "code": error.code, "message": error.condition}
    except Exception as error:  # noqa: BLE001 - the host always needs one JSON line.
        result = {"ok": False, "code": "TRANSLATION_FAILED", "message": f"{type(error).__name__}: {str(error)[:200]}"}
    print(json.dumps(result, ensure_ascii=True), file=stdout or sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

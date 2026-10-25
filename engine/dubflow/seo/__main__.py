"""JSONL CLI for an exported editable/timeline.json, without model dependencies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .suggest import suggest_post


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suggest Vietnamese titles, a description and hashtags offline.")
    parser.add_argument("timeline", type=Path, help="DubFlow editable/timeline.json")
    parser.add_argument("--source-title", default="", help="Vietnamese translation of the source title")
    args = parser.parse_args(argv)

    try:
        # utf-8-sig also accepts plain UTF-8 and Windows editors' UTF-8 BOM.
        document = json.loads(args.timeline.read_text(encoding="utf-8-sig"))
    except OSError:
        result = {"ok": False, "code": "TIMELINE_READ_ERROR", "message": "Cannot read the timeline file."}
    except (UnicodeError, ValueError, RecursionError):
        result = {"ok": False, "code": "INVALID_JSON", "message": "Timeline must be a UTF-8 JSON document."}
    else:
        cues = document.get("cues") if isinstance(document, dict) else None
        if not isinstance(cues, list) or any(
            not isinstance(cue, dict)
            or (cue.get("translated_text") is not None and not isinstance(cue["translated_text"], str))
            for cue in cues
        ):
            result = {"ok": False, "code": "INVALID_TIMELINE", "message": "Expected a cues array of objects with string or null translated_text."}
        else:
            # Untranslated/null cues contribute no text; never substitute Chinese.
            result = {"ok": True, **suggest_post([cue.get("translated_text") or "" for cue in cues], source_title=args.source_title)}

    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

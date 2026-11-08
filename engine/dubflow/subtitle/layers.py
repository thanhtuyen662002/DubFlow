"""Tách phụ đề for the editor: a plain video's own words and their Vietnamese.

``python -B -m engine.dubflow.subtitle.layers --video V --out-dir D ...``

1. recognises the speech with the pinned faster-whisper model, chunked and
   resumable, exactly as a documentary source (``docu.transcribe``);
2. translates it to Vietnamese offline (Argos), then, when the user saved an
   AI key (``translation_settings.json`` in ``D``, the key in the
   environment), in context with that AI (``context_ai``), same as a dubbing
   job; the offline lines stand wherever the AI fails;
3. applies the user's sensitive words to the Vietnamese lines;
4. writes ``D/layers.json``: ``source`` (the original words) and ``vi`` lines
   with their times in the video, for the editor's two subtitle layers.

Progress goes to ``D/status.json`` (``state`` RUNNING, COMPLETED, FAILED or
CANCELLED, ``progress`` 0–100, ``message``); a ``D/cancel`` file stops it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Callable

LAYERS_FILE = "layers.json"
LANGUAGES = ("zh", "en")
LANGUAGE_NAMES = {"zh": "tiếng Trung", "en": "tiếng Anh"}


class LayersError(RuntimeError):
    pass


class Cancelled(LayersError):
    pass


def _settings(out_dir: Path, warnings: list[str]):
    from engine.dubflow.worker.production_job import _translation_settings

    class _Config:
        output_dir = out_dir

    return _translation_settings(_Config(), warnings)


def _cues(transcript: Path) -> list[Any]:
    from engine.dubflow.worker.production_job import TextCue

    document = json.loads(transcript.read_text(encoding="utf-8"))
    return [TextCue(str(cue["cue_id"]), int(cue["start_ms"]), int(cue["end_ms"]), str(cue["source_text"]).strip())
            for cue in document["cues"] if str(cue.get("source_text", "")).strip() and int(cue["end_ms"]) > int(cue["start_ms"])]


def _offline(cues: list[Any], *, app_root: Path, model_root: Path, language: str, work: Path) -> list[Any]:
    from engine.dubflow.worker.production_job import _translate_with_argos, create_translation_runtime

    runtime = create_translation_runtime("argos", app_root, model_root, language)
    return list(_translate_with_argos(cues, model_root, language, runtime=runtime, chunks_dir=work / "translation-chunks"))


def run(video: Path, out_dir: Path, *, app_root: Path, model_root: Path, ffmpeg: Path, language: str, status: Any,
        transcribe: Callable[..., Path] | None = None, offline: Callable[..., list[Any]] | None = None,
        in_context: Callable[..., Any] | None = None, settings: Callable[..., Any] | None = None) -> dict:
    if language not in LANGUAGES:
        raise LayersError("Ngôn ngữ gốc phải là tiếng Trung hoặc tiếng Anh")
    out_dir.mkdir(parents=True, exist_ok=True)
    cancel = out_dir / "cancel"
    warnings: list[str] = []

    def check() -> None:
        if cancel.exists():
            raise Cancelled("cancelled")

    # 1. The words, 2–60 %.
    if transcribe is None:
        from engine.dubflow.docu.transcribe import transcribe

    def heard(stage: str, done: int, total: int) -> None:
        check()
        if stage == "asr":
            status.write(force=False, progress=round(4 + 56 * done / max(1, total), 1), message=f"Đang nghe lời thoại ({done}/{total} đoạn)…")
    status.write(progress=2, message="Đang đọc âm thanh của video…")
    try:
        transcript = transcribe(video, out_dir, app_root=app_root, model_root=model_root, ffmpeg=ffmpeg, language=language, progress=heard)
    except Cancelled:
        raise
    except Exception as error:  # noqa: BLE001 - shown to the user as is (DocuError messages are Vietnamese).
        raise LayersError(str(error)) from error
    check()
    cues = _cues(transcript)
    if not cues:
        raise LayersError("Không nghe thấy lời nói nào trong video này")

    # 2. Offline Vietnamese, 60–70 %.
    status.write(progress=60, message="Đang dịch sang tiếng Việt…")
    try:
        translated = (offline or _offline)(cues, app_root=app_root, model_root=model_root, language=language, work=out_dir)
    except Exception as error:  # noqa: BLE001 - the AI pass below may still translate every line.
        warnings.append(f"OFFLINE_TRANSLATION_FAILED: {type(error).__name__}: {str(error)[:200]}")
        translated = list(cues)
    check()

    # 3. In context with the user's AI, 70–96 %.
    chosen = (settings or _settings)(out_dir, warnings)
    used_ai = None
    if chosen.route is not None:
        from engine.dubflow.translation import context_ai

        label = chosen.route.preset.label

        def ai_progress(done: int, total: int) -> None:
            check()
            status.write(force=False, progress=round(70 + 26 * done / max(1, total), 1), message=f"Đang dịch theo ngữ cảnh bằng {label} ({done}/{total} đoạn)…")
        status.write(progress=70, message=f"Đang dịch theo ngữ cảnh bằng {label}…")
        try:
            result = (in_context or context_ai.translate_in_context)(translated, chosen.route, dubbing=False, cache_dir=out_dir / "ai-translation",
                                                                   title=chosen.title, on_progress=ai_progress)
            translated = list(result.cues)
            used_ai = {"label": label, "lines": result.cues_from_ai, "error": result.error}
            if result.error and not result.cues_from_ai:
                warnings.append(f"AI_TRANSLATION_FAILED: {result.error}")
        except Cancelled:
            raise
        except Exception as error:  # noqa: BLE001 - the offline lines stand.
            warnings.append(f"AI_TRANSLATION_FAILED: {type(error).__name__}: {str(error)[:200]}")
    check()

    # 4. Sensitive words, then the two layers.
    lines_vi = []
    for cue in translated:
        text = (cue.translated_text or "").strip()
        if chosen.sensitive is not None and text:
            text = chosen.sensitive.subtitle(text)
        if text:
            lines_vi.append({"start_ms": cue.start_ms, "end_ms": cue.end_ms, "text": text})
    if not lines_vi:
        raise LayersError("Không dịch được câu nào sang tiếng Việt; hãy lưu khóa AI trong Cài đặt rồi thử lại")
    document = {
        "schema_version": 1, "language": language, "ai": used_ai, "warnings": warnings,
        "source": [{"start_ms": cue.start_ms, "end_ms": cue.end_ms, "text": cue.source_text} for cue in cues],
        "vi": lines_vi,
    }
    target = out_dir / LAYERS_FILE
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    by_ai = f", {used_ai['lines']} câu dịch bằng {used_ai['label']}" if used_ai and used_ai["lines"] else ""
    status.write(state="COMPLETED", progress=100, message=f"Đã tách {len(cues)} câu {LANGUAGE_NAMES[language]}{by_ai}.")
    return status.value


def main(argv: list[str] | None = None) -> int:
    from engine.dubflow.separation.__main__ import Status

    parser = argparse.ArgumentParser(prog="python -m engine.dubflow.subtitle.layers")
    for name in ("video", "out-dir", "app-root", "model-root", "ffmpeg"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--language", default="zh", choices=LANGUAGES)
    args = parser.parse_args(argv)
    status = Status(args.out_dir / "status.json")
    status.value["message"] = "Đang chuẩn bị tách phụ đề…"
    status.write()
    try:
        result = run(args.video, args.out_dir, app_root=args.app_root, model_root=args.model_root, ffmpeg=args.ffmpeg,
                     language=args.language, status=status)
    except Cancelled:
        status.write(state="CANCELLED", progress=0, message="Đã hủy tách phụ đề.")
        result = status.value
    except Exception as error:  # noqa: BLE001 - every failure becomes a readable status.
        detail = " ".join(str(error).split())[:300] or type(error).__name__
        status.write(state="FAILED", progress=0, message=f"Không tách được phụ đề: {detail}")
        result = status.value
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["state"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())

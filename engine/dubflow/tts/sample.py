"""``python -B -m engine.dubflow.tts.sample``: Nghe thử, one voice reading a short line.

Writes a mono 16-bit WAV of the voice at its natural pace (on the CPU: a
sample must not pull the GPU pack down) and prints ``{"ok": true}`` or
``{"ok": false, "message": …}`` as the last stdout line. The desktop keeps
the file per voice version, so each voice is made once.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SAMPLE_TEXT = "Xin chào, mình là giọng lồng tiếng của DubFlow. Bạn nghe thử rồi chọn giọng hợp với video nhé."


class _Quiet:
    def write(self, **_fields) -> None:
        pass


def make_sample(voice_id: str, out: Path, *, app_root: Path, model_root: Path, ffmpeg: Path, speaker_factory=None) -> None:
    from engine.dubflow.product.render import write_narration

    if speaker_factory is None:
        from engine.dubflow.product.speaker import VieNeuSpeaker

        def speaker_factory():
            return VieNeuSpeaker(app_root, model_root, ffmpeg, voice_id, cpu_only=True, status=_Quiet())
    speaker = speaker_factory()
    try:
        samples = speaker.speak(SAMPLE_TEXT)
        rate = speaker.sample_rate
    finally:
        speaker.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    write_narration(out, [samples], rate, lead_ms=120, gap_ms=0, tail_ms=300)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine.dubflow.tts.sample")
    parser.add_argument("--voice-id", required=True)
    for name in ("out", "app-root", "model-root", "ffmpeg"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        make_sample(args.voice_id, args.out, app_root=args.app_root, model_root=args.model_root, ffmpeg=args.ffmpeg)
        result = {"ok": True}
    except Exception as error:  # noqa: BLE001 - every failure is reported to the picker.
        code = getattr(error, "code", type(error).__name__)
        detail = " ".join(str(getattr(error, "condition", getattr(error, "message", error))).split())[:200]
        result = {"ok": False, "message": f"Không tạo được giọng mẫu ({code}: {detail})"}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

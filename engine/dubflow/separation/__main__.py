"""``python -B -m engine.dubflow.separation``: Tách giọng for the editor.

Separates one video's (or WAV's) sound into the accompaniment left once the
pinned MDX-Net vocal model's voice is removed (``background.wav``) beside an aligned PCM copy of the source
(``source.wav`` when the input is a video). The original voice is the source
minus the accompaniment, so the editor mixes "Giọng gốc", "Nhạc nền" and the
Vietnamese stem from these files without a third model output.

Progress goes to ``<out-dir>/status.json`` (``state`` RUNNING, COMPLETED or
FAILED, ``progress`` 0–100, ``message``, and ``backend`` once COMPLETED, so the
app offers to separate again files an older model made); the last stdout line
is that JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import time

from engine.dubflow.separation.production import SeparationError

PROFILE = "models/manifests/production-separation-mdx-v1.json"
SAMPLE_RATE = 48_000


class Status:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.value = {"schema_version": 1, "state": "RUNNING", "progress": 0, "message": "Đang chuẩn bị tách giọng…"}
        self._written = 0.0

    def write(self, *, force: bool = True, **fields) -> None:
        self.value.update(fields)
        now = time.monotonic()
        if not force and now - self._written < 0.5:
            return
        self._written = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".status-", suffix=".json", dir=self.path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(self.value, stream, ensure_ascii=True)
        os.replace(name, self.path)


def _cancelled(out_dir: Path) -> bool:
    return (out_dir / "cancel").exists()


def run(source: Path, out_dir: Path, *, app_root: Path, model_root: Path, ffmpeg: Path, ffprobe: Path,
        status: Status, separate=None, ensure=None, extract=None) -> dict:
    """Separate `source` into `out_dir`; returns the final status value."""
    from engine.dubflow.models.runtime import ensure_model_profile
    from engine.dubflow.separation import mdx
    from engine.dubflow.tts.neural_vits import _child, _read_json

    separate = separate or mdx.separate_background
    ensure = ensure or ensure_model_profile
    profile_path = app_root / PROFILE
    profile = _read_json(profile_path)
    model_dir = _child(model_root, profile["model_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    background = out_dir / "background.wav"
    if source.suffix.lower() == ".wav":
        source_wav = source
    else:
        source_wav = out_dir / "source.wav"
        status.write(progress=2, message="Đang đọc âm thanh của video…")
        (extract or _extract)(source, source_wav, ffmpeg=ffmpeg, ffprobe=ffprobe)
    if _cancelled(out_dir):
        return _stop(status)

    def downloading(_artifact, done, total):
        if total:
            status.write(force=False, progress=5, message=f"Đang tải bộ tách giọng lần đầu: {done // 1_000_000}/{total // 1_000_000} MB")
    status.write(progress=5, message="Đang chuẩn bị bộ tách giọng…")
    ensure(profile_path, model_root, progress=downloading)

    def progress(fraction):
        if _cancelled(out_dir):
            raise SeparationError("SEPARATION_CANCELLED", "cancelled")
        status.write(force=False, progress=round(8 + 82 * min(1.0, max(0.0, fraction)), 1),
                     message="Đang tách giọng gốc khỏi nhạc nền…")
    status.write(progress=8, message="Đang tách giọng gốc khỏi nhạc nền…")
    try:
        separate(source_wav, background, model_dir=model_dir, ffmpeg_path=ffmpeg, progress=progress)
    except SeparationError as error:
        if error.code == "SEPARATION_CANCELLED":
            return _stop(status)
        raise
    # The older Spleeter adapter's resumable batches are scratch nobody reads now.
    shutil.rmtree(out_dir / ".spleeter-chunks", ignore_errors=True)
    # Nhạc and SFX: the accompaniment in two parts (no model; a few seconds a minute).
    status.write(progress=90, message="Đang tách nhạc và tiếng động…")
    try:
        from engine.dubflow.separation import split as music_split

        music_split.split(background, out_dir, source=source_wav, cancelled=lambda: _cancelled(out_dir),
                          progress=lambda fraction: status.write(force=False, progress=round(90 + 9 * fraction, 1),
                                                                 message="Đang tách nhạc và tiếng động…"))
    except Exception as error:  # noqa: BLE001 - voice and accompaniment are still usable without the two parts.
        if _cancelled(out_dir):
            return _stop(status)
        # Partial success: the voice and accompaniment stand; the two parts can be split again on their own.
        detail = " ".join(str(error).split())[:200]
        status.write(state="COMPLETED", progress=100, backend=mdx.BACKEND, split_error=detail,
                     message=f"Đã tách giọng, nhưng chưa tách được nhạc và tiếng động ({detail}). Bấm Tách nhạc và tiếng động (SFX) để thử lại.")
        return status.value
    status.write(state="COMPLETED", progress=100, message="Đã tách giọng, nhạc và tiếng động.", backend=mdx.BACKEND)
    return status.value


def _stop(status: Status) -> dict:
    status.write(state="CANCELLED", progress=0, message="Đã hủy tách giọng.")
    return status.value


def _extract(source: Path, target: Path, *, ffmpeg: Path, ffprobe: Path) -> None:
    from engine.dubflow.media.adapter import FfmpegMediaAdapter, MediaProbe

    probe = MediaProbe(ffprobe, timeout_seconds=600).probe(source)
    if not probe.has_audio:
        raise SeparationError("SEPARATION_NO_AUDIO", "video has no sound")
    # Same alignment as the dubbing jobs: the WAV starts with the picture.
    FfmpegMediaAdapter(ffmpeg, timeout_seconds=6 * 3600).extract_audio(
        source, target, sample_rate=SAMPLE_RATE, channels=2, video_stream=probe.video, overwrite=True)


MESSAGES = {
    "SEPARATION_NO_AUDIO": "Video này không có âm thanh để tách.",
    "SEPARATION_REJECTED": "Bộ tách giọng cho kết quả không dùng được với âm thanh này.",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine.dubflow.separation")
    for name in ("source", "out-dir", "app-root", "model-root", "ffmpeg", "ffprobe"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    status = Status(args.out_dir / "status.json")
    status.write()
    try:
        result = run(args.source, args.out_dir, app_root=args.app_root, model_root=args.model_root,
                     ffmpeg=args.ffmpeg, ffprobe=args.ffprobe, status=status)
    except Exception as error:  # noqa: BLE001 - every failure becomes a readable status.
        code = getattr(error, "code", type(error).__name__)
        detail = " ".join(str(getattr(error, "condition", error)).split())[:200]
        message = MESSAGES.get(code) or f"Không tách được giọng ({code}: {detail})."
        status.write(state="FAILED", progress=0, message=message)
        result = status.value
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["state"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())

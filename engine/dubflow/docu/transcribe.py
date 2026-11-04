"""The source's own words for a documentary, straight from its video (ADR-0049).

A documentary source used to need either a finished dubbing job (a full
Vietnamese-subtitled render, 237 MB for a 5-minute video, only to get its
Chinese transcript) or an ``.srt``. This recognises the speech of a plain
video with the same pinned faster-whisper model and chunked, resumable ASR as
the dubbing pipeline, and writes a ``transcript.json`` the project reads like a
job's. Nothing else of the dubbing pipeline runs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Callable

from .project import DocuError, write_json

ASR_PROFILE = "models/manifests/production-cpu-v1.json"
RECIPE = "docu-transcribe-v1"
Progress = Callable[[str, int, int], None]


def _identity(video: Path, language: str) -> dict[str, Any]:
    stat = video.stat()
    return {"recipe": RECIPE, "video": str(video), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "language": language}


def transcribe(video: Path, out_dir: Path, *, app_root: Path, model_root: Path, ffmpeg: Path, language: str = "zh",
               progress: Progress | None = None, recognise: Callable[..., Any] | None = None) -> Path:
    """``out_dir/transcript.json`` with the video's speech as cues; reused while the video is unchanged."""
    if language not in ("zh", "en"):
        raise DocuError("Ngôn ngữ lời gốc phải là zh hoặc en")
    if not video.is_file():
        raise DocuError(f"Không tìm thấy video: {video.name}")
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript = out_dir / "transcript.json"
    identity = _identity(video, language)
    try:
        saved = json.loads(transcript.read_text(encoding="utf-8"))
        if saved.get("identity") == identity and saved.get("cues"):
            return transcript
    except (OSError, ValueError):
        pass
    if recognise is None:
        from engine.dubflow.models.runtime import ModelBootstrapError, ensure_model_profile
        from engine.dubflow.worker.production_job import ProductionJobError, _transcribe_with_faster_whisper

        try:
            ensure_model_profile(app_root / ASR_PROFILE, model_root)
        except ModelBootstrapError as error:
            raise DocuError("Không tải được model nhận dạng lời; hãy kiểm tra mạng rồi thử lại") from error

        def recognise(audio: Path, **kwargs: Any) -> Any:
            try:
                return _transcribe_with_faster_whisper(audio, model_root, language, **kwargs)
            except ProductionJobError as error:
                raise DocuError(f"Không nhận dạng được lời của {video.name}: {error}") from error
    audio = out_dir / "analysis.wav"
    if progress:
        progress("audio", 0, 1)
    result = subprocess.run([str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1",
                             "-ar", "16000", "-c:a", "pcm_s16le", str(audio)],
                            stdin=subprocess.DEVNULL, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or not audio.is_file():
        raise DocuError(f"Không tách được âm thanh của {video.name}")
    binding = "sha256:" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()

    def chunk(done: int, total: int, _chunk_id: str, _artifact: str) -> None:
        if progress:
            progress("asr", done, total)
    found = recognise(audio, chunks_dir=out_dir / "asr-chunks", model_binding=binding, on_chunk=chunk)
    cues = [{"cue_id": cue.cue_id, "start_ms": cue.start_ms, "end_ms": cue.end_ms, "source_text": cue.source_text}
            for cue in found.cues if cue.source_text.strip()]
    if not cues:
        raise DocuError(f"Không nghe thấy lời nói nào trong {video.name}; hãy chọn video có lời thuyết minh hoặc kèm phụ đề .srt")
    write_json(transcript, {"schema_version": 1, "source": "faster-whisper", "language": language, "identity": identity, "cues": cues})
    audio.unlink(missing_ok=True)
    return transcript

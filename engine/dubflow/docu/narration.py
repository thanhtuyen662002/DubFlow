"""The master narration: one voice, sentence by sentence, timed to the sample (ADR-0049, phase 2a).

Every sentence is synthesised once and cached by its text, voice and speed, so
a cancelled or edited run only speaks what changed. The pieces are joined with
fixed pauses into one PCM track whose sample positions are the timeline;
loudness normalisation afterwards is linear and keeps every time.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterable, Protocol, Sequence
import wave

from engine.dubflow.product.job import Cancelled, ProductError, run_ffmpeg
from .project import DocuError, work_dir, write_json
from .script import CHAPTERS, script_sha256

SENTENCE_PAUSE = 0.4
SCENE_PAUSE = 0.8
# Longer, so the music can rise between acts (phase 2b).
CHAPTER_PAUSE = 1.6
# Between the pieces of a sentence too long for one request.
CLAUSE_PAUSE = 0.12
MAX_PIECE = 280
TARGET_LUFS = -16.0
TRUE_PEAK = -1.5
OUTPUT_RATE = 48_000
CAPTION_CHARS = 84
CAPTION_LINE = 42
# A period after these does not end a sentence.
_ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "dr", "st", "mt", "vs", "approx", "no", "e.g", "i.e", "u.s", "u.k", "jr", "sr", "inc", "co", "corp", "ft", "lb"})
_BOUNDARY = re.compile(r"[.!?…][\"'”’)\]]*(\s+)(?=[\"'“‘(\[]?[A-Z0-9$])")


class Speaker(Protocol):
    sample_rate: int

    def speak(self, text: str) -> Sequence[float]: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Piece:
    """One synthesis request and the pause after it."""

    chapter: str
    scene_id: str
    sentence: int
    text: str
    pause: float


def sentences(text: str) -> list[str]:
    """English sentences; "U.S." and "Dr." do not end one."""
    text = re.sub(r"\s+", " ", text).strip()
    found, start = [], 0
    for match in _BOUNDARY.finditer(text):
        end = match.start(1)
        word = re.search(r"([A-Za-z.]+)[.!?…][\"'”’)\]]*$", text[start:end])
        if word and word.group(1).casefold().rstrip(".") in _ABBREVIATIONS:
            continue
        found.append(text[start:end].strip())
        start = match.end()
    found.append(text[start:].strip())
    return [sentence for sentence in found if sentence]


def _pieces_of(sentence: str) -> list[str]:
    """Split a very long sentence at clause marks, then at spaces."""
    if len(sentence) <= MAX_PIECE:
        return [sentence]
    parts, current = [], ""
    for clause in re.split(r"(?<=[,;:—])\s+", sentence):
        if current and len(current) + 1 + len(clause) > MAX_PIECE:
            parts.append(current)
            current = clause
        else:
            current = f"{current} {clause}".strip()
    parts.append(current)
    result = []
    for part in parts:
        while len(part) > MAX_PIECE:
            cut = part.rfind(" ", 0, MAX_PIECE)
            cut = cut if cut > 0 else MAX_PIECE
            result.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            result.append(part)
    return result


def plan(script: dict[str, Any]) -> list[Piece]:
    pieces: list[Piece] = []
    chapters = [chapter for chapter in script.get("chapters", []) if chapter.get("chapter_type") in CHAPTERS]
    for chapter_index, chapter in enumerate(chapters):
        scenes = [scene for scene in chapter.get("scenes", []) if str(scene.get("narration_text", "")).strip()]
        for scene_index, scene in enumerate(scenes):
            spoken = sentences(scene["narration_text"])
            for sentence_index, sentence in enumerate(spoken):
                parts = _pieces_of(sentence)
                for part_index, part in enumerate(parts):
                    if part_index < len(parts) - 1:
                        pause = CLAUSE_PAUSE
                    elif sentence_index < len(spoken) - 1:
                        pause = SENTENCE_PAUSE
                    elif scene_index < len(scenes) - 1:
                        pause = SCENE_PAUSE
                    elif chapter_index < len(chapters) - 1:
                        pause = CHAPTER_PAUSE
                    else:
                        pause = 0.0
                    pieces.append(Piece(chapter["chapter_type"], scene["scene_id"], sentence_index, part, pause))
    if not pieces:
        raise DocuError("Kịch bản chưa có lời đọc")
    return pieces


def _pcm(samples: Iterable[float]) -> bytes:
    values = array("h", (max(-32768, min(32767, round(sample * 32767))) for sample in samples))
    if values.itemsize != 2:  # pragma: no cover - every supported platform has 16-bit shorts
        raise DocuError("PCM 16-bit không khả dụng")
    return values.tobytes()


class _Status:
    def __init__(self, path: Path, total: int) -> None:
        self.path = path
        self.value: dict[str, Any] = {"schema_version": 1, "state": "RUNNING", "done": 0, "total": total,
                                      "message": "Đang chuẩn bị giọng đọc tiếng Anh…"}
        self.write()

    def write(self, **changes: Any) -> None:
        self.value.update(changes)
        write_json(self.path, self.value)


def _srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _wrap(text: str) -> str:
    if len(text) <= CAPTION_LINE:
        return text
    cut = text.rfind(" ", 0, len(text) // 2 + 8)
    cut = cut if cut > 0 else text.find(" ")
    return text if cut < 0 else f"{text[:cut]}\n{text[cut + 1:]}"


def captions(scenes: Sequence[dict[str, Any]]) -> str:
    """Sentence captions of at most two 42-character lines, timed by their share of the sentence."""
    cues: list[tuple[float, float, str]] = []
    for scene in scenes:
        for sentence in scene["sentences"]:
            words = sentence["text"].split()
            # Balanced chunks, so no caption is a lone trailing word.
            size = math.ceil(len(sentence["text"]) / math.ceil(len(sentence["text"]) / CAPTION_CHARS))
            chunks, current = [], ""
            for word in words:
                if current and len(current) + 1 + len(word) > size + 8:
                    chunks.append(current)
                    current = word
                else:
                    current = f"{current} {word}".strip()
            chunks.append(current)
            total = sum(len(chunk) for chunk in chunks) or 1
            start, span = sentence["start"], sentence["end"] - sentence["start"]
            for chunk in chunks:
                end = start + span * len(chunk) / total
                cues.append((start, end, _wrap(chunk)))
                start = end
    return "".join(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n\n" for index, (start, end, text) in enumerate(cues, 1))


def _loudness(ffmpeg: Path, source: Path, work: Path, cancelled: Callable[[], bool]) -> dict[str, Any]:
    """First pass of EBU R128 normalisation: the measured values for the linear second pass."""
    run_ffmpeg([str(ffmpeg), "-hide_banner", "-nostats", "-i", str(source), "-af",
                f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA=11:print_format=json", "-f", "null", "-"], work, cancelled)
    text = (work / "ffmpeg-error.log").read_text(encoding="utf-8", errors="replace")
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", text)
    if not match:
        raise DocuError("Không đo được độ lớn lời đọc")
    return json.loads(match.group(0))


def run(run_dir: Path, script: dict[str, Any], *, speaker: Callable[[Callable[[str], None]], Speaker], voice_id: str, speed: float,
        ffmpeg: Path, producer: str = "", now: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Speak the script into ``narration.wav``, ``master_audio_timeline.json`` and ``captions_en.srt``."""
    work = work_dir(run_dir) / "narration"
    cache = work / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    cancel = work / "cancel"
    cancel.unlink(missing_ok=True)
    pieces = plan(script)
    status = _Status(work / "status.json", len(pieces))
    cancelled = cancel.exists
    voice: Speaker | None = None
    try:
        audio: list[Path] = []
        last = 0.0
        for index, piece in enumerate(pieces):
            if cancelled():
                raise Cancelled()
            key = hashlib.sha256(json.dumps([producer, voice_id, speed, piece.text], ensure_ascii=False).encode("utf-8")).hexdigest()
            path = cache / f"{key}.wav"
            if not path.is_file():
                if voice is None:
                    voice = speaker(lambda message: status.write(message=message))
                samples = voice.speak(piece.text)
                if not samples:
                    raise DocuError(f"Không đọc được câu: {piece.text[:60]}")
                partial = path.with_suffix(".partial")
                with wave.open(str(partial), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(voice.sample_rate)
                    handle.writeframes(_pcm(samples))
                partial.replace(path)
            audio.append(path)
            if now() - last >= 0.5 or index == len(pieces) - 1:
                last = now()
                status.write(done=index + 1, message=f"Đang đọc câu {index + 1}/{len(pieces)}")
        raw = work / "narration-raw.wav"
        scenes, chapters, position = _join(pieces, audio, raw)
        with wave.open(str(raw), "rb") as handle:
            rate = handle.getframerate()
        status.write(message="Đang chuẩn hóa âm lượng lời đọc…")
        measured = _loudness(ffmpeg, raw, work, cancelled)
        output = run_dir / "narration.wav"
        partial = work / "narration.partial.wav"
        normalise = (f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA=11:measured_I={measured['input_i']}:"
                     f"measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}:"
                     f"measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}:linear=true")
        run_ffmpeg([str(ffmpeg), "-hide_banner", "-nostats", "-y", "-i", str(raw), "-af", normalise,
                    "-ar", str(OUTPUT_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(partial)], work, cancelled)
        partial.replace(output)
        duration = position / rate
        timeline = {"schema_version": 1, "script_sha256": script_sha256(script), "voice_id": voice_id, "speed": speed,
                    "producer": producer, "sample_rate": OUTPUT_RATE, "duration": round(duration, 3),
                    "loudness": {"target_lufs": TARGET_LUFS, "true_peak_db": TRUE_PEAK, "measured_lufs": _number(measured["input_i"])},
                    "pauses": {"sentence": SENTENCE_PAUSE, "scene": SCENE_PAUSE, "chapter": CHAPTER_PAUSE},
                    "chapters": chapters, "scenes": scenes}
        write_json(run_dir / "master_audio_timeline.json", timeline)
        (run_dir / "captions_en.srt").write_text(captions(scenes), encoding="utf-8", newline="\n")
        raw.unlink(missing_ok=True)
        status.write(state="COMPLETED", done=len(pieces), message="Đã tạo lời đọc", duration=timeline["duration"])
        return status.value
    except Cancelled:
        status.write(state="CANCELLED", message="Đã hủy tạo lời đọc")
        return status.value
    except (DocuError, ProductError) as error:
        status.write(state="FAILED", message=getattr(error, "message", str(error)))
        return status.value
    finally:
        if voice is not None:
            voice.close()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if math.isfinite(number) else None


def _join(pieces: Sequence[Piece], audio: Sequence[Path], output: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Concatenate the cached pieces with their pauses; times come from sample counts."""
    scenes: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    position = 0
    rate = 0
    with wave.open(str(output), "wb") as out:
        for piece, path in zip(pieces, audio):
            with wave.open(str(path), "rb") as handle:
                if rate == 0:
                    rate = handle.getframerate()
                    out.setnchannels(1)
                    out.setsampwidth(2)
                    out.setframerate(rate)
                if handle.getframerate() != rate or handle.getnchannels() != 1 or handle.getsampwidth() != 2:
                    raise DocuError("Các câu đọc không cùng định dạng; hãy xóa bộ đệm lời đọc rồi thử lại")
                frames = handle.readframes(handle.getnframes())
            start = position
            out.writeframes(frames)
            position += len(frames) // 2
            if not chapters or chapters[-1]["chapter_type"] != piece.chapter:
                chapters.append({"chapter_type": piece.chapter, "start": start / rate})
            if not scenes or scenes[-1]["scene_id"] != piece.scene_id:
                scenes.append({"scene_id": piece.scene_id, "chapter_type": piece.chapter, "start": start / rate, "sentences": []})
            sentence_list = scenes[-1]["sentences"]
            if sentence_list and sentence_list[-1]["index"] == piece.sentence:
                sentence_list[-1]["text"] += " " + piece.text
                sentence_list[-1]["end"] = position / rate
            else:
                sentence_list.append({"index": piece.sentence, "text": piece.text, "start": start / rate, "end": position / rate})
            scenes[-1]["end"] = position / rate
            chapters[-1]["end"] = position / rate
            pause = round(piece.pause * rate)
            out.writeframes(b"\x00\x00" * pause)
            position += pause
    for index, scene in enumerate(scenes):
        slot_end = scenes[index + 1]["start"] if index + 1 < len(scenes) else position / rate
        scene["slot_end"] = slot_end
        scene["exact_duration_ms"] = round((scene["end"] - scene["start"]) * 1000)
        for sentence in scene["sentences"]:
            sentence.pop("index")
    for index, chapter in enumerate(chapters):
        chapter["slot_end"] = chapters[index + 1]["start"] if index + 1 < len(chapters) else position / rate
    _round(scenes)
    _round(chapters)
    return scenes, chapters, position


def _round(items: list[dict[str, Any]]) -> None:
    for item in items:
        for key in ("start", "end", "slot_end"):
            item[key] = round(item[key], 3)
        for sentence in item.get("sentences", []):
            sentence["start"], sentence["end"] = round(sentence["start"], 3), round(sentence["end"], 3)

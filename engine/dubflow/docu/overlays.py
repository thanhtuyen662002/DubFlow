"""What the rough cut draws over the picture and plays under the narration (ADR-0049).

Before this, ``documentary.mp4`` carried only B-roll and the voice: the script's
number cards and lower-thirds, the English captions and the chapter moods were
used only by the editor, so the "draft" had none of the things a documentary
viewer expects. The options below are on by default except burned captions
(YouTube takes ``captions_en.srt`` as a caption track, which viewers can turn off).

* graphics: METRIC_CALLOUT cards (top right) and LOWER_THIRD bars (bottom left)
  while their scene is narrated;
* captions: ``captions_en.srt`` burned at the bottom, above a letterbox bar;
* music: a quiet synthesized bed per chapter mood (no third-party audio, so no
  licence to clear), or the user's own track, ducked under the narration.

Overlays are cut per slice like the labels, so a re-render still reuses every
slice whose picture and overlay did not change.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Sequence

from .context import parse_srt
from .project import DocuError, work_dir, write_json

OPTIONS = "render_options.json"
DEFAULTS: dict[str, Any] = {"graphics": True, "captions": False, "music": "generated", "music_path": None}
MUSIC_CHOICES = ("generated", "file", "none")
CALLOUT_SECONDS = 6.0
LOWER_THIRD_SECONDS = 5.0
FADE_MS = 250
WIDTH, HEIGHT = 1920, 1080
# Loudness of the bed before ducking; the narration sits at -16 LUFS.
BED_LUFS = -30.0
MUSIC_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus")


def load_options(run_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads((work_dir(run_dir) / OPTIONS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    options = dict(DEFAULTS)
    if isinstance(value, dict):
        for key in ("graphics", "captions"):
            if isinstance(value.get(key), bool):
                options[key] = value[key]
        if value.get("music") in MUSIC_CHOICES:
            options["music"] = value["music"]
        if isinstance(value.get("music_path"), str) and value["music_path"].strip():
            options["music_path"] = value["music_path"]
    if options["music"] == "file" and not options["music_path"]:
        options["music"] = "generated"
    return options


def save_options(run_dir: Path, *, graphics: bool | None = None, captions: bool | None = None, music: str | None = None,
                 music_path: str | None = None) -> dict[str, Any]:
    options = load_options(run_dir)
    for key, value in (("graphics", graphics), ("captions", captions)):
        if value is not None:
            if not isinstance(value, bool):
                raise DocuError(f"{key} phải là true hoặc false")
            options[key] = value
    if music_path is not None:
        path = Path(music_path)
        if not path.is_absolute() or path.suffix.casefold() not in MUSIC_SUFFIXES or not path.is_file():
            raise DocuError("Nhạc nền cần là file MP3, WAV, M4A, AAC, FLAC, OGG hoặc OPUS có đường dẫn đầy đủ")
        options["music_path"], options["music"] = str(path), "file"
    if music is not None:
        if music not in MUSIC_CHOICES:
            raise DocuError("music phải là generated, file hoặc none")
        if music == "file" and not options["music_path"]:
            raise DocuError("Chọn file nhạc (music_path) trước")
        options["music"] = music
    write_json(work_dir(run_dir) / OPTIONS, {"schema_version": 1, **options})
    return options


@dataclass(frozen=True)
class Event:
    start: float
    end: float
    style: str
    text: str
    # \pos in the 1920x1080 frame, or None to place by style alignment and margins.
    pos: tuple[int, int] | None = None


def _ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def graphic_events(script: dict[str, Any], timeline: dict[str, Any], bar: int) -> list[Event]:
    """The script's on-screen graphics while their scene is narrated; inside the picture when there are letterbox bars."""
    times = {scene["scene_id"]: scene for scene in timeline.get("scenes", [])}
    events: list[Event] = []
    for chapter in script.get("chapters", []):
        for scene in chapter.get("scenes", []):
            graphic, timed = scene.get("on_screen_graphic"), times.get(scene.get("scene_id"))
            if not isinstance(graphic, dict) or not timed:
                continue
            title, value, subtext = (str(graphic.get(key) or "").strip() for key in ("title", "value", "subtext"))
            begin = float(timed["start"])
            stop = float(timed.get("slot_end") or timed["end"])
            if graphic.get("type") == "LOWER_THIRD":
                start, end = begin + 0.4, min(stop, begin + 0.4 + LOWER_THIRD_SECONDS)
                y = HEIGHT - bar - 120
                if title:
                    events.append(Event(start, end, "ThirdTitle", title, (110, y - (52 if value else 0))))
                if value:
                    events.append(Event(start, end, "ThirdValue", value, (110, y)))
            elif value or title:
                start, end = begin + 0.6, min(stop, begin + 0.6 + CALLOUT_SECONDS)
                y = bar + 70
                for style, words in (("CardTitle", title.upper()), ("CardValue", value), ("CardSub", subtext)):
                    if words:
                        events.append(Event(start, end, style, words, (WIDTH - 110, y)))
                        y += {"CardTitle": 54, "CardValue": 104, "CardSub": 50}[style]
    # A scene too short to read a card in gets none.
    return [event for event in events if event.end - event.start >= 1.0]


def caption_events(srt: str) -> list[Event]:
    return [Event(start / 1000, end / 1000, "Caption", text) for start, end, text in parse_srt(srt)]


def all_events(run_dir: Path, script: dict[str, Any], timeline: dict[str, Any], options: dict[str, Any], bar: int) -> list[Event]:
    events = graphic_events(script, timeline, bar) if options["graphics"] else []
    if options["captions"]:
        try:
            events += caption_events((run_dir / "captions_en.srt").read_text(encoding="utf-8"))
        except OSError:
            pass
    return events


def _clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds // 3600)}:{int(seconds // 60) % 60:02d}:{seconds % 60:05.2f}"


def styles(bar: int) -> str:
    # Colours are &HAABBGGRR. Slate boxes like the labels, a cyan accent for the card title.
    caption_margin = bar + 46
    return ("Style: CardTitle,Segoe UI,34,&H00F5D622,&H00000000,&H1A2A170F,1,0,3,16,0,9,0,0,0\n"
            "Style: CardValue,Segoe UI,84,&H00FFFFFF,&H00000000,&H1A2A170F,1,0,3,18,0,9,0,0,0\n"
            "Style: CardSub,Segoe UI,32,&H00DDDDDD,&H00000000,&H1A2A170F,0,0,3,14,0,9,0,0,0\n"
            "Style: ThirdTitle,Segoe UI,44,&H00FFFFFF,&H00000000,&H1A2A170F,1,0,3,16,0,1,0,0,0\n"
            "Style: ThirdValue,Segoe UI,34,&H00F5D622,&H00000000,&H1A2A170F,0,0,3,14,0,1,0,0,0\n"
            f"Style: Caption,Segoe UI,46,&H00FFFFFF,&H00000000,&H80000000,1,0,1,3,1,2,160,160,{caption_margin}\n")


def dialogue(event: Event, *, fade_in: bool, fade_out: bool) -> str:
    tags = ""
    if event.pos:
        tags += f"\\pos({event.pos[0]},{event.pos[1]})"
    if fade_in or fade_out:
        tags += f"\\fad({FADE_MS if fade_in else 0},{FADE_MS if fade_out else 0})"
    text = ("{" + tags + "}" if tags else "") + _ass_text(event.text)
    return f"Dialogue: 1,{_clock(event.start)},{_clock(event.end)},{event.style},,0,0,0,,{text}\n"


def overlay_rows(events: Sequence[Event], at: float, seconds: float) -> list[str]:
    """ASS Dialogue rows for one slice; a fade only where the overlay really starts or ends, not at a cut."""
    rows = []
    for event in events:
        if event.start >= at + seconds - 1e-3 or event.end <= at + 1e-3:
            continue
        local = Event(max(event.start, at) - at, min(event.end, at + seconds) - at, event.style, event.text, event.pos)
        rows.append(dialogue(local, fade_in=event.start >= at - 1e-3, fade_out=event.end <= at + seconds + 1e-3))
    return rows


# ---- music bed -------------------------------------------------------------------------------------------------

# Chord tones (Hz) per mood; slow swells keep them from sounding like test tones.
MOOD_CHORDS: dict[str, tuple[float, ...]] = {
    "cinematic_tension_drone": (55.0, 82.41, 110.0, 164.81),
    "epic_build": (73.42, 110.0, 146.83, 185.0, 220.0),
    "curious_pulse": (130.81, 196.0, 261.63, 329.63),
    "reflective_ambient": (87.31, 130.81, 220.0, 329.63),
    "hopeful_rise": (98.0, 146.83, 246.94, 293.66),
    "dark_suspense": (65.41, 92.5, 130.81, 185.0),
}
PULSED = {"curious_pulse": 2.0, "epic_build": 1.0}


def mood_expression(mood: str) -> str:
    """An aevalsrc expression: detuned sine pairs with slow, out-of-phase swells (and a soft pulse for some moods)."""
    chord = MOOD_CHORDS.get(mood, MOOD_CHORDS["reflective_ambient"])
    voices = []
    for index, frequency in enumerate(chord):
        swell = f"(0.55+0.45*sin(2*PI*{0.05 + index * 0.031:.3f}*t+{index * 1.7:.1f}))"
        voices.append(f"{swell}*(sin(2*PI*{frequency:.2f}*t)+0.6*sin(2*PI*{frequency * 1.003:.3f}*t))")
    body = "+".join(voices)
    gain = 0.5 / len(chord)
    pulse = PULSED.get(mood)
    if pulse:
        body = f"({body})*(0.7+0.3*sin(2*PI*{pulse:g}*t))"
    return f"{gain:.4f}*({body})"


def chapter_moods(script: dict[str, Any], timeline: dict[str, Any], total: float) -> list[tuple[str, float, float]]:
    moods = {chapter.get("chapter_type"): chapter.get("recommended_bgm_mood") for chapter in script.get("chapters", [])}
    spans = []
    for chapter in timeline.get("chapters", []):
        start = float(chapter.get("start") or 0.0)
        end = float(chapter.get("slot_end") or chapter.get("end") or total)
        spans.append((str(moods.get(chapter.get("chapter_type")) or "reflective_ambient"), start, max(start, end)))
    if spans:
        spans[0] = (spans[0][0], 0.0, spans[0][2])
        spans[-1] = (spans[-1][0], spans[-1][1], total)
    return [span for span in spans if span[2] - span[1] >= 1.0] or [("reflective_ambient", 0.0, total)]


def bed_argv(ffmpeg: Path, spans: Sequence[tuple[str, float, float]], total: float, output: Path) -> list[str]:
    """One WAV under the whole film: each chapter's mood crossfading into the next."""
    argv = [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y"]
    graph = []
    for index, (mood, start, end) in enumerate(spans):
        length = end - start + (2.0 if index < len(spans) - 1 else 0.0)
        argv += ["-f", "lavfi", "-i", f"aevalsrc=exprs='{mood_expression(mood)}':s=48000:c=stereo:d={length:.3f}"]
        fade = min(2.0, length / 3)
        graph.append(f"[{index}:a]afade=t=in:d={fade:.3f},afade=t=out:st={length - fade:.3f}:d={fade:.3f},"
                     f"adelay={int(round(start * 1000))}:all=1[m{index}]")
    mixed = "".join(f"[m{index}]" for index in range(len(spans)))
    graph.append(f"{mixed}amix=inputs={len(spans)}:normalize=0:duration=longest,lowpass=f=1800,"
                 f"aecho=0.8:0.6:90|170:0.25|0.15,atrim=0:{total:.3f},loudnorm=I={BED_LUFS:g}:TP=-8:LRA=6[bed]")
    return argv + ["-filter_complex", ";".join(graph), "-map", "[bed]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output)]


def file_bed_argv(ffmpeg: Path, music: Path, total: float, output: Path) -> list[str]:
    """The user's track, looped to the film's length and faded out."""
    fade = min(4.0, total / 4)
    return [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y", "-stream_loop", "-1", "-i", str(music),
            "-filter_complex", f"[0:a]atrim=0:{total:.3f},afade=t=out:st={max(0.0, total - fade):.3f}:d={fade:.3f},"
            f"loudnorm=I={BED_LUFS:g}:TP=-8:LRA=6[bed]", "-map", "[bed]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output)]


def ducked_mix() -> str:
    """Narration over the bed, the bed pulled down while the narrator speaks."""
    return ("[1:a]apad,asplit=2[voice][key];[2:a][key]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=450:makeup=1[bed];"
            "[voice][bed]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95[audio]")


def describe(options: dict[str, Any]) -> dict[str, Any]:
    music = options["music"]
    return {"graphics": options["graphics"], "captions": options["captions"], "music": music,
            "music_path": options["music_path"] if music == "file" else None}


"""What the rough cut draws over the picture and plays under the narration (ADR-0049).

Before this, ``documentary.mp4`` carried only B-roll and the voice: the script's
number cards and lower-thirds, the English captions and the chapter moods were
used only by the editor, so the "draft" had none of the things a documentary
viewer expects. The options below are on by default except burned captions
(YouTube takes ``captions_en.srt`` as a caption track, which viewers can turn off).

* graphics: METRIC_CALLOUT cards (top right, a yellow accent bar beside them,
  sliding in) and LOWER_THIRD bars (bottom left) while their scene is
  narrated. The hook's first number and any number compared with a US landmark
  ("≈ 150 American football fields") get the big "hero" callout instead: a
  large value at the left, after an accent bar, at most one per act;
* captions: ``captions_en.srt`` burned at the bottom, above a letterbox bar;
* music: a quiet synthesized bed per chapter mood (no third-party audio, so no
  licence to clear), or the user's own track, ducked under the narration;
* sfx: each scene's ambient sound and a hit between acts (``soundscape``).

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
from .units import BENCHMARKS

OPTIONS = "render_options.json"
DEFAULTS: dict[str, Any] = {"graphics": True, "captions": False, "music": "generated", "music_path": None, "sfx": True}
MUSIC_CHOICES = ("generated", "file", "none")
CALLOUT_SECONDS = 6.0
LOWER_THIRD_SECONDS = 5.0
FADE_MS = 250
WIDTH, HEIGHT = 1920, 1080
# Loudness of the bed before ducking; the narration sits at -16 LUFS. Ducking leaves it about 10 dB under the voice.
BED_LUFS = -20.0
MUSIC_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus")


def load_options(run_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads((work_dir(run_dir) / OPTIONS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    options = dict(DEFAULTS)
    if isinstance(value, dict):
        for key in ("graphics", "captions", "sfx"):
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
                 music_path: str | None = None, sfx: bool | None = None) -> dict[str, Any]:
    options = load_options(run_dir)
    for key, value in (("graphics", graphics), ("captions", captions), ("sfx", sfx)):
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
    # Slides in from this many pixels to the side (negative: from the left) where the event really starts.
    slide: int = 0


SLIDE_MS = 350
CARD_SLIDE = 70
HERO_SLIDE = -90
ACCENT_WIDTH = 8
# Landmark names (any alias) that make a callout a hero.
_LANDMARKS = tuple(alias.casefold() for benchmark in BENCHMARKS for alias in benchmark.aliases)


def is_hero(graphic: dict[str, Any], chapter_type: str, first_in_hook: bool) -> bool:
    """The hook's first number card, or a card that compares its number with a US landmark."""
    if graphic.get("type") == "LOWER_THIRD":
        return False
    words = " ".join(str(graphic.get(key) or "") for key in ("title", "value", "subtext")).casefold()
    return (chapter_type == "HOOK" and first_in_hook) or any(name in words for name in _LANDMARKS)


def _bar(height: int) -> str:
    """An ASS vector drawing: the accent bar, ``height`` tall, its top-left corner at the event's position."""
    return f"{{\\p1}}m 0 0 l {ACCENT_WIDTH} 0 {ACCENT_WIDTH} {height} 0 {height}{{\\p0}}"



def _ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def graphic_events(script: dict[str, Any], timeline: dict[str, Any], bar: int, skip: frozenset[str] = frozenset()) -> list[Event]:
    """The script's on-screen graphics while their scene is narrated; inside the picture when there are letterbox bars.

    Scenes in ``skip`` show their figure as a full-screen data slide (Tier-3), so their card is not drawn."""
    times = {scene["scene_id"]: scene for scene in timeline.get("scenes", [])}
    events: list[Event] = []
    for chapter in script.get("chapters", []):
        kind = str(chapter.get("chapter_type") or "")
        cards = hero_done = 0
        for scene in chapter.get("scenes", []):
            graphic, timed = scene.get("on_screen_graphic"), times.get(scene.get("scene_id"))
            if not isinstance(graphic, dict) or not timed or (scene.get("scene_id") in skip and graphic.get("type") != "LOWER_THIRD"):
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
                hero = not hero_done and is_hero(graphic, kind, cards == 0)
                cards += 1
                if hero:
                    hero_done = 1
                    # Large, at the left, under the frame's middle: title, value, comparison, after a tall accent bar.
                    x, y = 170, max(bar + 70, 520 - (60 if subtext else 0))
                    rows = [(style, words) for style, words in (("HeroTitle", title.upper()), ("HeroValue", value), ("HeroSub", subtext)) if words]
                    height = sum({"HeroTitle": 56, "HeroValue": 150, "HeroSub": 54}[style] for style, _ in rows)
                    events.append(Event(start, end, "Accent", _bar(height - 14), (x - 34, y + 6), HERO_SLIDE))
                    for style, words in rows:
                        events.append(Event(start, end, style, words, (x, y), HERO_SLIDE))
                        y += {"HeroTitle": 56, "HeroValue": 150, "HeroSub": 54}[style]
                    continue
                x, y = WIDTH - 110, bar + 70
                rows = [(style, words) for style, words in (("CardTitle", title.upper()), ("CardValue", value), ("CardSub", subtext)) if words]
                height = sum({"CardTitle": 54, "CardValue": 104, "CardSub": 50}[style] for style, _ in rows)
                # The accent bar just right of the cards' boxes, as tall as all of them.
                events.append(Event(start, end, "Accent", _bar(height + 10), (x + 18, y - 16), CARD_SLIDE))
                for style, words in rows:
                    events.append(Event(start, end, style, words, (x, y), CARD_SLIDE))
                    y += {"CardTitle": 54, "CardValue": 104, "CardSub": 50}[style]
    # A scene too short to read a card in gets none.
    return [event for event in events if event.end - event.start >= 1.0]


def caption_events(srt: str) -> list[Event]:
    return [Event(start / 1000, end / 1000, "Caption", text) for start, end, text in parse_srt(srt)]


def hero_starts(events: Sequence[Event]) -> list[float]:
    """When each hero card slides in (its accent bar's start)."""
    return sorted({round(event.start, 3) for event in events if event.style == "Accent" and event.slide == HERO_SLIDE})


def clear_spans(events: Sequence[Event], spans: Sequence[tuple[float, float]]) -> list[Event]:
    """Cards and lower-thirds kept off full-screen graphics (the map, a data slide): cut short before one, or held
    back until after it; captions stay."""
    kept = []
    for event in events:
        start, end = event.start, event.end
        if event.style != "Caption":
            for a, b in spans:
                if start < b and end > a:
                    if start < a:
                        end = a
                    else:
                        start = b
        if end - start >= 1.0:
            kept.append(Event(start, end, event.style, event.text, event.pos, event.slide))
    return kept


def all_events(run_dir: Path, script: dict[str, Any], timeline: dict[str, Any], options: dict[str, Any], bar: int,
               skip: frozenset[str] = frozenset()) -> list[Event]:
    events = graphic_events(script, timeline, bar, skip) if options["graphics"] else []
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
    # Colours are &HAABBGGRR. Slate boxes like the labels, a cyan accent for the card title, an engineering
    # yellow (#FAC822) for the accent bars and the hero's title; the hero has no box, a soft dark outline instead.
    caption_margin = bar + 46
    return ("Style: CardTitle,Segoe UI,34,&H00F5D622,&H00000000,&H1A2A170F,1,0,3,16,0,9,0,0,0\n"
            "Style: CardValue,Segoe UI,84,&H00FFFFFF,&H00000000,&H1A2A170F,1,0,3,18,0,9,0,0,0\n"
            "Style: CardSub,Segoe UI,32,&H00DDDDDD,&H00000000,&H1A2A170F,0,0,3,14,0,9,0,0,0\n"
            "Style: ThirdTitle,Segoe UI,44,&H00FFFFFF,&H00000000,&H1A2A170F,1,0,3,16,0,1,0,0,0\n"
            "Style: ThirdValue,Segoe UI,34,&H00F5D622,&H00000000,&H1A2A170F,0,0,3,14,0,1,0,0,0\n"
            "Style: HeroTitle,Segoe UI,40,&H0022C8FA,&H00000000,&H802A170F,1,0,1,3,2,7,0,0,0\n"
            "Style: HeroValue,Segoe UI,132,&H00FFFFFF,&H00000000,&H802A170F,1,0,1,5,3,7,0,0,0\n"
            "Style: HeroSub,Segoe UI,40,&H00EEEEEE,&H00000000,&H802A170F,0,0,1,3,2,7,0,0,0\n"
            "Style: Accent,Segoe UI,20,&H0022C8FA,&H00000000,&H00000000,0,0,1,0,0,7,0,0,0\n"
            f"Style: Caption,Segoe UI,46,&H00FFFFFF,&H00000000,&H80000000,1,0,1,3,1,2,160,160,{caption_margin}\n")


def dialogue(event: Event, *, fade_in: bool, fade_out: bool) -> str:
    tags = ""
    if event.pos:
        x, y = event.pos
        # Slides in only where the event really starts, not again at every cut it spans.
        tags += (f"\\move({x + event.slide},{y},{x},{y},0,{SLIDE_MS})" if event.slide and fade_in else f"\\pos({x},{y})")
    if fade_in or fade_out:
        tags += f"\\fad({FADE_MS if fade_in else 0},{FADE_MS if fade_out else 0})"
    # A drawing (the accent bar) is ASS markup already; everything else is plain text.
    text = event.text if event.style == "Accent" else _ass_text(event.text)
    text = ("{" + tags + "}" if tags else "") + text
    return f"Dialogue: 1,{_clock(event.start)},{_clock(event.end)},{event.style},,0,0,0,,{text}\n"


def overlay_rows(events: Sequence[Event], at: float, seconds: float) -> list[str]:
    """ASS Dialogue rows for one slice; a fade only where the overlay really starts or ends, not at a cut."""
    rows = []
    for event in events:
        if event.start >= at + seconds - 1e-3 or event.end <= at + 1e-3:
            continue
        local = Event(max(event.start, at) - at, min(event.end, at + seconds) - at, event.style, event.text, event.pos, event.slide)
        rows.append(dialogue(local, fade_in=event.start >= at - 1e-3, fade_out=event.end <= at + seconds + 1e-3))
    return rows


# ---- music bed -------------------------------------------------------------------------------------------------

# Per mood: chord tones (Hz), the pad's low-pass cutoff (Hz), tempo (BPM) and beats per sub-bass pulse.
# A pad of detuned triangle pairs through the low-pass breathes once every eight beats; under it a sub-bass
# sine pulses on the beat, so the bed is warm and moving instead of a row of test tones (Tier-2, #548).
MOODS: dict[str, tuple[tuple[float, ...], int, int, int]] = {
    "cinematic_tension_drone": ((55.0, 82.41, 110.0, 164.81), 380, 60, 2),
    "epic_build": ((73.42, 110.0, 146.83, 185.0, 220.0), 560, 84, 1),
    "curious_pulse": ((130.81, 196.0, 261.63, 329.63), 600, 96, 1),
    "reflective_ambient": ((87.31, 130.81, 220.0, 329.63), 440, 66, 4),
    "hopeful_rise": ((98.0, 146.83, 246.94, 293.66), 520, 72, 2),
    "dark_suspense": ((65.41, 92.5, 130.81, 185.0), 260, 52, 2),
}
MOOD_CHORDS: dict[str, tuple[float, ...]] = {mood: value[0] for mood, value in MOODS.items()}
# The pad and the pulse are generated at this rate (all of it sits under 1 kHz) and resampled to 48 kHz.
BED_RATE = 24_000


def _mood(mood: str) -> tuple[tuple[float, ...], int, int, int]:
    return MOODS.get(mood, MOODS["reflective_ambient"])


def mood_expression(mood: str) -> str:
    """The pad: detuned triangle pairs, each swelling out of phase with the others once every eight beats."""
    chord, _cutoff, bpm, _every = _mood(mood)
    breath = bpm / 60 / 8
    voices = []
    for index, frequency in enumerate(chord):
        swell = f"(0.6+0.4*sin(2*PI*{breath * (1 + index * 0.17):.4f}*t+{index * 1.7:.1f}))"
        voices.append(f"{swell}*(asin(sin(2*PI*{frequency:.2f}*t))+0.7*asin(sin(2*PI*{frequency * 1.004:.3f}*t)))")
    return f"{0.9 / len(chord) / 3.14159 * 2:.4f}*({'+'.join(voices)})"


def pulse_expression(mood: str) -> str:
    """The sub-bass pulse: a low sine with a fast attack and an exponential decay on every pulse."""
    chord, _cutoff, bpm, every = _mood(mood)
    root = chord[0] if chord[0] < 70 else chord[0] / 2
    rate = bpm / 60 / every
    phase = f"mod(t*{rate:.4f},1)"
    return f"0.5*sin(2*PI*{root:.2f}*t)*min(1,{phase}/0.02)*exp(-4.5*{phase})"


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
    """One WAV under the whole film: each chapter's pad and pulse, crossfading into the next chapter's."""
    argv = [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y"]
    graph = []
    for index, (mood, start, end) in enumerate(spans):
        length = end - start + (2.0 if index < len(spans) - 1 else 0.0)
        cutoff = _mood(mood)[1]
        argv += ["-f", "lavfi", "-i", f"aevalsrc=exprs='{mood_expression(mood)}':s={BED_RATE}:c=mono:d={length:.3f}",
                 "-f", "lavfi", "-i", f"aevalsrc=exprs='{pulse_expression(mood)}':s={BED_RATE}:c=mono:d={length:.3f}"]
        fade = min(2.0, length / 3)
        graph.append(f"[{index * 2}:a]lowpass=f={cutoff},lowpass=f={cutoff * 2}[pad{index}];[{index * 2 + 1}:a]lowpass=f=140[sub{index}];"
                     f"[pad{index}][sub{index}]amix=inputs=2:normalize=0,afade=t=in:d={fade:.3f},afade=t=out:st={length - fade:.3f}:d={fade:.3f},"
                     f"adelay={int(round(start * 1000))}:all=1[m{index}]")
    mixed = "".join(f"[m{index}]" for index in range(len(spans)))
    graph.append(f"{mixed}amix=inputs={len(spans)}:normalize=0:duration=longest,aresample=48000,pan=stereo|c0=c0|c1=c0,"
                 f"aecho=0.8:0.6:90|170:0.25|0.15,atrim=0:{total:.3f},loudnorm=I={BED_LUFS:g}:TP=-8:LRA=6[bed]")
    return argv + ["-filter_complex", ";".join(graph), "-map", "[bed]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output)]


def file_bed_argv(ffmpeg: Path, music: Path, total: float, output: Path) -> list[str]:
    """The user's track, looped to the film's length and faded out."""
    fade = min(4.0, total / 4)
    return [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y", "-stream_loop", "-1", "-i", str(music),
            "-filter_complex", f"[0:a]atrim=0:{total:.3f},afade=t=out:st={max(0.0, total - fade):.3f}:d={fade:.3f},"
            f"loudnorm=I={BED_LUFS:g}:TP=-8:LRA=6[bed]", "-map", "[bed]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output)]


def ducked_mix(bed: bool = True, ambience: int | None = None) -> str:
    """Narration over the bed, the bed pulled down while the narrator speaks; the room tone (input ``ambience``)
    sits under both without ducking, already far below the voice."""
    if not bed and ambience is None:
        return "[1:a]apad[audio]"
    parts, layers = ["[1:a]apad,asplit=2[voice][key]" if bed else "[1:a]apad[voice]"], ["[voice]"]
    if bed:
        parts.append("[2:a][key]sidechaincompress=threshold=0.05:ratio=3:attack=20:release=450:makeup=1[bed]")
        layers.append("[bed]")
    if ambience is not None:
        parts.append(f"[{ambience}:a]anull[room]")
        layers.append("[room]")
    parts.append(f"{''.join(layers)}amix=inputs={len(layers)}:normalize=0:duration=first,alimiter=limit=0.95[audio]")
    return ";".join(parts)


def describe(options: dict[str, Any]) -> dict[str, Any]:
    music = options["music"]
    return {"graphics": options["graphics"], "captions": options["captions"], "music": music,
            "music_path": options["music_path"] if music == "file" else None, "sfx": options["sfx"]}


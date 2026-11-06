"""Room tone under the narration: each scene's ambient sound and a hit between acts (ADR-0049, Tier-2).

The script gives every scene an ``ambient_sfx_tag`` (wind at height, machinery
hum, flowing water…). Before this the rough cut ignored it, so between the
voice and the music bed the film was dead air. Now each tagged scene gets its
sound for exactly its narrated ``[start, end]`` (from
``master_audio_timeline.json``), faded in and out over 0.5 s at about
-31 LUFS, far under the -16 LUFS voice; consecutive scenes with one tag share
one stretch, so the sound does not dip in the pause between them.

A file ``<tag>.wav`` (or .mp3, .flac, .ogg) in an ``assets/sfx`` folder, the
project's first and then the app's, wins over the built-in sound and is
looped and normalised to the same level. Otherwise the sound is synthesised by
FFmpeg (``anoisesrc``/``aevalsrc`` through filters, fixed seeds), so there is no
third-party audio to licence and the same film always sounds the same.

At every act change, during the 1.0 s pause the narration leaves there, a
1.2 s low sub-drop with a soft whoosh (about -22 LUFS) marks the new chapter.

Tier-3 adds UI micro-sounds (about -24 LUFS, short and crisp) exactly where a
graphic appears: ``telemetry_sweep`` (a rising radar sweep and soft beeps) when
the satellite map cuts in, ``hud_slide_tick`` (a low mechanical click with a
bright tick) when a hero number card slides in or a data slide opens. A file
``<name>.wav`` in an ``assets/sfx`` folder wins over the built-in sound here too.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

RATE = 48_000
SFX_LUFS = -31.0
HIT_LUFS = -22.0
FADE = 0.5
HIT_SECONDS = 1.2
# Two stretches of one tag closer than this become one (the scene pause is 0.40 s).
JOIN_GAP = 1.2
# Shorter stretches are left silent: a sound that fades in and straight out is a blip.
MIN_STRETCH = 1.5
SUFFIXES = (".wav", ".flac", ".mp3", ".ogg")
APP_SFX = Path(__file__).resolve().parents[3] / "assets" / "sfx"

# Built-in sounds: an FFmpeg source and a filter chain each, at about 0 dB gain before ``GAINS``.
_NOISE = "anoisesrc=r={rate}:c={colour}:a={amp}:seed={seed}"
RECIPES: dict[str, tuple[str, str]] = {
    # Pink noise, low band-pass, a slow gust swell.
    "wind_high_altitude": (_NOISE.format(rate=RATE, colour="pink", amp=0.5, seed=11),
                           "highpass=f=90,lowpass=f=900,bandpass=f=380:width_type=h:w=520,tremolo=f=0.13:d=0.55"),
    # A warm drone on 55 and 110 Hz with its harmonics, slowly beating, over rumble.
    "heavy_machinery_hum": ("aevalsrc=exprs='(0.55*sin(2*PI*55*t)+0.35*sin(2*PI*110.4*t)+0.12*sin(2*PI*165*t)+0.05*sin(2*PI*220.7*t))"
                            "*(0.85+0.15*sin(2*PI*0.21*t))':s=48000:c=mono",
                            "lowpass=f=420"),
    "water_turbine_roar": (_NOISE.format(rate=RATE, colour="brown", amp=0.7, seed=13),
                           "highpass=f=40,lowpass=f=700,volume='0.85+0.15*sin(2*PI*0.09*t)':eval=frame"),
    # Soft blips at 1.6–2.4 kHz, a few a second, never on a steady grid.
    "data_beeps": ("aevalsrc=exprs='0.25*sin(2*PI*(1600+400*mod(floor(t*2.7),3))*t)*exp(-28*mod(t*2.7,1))*lt(mod(floor(t*2.7)*7,5),3)'"
                   ":s=48000:c=mono", "lowpass=f=3200"),
    "construction_site": ("aevalsrc=exprs='0.3*sin(2*PI*640*t)*exp(-22*mod(t,1.73))+0.22*sin(2*PI*410*t)*exp(-30*mod(t+0.6,2.41))"
                          "+0.04*(random(1)-0.5)':s=48000:c=mono", "lowpass=f=2500,aecho=0.6:0.4:120:0.3"),
    "city_traffic": (_NOISE.format(rate=RATE, colour="brown", amp=0.6, seed=17),
                     "highpass=f=50,lowpass=f=1100,volume='0.78+0.22*sin(2*PI*0.07*t)':eval=frame"),
    # Brown noise, smoothed, with a little high wash for the surface.
    "flowing_water": (_NOISE.format(rate=RATE, colour="brown", amp=0.6, seed=19),
                      "highpass=f=150,lowpass=f=1800,tremolo=f=0.31:d=0.25"),
    "rain": (_NOISE.format(rate=RATE, colour="pink", amp=0.4, seed=23), "highpass=f=900,lowpass=f=7000"),
    "crowd_murmur": (_NOISE.format(rate=RATE, colour="pink", amp=0.5, seed=29),
                     "bandpass=f=550:width_type=h:w=700,tremolo=f=3.3:d=0.35,tremolo=f=0.4:d=0.3"),
    "industrial_metal": ("aevalsrc=exprs='0.28*(sin(2*PI*523*t)+0.6*sin(2*PI*1187*t)+0.3*sin(2*PI*1851*t))*exp(-6*mod(t,2.9))"
                         "+0.03*(random(2)-0.5)':s=48000:c=mono", "lowpass=f=3500,aecho=0.7:0.5:60|140:0.35|0.2"),
}
# Linear gain that brings each built-in sound to SFX_LUFS (ebur128 on 30 s of it, mono; the mix is spread to
# both channels at -3 dB so the stereo file measures the same).
GAINS: dict[str, float] = {
    "wind_high_altitude": 1.1749, "heavy_machinery_hum": 0.1, "water_turbine_roar": 0.335, "data_beeps": 1.0351,
    "construction_site": 2.7542, "city_traffic": 0.4121, "flowing_water": 0.4266, "rain": 0.5957, "crowd_murmur": 1.0593,
    "industrial_metal": 0.9441,
}
# The act hit: a sine falling from 90 to 35 Hz with a fast attack and long decay, under a band-passed whoosh.
HIT = ("aevalsrc=exprs='(0.9*sin(2*PI*(35*t+55*(1-exp(-3*t))/3))*min(1,t/0.03)*exp(-2.6*t))':s=48000:c=mono:d={d}".format(d=HIT_SECONDS),
       "lowpass=f=160")
WHOOSH = (_NOISE.format(rate=RATE, colour="pink", amp=0.6, seed=31) + f":d={HIT_SECONDS}",
          f"bandpass=f=700:width_type=h:w=900,afade=t=in:d=0.45:curve=qsin,afade=t=out:st=0.45:d={HIT_SECONDS - 0.45:.2f}:curve=qsin")
# Sub-drop at about -23 LUFS and whoosh at about -28.7: together about HIT_LUFS.
HIT_GAIN = 0.5
WHOOSH_GAIN = 1.0


UI_LUFS = -24.0
# The UI sounds, synthesised like the room tones; gains bring each to about UI_LUFS (ebur128, mono).
UI_SOUNDS: dict[str, tuple[str, float, float]] = {
    "telemetry_sweep": ("aevalsrc=exprs='0.3*sin(2*PI*(500*t+900*t*t))*exp(-1.8*t)"
                        "+0.3*sin(2*PI*1850*t)*exp(-38*mod(t-0.15,0.27))*gte(t,0.15)*lt(t,0.96)':s=48000:c=mono:d=1.2", 0.569, 1.2),
    "hud_slide_tick": ("aevalsrc=exprs='0.8*sin(2*PI*130*t)*exp(-38*t)+0.5*sin(2*PI*2600*t)*exp(-160*t)"
                       "+0.25*(random(5)-0.5)*exp(-12*abs(t-0.12))':s=48000:c=mono:d=0.5", 0.902, 0.5),
}


def stretches(script: dict[str, Any], timeline: dict[str, Any]) -> list[tuple[str, float, float]]:
    """(tag, start, end) per run of narrated scenes with one ambient tag, in time order."""
    tags = {scene.get("scene_id"): scene.get("ambient_sfx_tag") for chapter in script.get("chapters", [])
            for scene in chapter.get("scenes", [])}
    found: list[list[Any]] = []
    for timed in sorted(timeline.get("scenes", []), key=lambda item: float(item.get("start") or 0.0)):
        tag = tags.get(timed.get("scene_id"))
        if tag not in RECIPES:
            continue
        start, end = float(timed["start"]), float(timed["end"])
        if found and found[-1][0] == tag and start - found[-1][2] <= JOIN_GAP:
            found[-1][2] = max(found[-1][2], end)
        else:
            found.append([tag, start, end])
    return [(tag, round(start, 3), round(end, 3)) for tag, start, end in found if end - start >= MIN_STRETCH]


def act_hits(timeline: dict[str, Any]) -> list[float]:
    """Where each act after the first is announced: the start of the pause before it."""
    chapters = sorted(timeline.get("chapters", []), key=lambda item: float(item.get("start") or 0.0))
    return [round(float(chapters[index - 1].get("end") or chapters[index]["start"]), 3) for index in range(1, len(chapters))]


def find_file(tag: str, folders: Sequence[Path]) -> Path | None:
    for folder in folders:
        for suffix in SUFFIXES:
            path = folder / f"{tag}{suffix}"
            if path.is_file():
                return path
    return None


def folders(run_dir: Path) -> list[Path]:
    return [run_dir / "assets" / "sfx", APP_SFX]


def envelope(spans: Sequence[tuple[float, float]]) -> str:
    """A volume expression: 1 inside each span, 0.5 s linear ramps at its edges, 0 elsewhere."""
    terms = [f"clip((t-{start:.3f})/{FADE},0,1)*clip(({end:.3f}-t)/{FADE},0,1)" for start, end in spans]
    return "+".join(terms) or "0"


def ambience_argv(ffmpeg: Path, script: dict[str, Any], timeline: dict[str, Any], total: float, output: Path,
                  sfx_folders: Sequence[Path] = (), cues: Sequence[tuple[str, float]] = ()) -> tuple[list[str], list[Path]] | None:
    """One stereo WAV the film's length with every scene's room tone and the act hits; None when there is nothing to play.

    Also returns the user's sound files used, so the caller can key its cache on them.
    """
    runs = stretches(script, timeline)
    hits = [at for at in act_hits(timeline) if at + 0.1 < total]
    cues = sorted((name, round(float(at), 3)) for name, at in cues if name in UI_SOUNDS and 0 <= float(at) < total - 0.1)
    if not runs and not hits and not cues:
        return None
    argv = [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y"]
    graph, labels, files = [], [], []
    by_tag: dict[str, list[tuple[float, float]]] = {}
    for tag, start, end in runs:
        by_tag.setdefault(tag, []).append((start, end))
    index = 0
    for tag in sorted(by_tag):
        spans = by_tag[tag]
        stop = min(total, max(end for _, end in spans) + FADE)
        file = find_file(tag, sfx_folders)
        if file is not None:
            files.append(file)
            argv += ["-stream_loop", "-1", "-i", str(file)]
            chain = (f"[{index}:a]aformat=sample_rates={RATE}:channel_layouts=mono,atrim=0:{stop:.3f},"
                     f"loudnorm=I={SFX_LUFS:g}:TP=-12:LRA=7,aresample={RATE}")
        else:
            source, filters = RECIPES[tag]
            argv += ["-f", "lavfi", "-i", f"{source}:d={stop:.3f}"]
            chain = f"[{index}:a]{filters},volume={GAINS[tag]:.4f}"
        graph.append(f"{chain},volume='{envelope(spans)}':eval=frame[s{index}]")
        labels.append(f"[s{index}]")
        index += 1
    for at in hits:
        for source, filters, gain in ((HIT[0], HIT[1], HIT_GAIN), (WHOOSH[0], WHOOSH[1], WHOOSH_GAIN)):
            argv += ["-f", "lavfi", "-i", source]
            graph.append(f"[{index}:a]{filters},volume={gain:.4f},adelay={int(round(at * 1000))}:all=1[s{index}]")
            labels.append(f"[s{index}]")
            index += 1
    for name, at in cues:
        file = find_file(name, sfx_folders)
        source, gain, length = UI_SOUNDS[name]
        if file is not None:
            files.append(file)
            argv += ["-i", str(file)]
            chain = (f"[{index}:a]aformat=sample_rates={RATE}:channel_layouts=mono,atrim=0:{length * 2:.3f},"
                     f"volume={gain:.4f}")
        else:
            argv += ["-f", "lavfi", "-i", source]
            chain = f"[{index}:a]highpass=f=60,lowpass=f=7000,volume={gain:.4f}"
        graph.append(f"{chain},adelay={int(round(at * 1000))}:all=1[s{index}]")
        labels.append(f"[s{index}]")
        index += 1
    graph.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest,apad=whole_dur={total:.3f},"
                 f"atrim=0:{total:.3f},pan=stereo|c0=0.7071*c0|c1=0.7071*c0[ambience]")
    argv += ["-filter_complex", ";".join(graph), "-map", "[ambience]", "-ar", str(RATE), "-ac", "2", "-c:a", "pcm_s16le", str(output)]
    return argv, files

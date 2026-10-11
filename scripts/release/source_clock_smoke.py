"""Actual owned FFmpeg source-clock controls for staged/installed Windows CI.

Authored PCM controls prove timestamp/codec behavior, not film lip-sync or TTS.
"""
from __future__ import annotations

import argparse
from array import array
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import wave


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _run(command):
    subprocess.run([str(item) for item in command], check=True, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)


def exercise(adapter, probing, work):
    from engine.dubflow.media.adapter import audio_origin_samples, MediaTimeline
    work.mkdir(parents=True, exist_ok=False)
    rate = 48000
    values = array("h", (int(4000 * math.sin(2 * math.pi * 440 * frame / rate))
        if rate // 4 <= frame < 3 * rate // 4 or 9 * rate // 4 <= frame < 11 * rate // 4 else 0
        for frame in range(4 * rate)))
    if sys.byteorder != "little":
        values.byteswap()
    raw = values.tobytes()
    tone = work / "tone.wav"
    with wave.open(str(tone), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(raw)
    cases = []
    for name, video_offset, audio_offset in (("zero", "0", "0"), ("late", "0", "1.5"),
                                            ("early", "2", "0"), ("shifted", "1", "2.5")):
        source = work / (name + ".mkv")
        _run([adapter.ffmpeg_path, "-nostdin", "-v", "error", "-copyts",
              "-itsoffset", video_offset, "-f", "lavfi", "-i", "color=s=160x90:r=25:d=4",
              "-itsoffset", audio_offset, "-i", tone, "-map", "0:v:0", "-map", "1:a:0",
              "-c:v", "h264_mf", "-quality", "90", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
              "-c:a", "pcm_s16le", "-avoid_negative_ts", "disabled", source])
        probe = probing.probe(source)
        video, audio = probe.video, probe.audio[0]
        actual_video = Fraction(video.start_pts * video.time_base.numerator, video.time_base.denominator)
        actual_audio = Fraction(audio.start_pts * audio.time_base.numerator, audio.time_base.denominator)
        if actual_video != Fraction(video_offset) or actual_audio != Fraction(audio_offset):
            raise ValueError("generated source clock differs from the authored control: " + name)
        origin = audio_origin_samples(video, rate)
        gap = actual_audio * rate - origin
        if gap.denominator != 1:
            raise ValueError("control gap is not integral PCM")
        gap = int(gap)
        expected = bytes(gap * 2) + raw if gap >= 0 else raw[-gap * 2:]
        bed = adapter.extract_audio(source, work / (name + ".wav"), video_stream=video, sample_rate=rate)
        with wave.open(str(bed), "rb") as reader:
            decoded = reader.readframes(reader.getnframes())
            if (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) != (rate, 1, 2) or decoded != expected:
                raise ValueError("decoded PCM differs from independent authored timestamp mapping: " + name)
        renders = []
        # Matroska reports container duration including the source offset, so
        # use the authored four-second video duration in its native tick base.
        duration_ticks = Fraction(4 * video.time_base.denominator, video.time_base.numerator)
        if duration_ticks.denominator != 1:
            raise ValueError("control video duration is non-integral")
        duration = MediaTimeline(video.time_base, 0, int(duration_ticks), int(duration_ticks))
        for mode in ("original", "mixed"):
            output = work / (name + "-" + mode + ".mp4")
            adapter.render(source, output, source_probe=probe, video_duration=duration,
                           audio_path=bed if mode == "mixed" else None, preserve_original_audio=mode == "original")
            rendered = probing.probe(output)
            if (rendered.video.start_pts != 0 or rendered.audio[0].start_pts != 0
                    or rendered.video.codec_name != "h264" or rendered.audio[0].codec_name != "aac"):
                raise ValueError("rendered source clock/codec differs: " + name + "/" + mode)
            _run([adapter.ffmpeg_path, "-nostdin", "-v", "error", "-i", output,
                  "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"])
            pcm = work / (name + "-" + mode + "-decoded.wav")
            adapter.extract_audio(output, pcm, sample_rate=rate)
            with wave.open(str(pcm), "rb") as reader:
                samples = array("h", reader.readframes(reader.getnframes()))
            target = array("h", expected)
            if sys.byteorder != "little":
                samples.byteswap()
                target.byteswap()
            actual_onset = next(i for i, sample in enumerate(samples) if abs(sample) > 512)
            expected_onset = next(i for i, sample in enumerate(target) if abs(sample) > 512)
            # AAC priming/pre-echo is allowed up to two 1024-sample blocks;
            # the original1.436-second defect cannot pass this check.
            if abs(actual_onset - expected_onset) > 2048:
                raise ValueError("encoded audio onset lost the source offset: " + name + "/" + mode)
            renders.append({"mode": mode, "sha256": _sha(output), "video_start_pts": rendered.video.start_pts,
                            "audio_start_pts": rendered.audio[0].start_pts, "onset_error_samples": actual_onset - expected_onset})
        cases.append({"name": name, "source_sha256": _sha(source), "source_video_start_pts": video.start_pts,
                      "source_audio_start_pts": audio.start_pts, "relative_gap_samples": gap,
                      "decoded_pcm_sha256": hashlib.sha256(decoded).hexdigest(), "pcm_equals_authored_reference": True,
                      "renders": renders})
    return cases


def run(args):
    root, work, report = args.root.resolve(), args.work_root.resolve(), args.report.resolve()
    if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode
            or not os.path.samefile(sys.executable, root / "runtime/python.exe")):
        raise ValueError("source clock qualification requires owned Python -I -S -B")
    for output in (work, report):
        if any(parent.exists() and os.path.samefile(parent, root) for parent in (output, *output.parents)):
            raise ValueError("qualification outputs must stay outside the immutable runtime")
    sys.path[:0] = [str(root / "app"), str(root / "runtime/Lib/site-packages")]
    from packaging.release.bootstrap import verify_bundle
    from packaging.release.manifest import ReleaseManifest
    with (root / "release-manifest.json").open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("release manifest exceeds bound")
    manifest = ReleaseManifest.from_mapping(json.loads(raw))
    if manifest.source_sha != args.expected_source_sha:
        raise ValueError("source clock qualification source SHA differs")
    verify_bundle(root, manifest)
    from engine.dubflow.media import FfmpegMediaAdapter, MediaProbe
    from engine.dubflow.media import adapter as producer
    if not os.path.samefile(producer.__file__, root / "app/engine/dubflow/media/adapter.py"):
        raise ValueError("source clock qualification did not load packaged producer")
    media_root = root / "runtime/media"
    cases = exercise(FfmpegMediaAdapter(media_root / "ffmpeg.exe", trusted_root=media_root),
                     MediaProbe(media_root / "ffprobe.exe", trusted_root=media_root), work)
    value = {"schema_version": 1, "source_sha": manifest.source_sha, "verified_release_tree": True,
             "recipe": producer.AUDIO_DECODE_RECIPE, "cases": cases,
             "scope": "actual packaged owned media with authored PCM; no film/TTS/human quality claim"}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--report", type=Path, required=True)
    run(parser.parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

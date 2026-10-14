"""Owned private corrected render/QC execution; never publish or write SQLite.

Native code must admit the original release/context/storage authority before
dispatch and independently retain/admit the complete result before graph CAS.
Completed candidates are resumable; long-form chunk scheduling is not enabled
by this entry point and remains required before user-facing activation.
"""
from __future__ import annotations

from pathlib import Path
import os
import sys

MAX_PACKET = 64 * 1024 * 1024
MAX_FILE = 128 * 1024**3
ARTIFACTS = {"final_video":"final_vi.mp4", "captions_srt":"captions_vi.srt",
    "captions_ass":"captions_vi.ass", "corrected_timeline":"timeline.json", "qc_report":"qc_report.json"}


def _imports():
    if __package__ in (None, ""):
        sys.path.append(str(Path(sys.executable).resolve().parent / "Lib/site-packages"))
        sys.path.append(str(Path(__file__).resolve().parents[3]))


def _closed(value, fields):
    from .production import ReviewError
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ReviewError("render context has missing or unknown fields")


def _path(value, root=None):
    from engine.dubflow.mix.streaming import _plain
    from .production import ReviewError
    if not isinstance(value, str):
        raise ReviewError("render paths require native absolute strings")
    path = _plain(Path(value))
    if root is not None:
        admitted = _plain(Path(root))
        if not admitted.is_dir():
            raise ReviewError("render input root requires an admitted directory")
        contained = path.is_relative_to(admitted)
        if not contained:
            # Native Windows locators may use the extended local-drive spelling
            # while the owned worker root uses DOS spelling. Compare existing
            # plain directory identities, never strip a prefix or resolve a
            # link into a permitted root. Keep the exact admitted input path.
            for ancestor in (path, *path.parents):
                try:
                    if ancestor.is_dir() and os.path.samefile(ancestor, admitted):
                        contained = True
                        break
                except OSError:
                    continue
        if not contained:
            raise ReviewError("render input is outside its native admitted root")
    return path


def _size(value):
    import re
    from .production import ReviewError
    if (not isinstance(value, str) or len(value) > 20 or
            re.fullmatch(r"[1-9][0-9]*", value) is None or int(value) > MAX_FILE):
        raise ReviewError("render byte count requires a bounded positive decimal string")
    return int(value)


def _file_hash(path, deadline, *, inventory_file=False):
    import hashlib
    import stat
    import time
    from engine.dubflow.mix.streaming import _plain
    from .production import ReviewError
    def safe():
        if inventory_file:
            # Packaging may deduplicate admitted owned executables with hard
            # links. This exception is never used for context/media/output.
            _plain(path.parent)
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ReviewError("owned render recipe cannot contain reparse files")
        else:
            _plain(path)
    safe()
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ReviewError("render media requires a regular file")
        while block := stream.read(32768):
            if time.monotonic() >= deadline:
                raise ReviewError("render operation exceeded its cooperative deadline")
            count += len(block)
            if count > MAX_FILE:
                raise ReviewError("render media exceeds its byte bound")
            digest.update(block)
    if not count:
        raise ReviewError("render media cannot be empty")
    safe()
    return "sha256:" + digest.hexdigest(), count


def _verify(pin, root, deadline):
    from .export import _hash
    from .production import ReviewError
    _closed(pin, ("path", "sha256", "size_bytes"))
    path = _path(pin["path"], root)
    _hash(pin["sha256"])
    expected = _size(pin["size_bytes"])
    if path.stat().st_size != expected or _file_hash(path, deadline) != (pin["sha256"], expected):
        raise ReviewError("render input/output size or content changed")
    return path


def _read_packet(path):
    from .production import ReviewError
    path = _path(str(path))
    if not path.is_file() or path.stat().st_size > MAX_PACKET:
        raise ReviewError("private render checkpoint is unavailable or exceeds its byte bound")
    with path.open("rb") as stream:
        raw = stream.read(MAX_PACKET + 1)
    if len(raw) > MAX_PACKET:
        raise ReviewError("private render checkpoint exceeds its byte bound")
    return raw


def _checkpoint(result, context, raw, identity, generation, deadline):
    from .production import ReviewError
    from .synthesis_input import _digest
    _closed(result, ("schema_version", "kind", "job_id", "mode", "context_sha256",
        "execution_identity", "after_snapshot_sha256", "artifacts", "active_graph_published", "correction_ready"))
    if (type(result["schema_version"]) is not int or result["schema_version"] != 1 or
            result["kind"] != "review_private_render_candidate" or
            result["job_id"] != context["job_id"] or result["mode"] != context["mode"] or
            result["execution_identity"] != identity or result["context_sha256"] != _digest(raw) or
            result["after_snapshot_sha256"] != _digest(context["after_snapshot_json"].encode("utf-8")) or
            result["active_graph_published"] is not False or result["correction_ready"] is not False):
        raise ReviewError("private render checkpoint differs from its input/recipe")
    _closed(result["artifacts"], ARTIFACTS)
    for role, name in ARTIFACTS.items():
        pin = result["artifacts"][role]
        _closed(pin, ("path", "sha256", "size_bytes"))
        if pin["path"] != str(generation / name):
            raise ReviewError("private render checkpoint substitutes an output role")
        _verify(pin, generation, deadline)
    if _read_packet(generation / "timeline.json") != context["corrected_timeline_json"].encode("utf-8"):
        raise ReviewError("private render checkpoint substitutes its corrected timeline")


def admit_context(raw):
    from .corrected_timeline import admit_correction, derive_corrected_timeline
    from .production import ReviewError
    from .synthesis_input import _digest
    from .wire import decode_review_json, read_review_snapshot
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_PACKET:
        raise ReviewError("render context exceeds its byte bound")
    context = decode_review_json(raw.decode("utf-8"))
    _closed(context, ("schema_version", "kind", "job_id", "mode", "before_snapshot_json",
        "after_snapshot_json", "original_manifest_json", "original_manifest_sha256",
        "original_timeline_json", "corrected_timeline_json", "corrected_timeline_sha256",
        "original_output_root", "source", "retained_mix", "burn_in_subtitles"))
    if (type(context["schema_version"]) is not int or context["schema_version"] != 1 or
            context["kind"] != "review_native_render_context" or context["mode"] not in ("b1", "b2") or
            type(context["burn_in_subtitles"]) is not bool):
        raise ReviewError("unknown native render format or mode requires compatibility")
    for name in ("before_snapshot_json", "after_snapshot_json", "original_manifest_json",
                 "original_timeline_json", "corrected_timeline_json"):
        if not isinstance(context[name], str):
            raise ReviewError("render context requires exact archived JSON strings")
    before = read_review_snapshot(decode_review_json(context["before_snapshot_json"]))
    after = read_review_snapshot(decode_review_json(context["after_snapshot_json"]))
    old, new = admit_correction(before, after)
    if (before.job_id != context["job_id"] or
            (old.voice is None) != (context["mode"] == "b1")):
        raise ReviewError("render mode/job differs from native correction")
    original = context["original_timeline_json"].encode("utf-8")
    corrected = context["corrected_timeline_json"].encode("utf-8")
    expected = derive_corrected_timeline(original, before, after)
    if (decode_review_json(corrected.decode("utf-8")) != decode_review_json(expected.decode("utf-8")) or
            _digest(corrected) != context["corrected_timeline_sha256"] or
            (old.translated_text == new.translated_text and corrected != original)):
        raise ReviewError("render substitutes the exact native corrected timeline")
    manifest_raw = context["original_manifest_json"].encode("utf-8")
    manifest = decode_review_json(manifest_raw.decode("utf-8"))
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or
            manifest["schema_version"] != 1 or manifest.get("kind") != "dubflow_localization_job" or
            manifest.get("job_id") != before.job_id or _digest(manifest_raw) != context["original_manifest_sha256"] or
            manifest["artifacts"]["editable_timeline"]["sha256"] != _digest(original)):
        raise ReviewError("render original manifest/timeline binding differs")
    source = context["source"]
    _closed(source, ("path", "sha256", "size_bytes", "artifact_root"))
    _path(source["path"], _path(source["artifact_root"]))
    _size(source["size_bytes"])
    if source["path"] != manifest["source"]["path"] or source["sha256"] != manifest["source"]["sha256"]:
        raise ReviewError("render source differs from the archived original")
    _path(context["original_output_root"])
    if context["mode"] == "b1":
        if context["retained_mix"] is not None:
            raise ReviewError("B1 render cannot invent a dubbing mix")
    else:
        from engine.dubflow.mix.adapter import parse_mix_json
        from .synthesis_input import _encode
        retained = context["retained_mix"]
        _closed(retained, ("mix_document_json", "mix_document_sha256", "composition_json",
                           "composition_sha256", "artifact_root"))
        for name in ("mix_document", "composition"):
            if (not isinstance(retained[name + "_json"], str) or
                    _digest(retained[name + "_json"].encode("utf-8")) != retained[name + "_sha256"]):
                raise ReviewError("render retained mix metadata hash differs")
        mix = parse_mix_json(retained["mix_document_json"])
        composition = decode_review_json(retained["composition_json"])
        if (not isinstance(composition, dict) or type(composition.get("schema_version")) is not int or
                composition.get("schema_version") != 2 or composition.get("kind") != "review_audio_composition" or
                composition.get("job_id") != before.job_id or
                composition.get("manifest_sha256") != context["original_manifest_sha256"] or
                composition.get("review_snapshot_sha256") != _digest(_encode(after.to_dict(), MAX_PACKET)) or
                mix["provenance"]["input_hash"] != retained["composition_sha256"]):
            raise ReviewError("render mix does not belong to this exact corrected revision")
        for role in ("original_audio", "dialogue_stem", "final_mix"):
            _path(mix[role]["path"], _path(retained["artifact_root"]))
    return context, manifest, before, after, old.translated_text != new.translated_text


def _inputs(context, deadline):
    from .production import ReviewError
    source = {k: context["source"][k] for k in ("path", "sha256", "size_bytes")}
    _verify(source, _path(context["source"]["artifact_root"]), deadline)
    if context["retained_mix"] is None:
        return None
    import wave
    from engine.dubflow.mix.adapter import parse_mix_json
    retained = context["retained_mix"]
    mix = parse_mix_json(retained["mix_document_json"])
    for role in ("original_audio", "dialogue_stem", "final_mix"):
        item = mix[role]
        path = _path(item["path"], _path(retained["artifact_root"]))
        if _file_hash(path, deadline)[0] != item["content_hash"]:
            raise ReviewError("render retained PCM content differs")
        with wave.open(str(path), "rb") as audio:
            if ((audio.getcomptype(), audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getnframes()) !=
                    ("NONE", item["sample_rate"], item["channels"], 2, item["frame_count"])):
                raise ReviewError("render retained PCM header differs")
            frames = 0
            while block := audio.readframes(8192):
                import time
                if time.monotonic() >= deadline:
                    raise ReviewError("render PCM validation exceeded its deadline")
                if len(block) % (2 * item["channels"]):
                    raise ReviewError("render retained PCM is truncated")
                frames += len(block) // (2 * item["channels"])
            if frames != item["frame_count"]:
                raise ReviewError("render retained PCM frame count differs")
    return Path(mix["final_mix"]["path"])


def render_candidate(raw, *, output_root, ffmpeg_path, ffprobe_path, media_root, timeout_seconds=900):
    """Execute/revalidate a private candidate. No publication or B2 downgrade."""
    import time
    from engine.dubflow.media import FfmpegMediaAdapter, MediaProbe
    from engine.dubflow.worker.production_job import TextCue, _write_subtitles, _source_render_duration, _validate_rendered_audio
    from .production import ReviewError
    from .synthesis_input import _digest, _encode
    from .synthesis_worker import _writer, _atomic
    from .wire import decode_review_json
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise ReviewError("render deadline must be a bounded native integer")
    deadline = time.monotonic() + timeout_seconds
    context, manifest, before, after, text_changed = admit_context(raw)
    output = _path(str(output_root))
    if not output.is_dir():
        raise ReviewError("native private render root is unavailable")
    media = FfmpegMediaAdapter(ffmpeg_path, trusted_root=media_root, timeout_seconds=timeout_seconds)
    probe = MediaProbe(ffprobe_path, trusted_root=media_root, timeout_seconds=timeout_seconds)
    # Actual executable/source bytes separate producer generations on resume.
    pins = [_file_hash(path, deadline, inventory_file=True)[0] for path in (media.ffmpeg_path, probe.ffprobe_path)]
    pins += [_file_hash(path, deadline)[0] for path in (
        Path(__file__), Path(__file__).parents[1] / "worker/production_job.py", Path(__file__).parents[1] / "media/adapter.py",
        *(Path(__file__).with_name(name) for name in ("corrected_timeline.py", "wire.py", "production.py",
                                                     "synthesis_input.py", "synthesis_worker.py")))]
    identity = _digest(_encode({"context":_digest(raw), "recipe":"review-private-render-v1", "executables_and_source":pins}, MAX_PACKET))
    generation = _path(str(output / ("render-" + identity[7:])))
    if _path(context["source"]["path"]).is_relative_to(generation):
        raise ReviewError("private render cannot overwrite its source")
    generation.mkdir(exist_ok=True)
    with _writer(generation):
        input_path, result_path = generation / "input.json", generation / "result.json"
        if input_path.exists():
            if _read_packet(input_path) != raw:
                raise ReviewError("private render generation has a foreign input")
        else:
            _atomic(input_path, raw)
        audio = _inputs(context, deadline)
        source = Path(context["source"]["path"])
        actual_source = probe.probe(source)
        expected_probe, actual_probe = dict(manifest["source"]["probe"]), actual_source.to_dict()
        expected_probe["source_path"] = actual_probe["source_path"]
        if expected_probe != actual_probe:
            raise ReviewError("source clock/streams differ from the archived probe")
        final = generation / "final_vi.mp4"
        for name in ARTIFACTS.values():
            _path(str(generation / name), generation)
        if result_path.exists():
            previous = _read_packet(result_path)
            result = decode_review_json(previous.decode("utf-8"))
            _checkpoint(result, context, raw, identity, generation, deadline)
        else:
            timeline = decode_review_json(context["corrected_timeline_json"])
            if text_changed:
                srt, ass = _write_subtitles(generation, tuple(TextCue(**cue) for cue in timeline["cues"]))
            else:
                # Voice changes cannot upgrade/reformat a prior subtitle artifact.
                for role, name in (("captions_srt", "captions_vi.srt"), ("captions_ass", "captions_vi.ass")):
                    pin = dict(manifest["artifacts"][role]); pin["size_bytes"] = str(pin["size_bytes"])
                    original = _verify(pin, _path(context["original_output_root"]), deadline)
                    with original.open("rb") as stream:
                        subtitle = stream.read(MAX_PACKET + 1)
                    if len(subtitle) > MAX_PACKET:
                        raise ReviewError("original subtitle exceeds its byte bound")
                    _atomic(generation / name, subtitle)
                srt, ass = generation / "captions_vi.srt", generation / "captions_vi.ass"
            _atomic(generation / "timeline.json", context["corrected_timeline_json"].encode("utf-8"))
            media.timeout_seconds = max(0.001, deadline - time.monotonic())
            media.render(source, final, subtitle_path=ass, audio_path=audio, source_probe=actual_source,
                video_duration=_source_render_duration(actual_source), preserve_original_audio=audio is None,
                burn_in_subtitles=context["burn_in_subtitles"], overwrite=True)
        probe.timeout_seconds = max(0.001, deadline - time.monotonic())
        output_probe = probe.probe(final)
        if output_probe.video.codec_name != "h264":
            raise ReviewError("corrected render did not produce H.264")
        _validate_rendered_audio(actual_source, output_probe)
        if context["mode"] == "b2" and not output_probe.has_audio:
            raise ReviewError("corrected B2 render lost its admitted mix")
        if (actual_source.duration_ticks is None or output_probe.duration_ticks is None or
                abs(actual_source.duration_ticks - output_probe.duration_ticks) > 2000):
            raise ReviewError("corrected render duration differs from its source")
        media.timeout_seconds = max(0.001, deadline - time.monotonic())
        decoded = media._run((str(media.ffmpeg_path), "-hide_banner", "-loglevel", "error", "-nostdin",
            "-xerror", "-i", str(final), "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"))
        if decoded.returncode != 0 or decoded.stderr.strip():
            raise ReviewError("corrected render full audio/video decode failed")
        _inputs(context, deadline)
        if result_path.exists():
            _checkpoint(result, context, raw, identity, generation, deadline)
            return result
        warnings = list(manifest["warnings"])
        if context["retained_mix"] is not None:
            from engine.dubflow.mix.adapter import parse_mix_json
            warnings.extend(w for w in parse_mix_json(context["retained_mix"]["mix_document_json"])["warnings"] if w not in warnings)
        qc = {"schema_version":1, "status":"passed", "mode":context["mode"], "source_probe":actual_probe,
            "output_probe":output_probe.to_dict(), "warnings":warnings, "full_av_decode":True,
            "context_sha256":_digest(raw), "execution_identity":identity, "active_graph_published":False}
        _atomic(generation / "qc_report.json", _encode(qc, MAX_PACKET))
        artifacts = {}
        for role, name in ARTIFACTS.items():
            path = generation / name
            digest, size = _file_hash(path, deadline)
            artifacts[role] = {"path":str(path), "sha256":digest, "size_bytes":str(size)}
        result = {"schema_version":1, "kind":"review_private_render_candidate", "job_id":before.job_id,
            "mode":context["mode"], "context_sha256":_digest(raw), "execution_identity":identity,
            "after_snapshot_sha256":_digest(context["after_snapshot_json"].encode("utf-8")),
            "artifacts":artifacts, "active_graph_published":False, "correction_ready":False}
        _atomic(result_path, _encode(result, MAX_PACKET))
        return result


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("context", "output-root", "ffmpeg", "ffprobe", "media-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--context-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.stdin.buffer.read(1) != b"\x01":
            raise ValueError("render requires owned Python -I -S -B and native containment byte gate")
        _imports()
        from engine.dubflow.review.export import _hash
        from engine.dubflow.review.render_execution import render_candidate, _read_packet
        from engine.dubflow.review.synthesis_input import _encode, _digest
        _hash(args.context_sha256)
        raw = _read_packet(args.context)
        if _digest(raw) != args.context_sha256:
            raise ValueError("render context differs from its native hash")
        result = render_candidate(raw, output_root=args.output_root,
            ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe, media_root=args.media_root)
        sys.stdout.buffer.write(_encode(result, MAX_PACKET)); sys.stdout.buffer.flush()
        return 0
    except Exception as error:
        sys.stderr.write(json.dumps({"code":"REVIEW_RENDER_EXECUTION_FAILED", "message":str(error)[:2048]}, ensure_ascii=True)+"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

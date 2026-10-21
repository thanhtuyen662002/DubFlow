"""Owned checkpointable private mix after native original/cue admission.

This producer uses existing speech, never ASR/OCR/translation/TTS or SQLite.
Native code supplies all input roots and verifies outputs before returning.
Private candidates are not retained graph/export/undo objects.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

MAX_PACKET = 64 * 1024 * 1024


def _imports():
    if __package__ in (None, ""):
        sys.path.append(str(Path(sys.executable).resolve().parent / "Lib/site-packages"))
        sys.path.append(str(Path(__file__).resolve().parents[3]))


def prepare_composition(raw: bytes):
    from engine.dubflow.mix import FileSource
    from engine.dubflow.mix.adapter import parse_mix_json
    from engine.dubflow.tts import TtsInput
    from engine.dubflow.tts.adapter import _parse_point
    from engine.dubflow.review.audio_composition import PinnedDocument, compose_audio, replace_audio_cue
    from engine.dubflow.review.production import ReviewError
    from engine.dubflow.review.synthesis_input import read_cue_synthesis_input
    from engine.dubflow.review.wire import decode_review_json, read_review_snapshot
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_PACKET:
        raise ReviewError("native mix context exceeds its byte bound")
    value = decode_review_json(raw.decode("utf-8"))
    fields = {"schema_version", "kind", "manifest_sha256", "before_snapshot_json", "after_snapshot_json",
              "source_graph_json", "request_json", "request_sha256", "source_audio", "original_tts_document",
              "mix_template", "accepted_tts_document"}
    if (not isinstance(value, dict) or set(value) != fields or type(value["schema_version"]) is not int or
        value["schema_version"] != 1 or value["kind"] != "review_native_mix_context"):
        raise ReviewError("unknown native mix context requires explicit compatibility")
    for field in ("before_snapshot_json", "after_snapshot_json", "source_graph_json", "request_json"):
        if not isinstance(value[field], str):
            raise ReviewError("mix context requires exact source JSON strings")
    before = read_review_snapshot(decode_review_json(value["before_snapshot_json"]))
    after = read_review_snapshot(decode_review_json(value["after_snapshot_json"]))
    graph = decode_review_json(value["source_graph_json"])
    if (not isinstance(graph, dict) or type(graph.get("schema_version")) is not int or graph.get("schema_version") != 1 or graph.get("kind") != "review_original_graph" or
        graph.get("mode") != "b2" or graph.get("revision") != "0" or before.revision != 0 or
        graph.get("job_id") != before.job_id or graph.get("manifest_sha256") != value["manifest_sha256"] or
        graph.get("time_base") != {"numerator":before.time_base[0], "denominator":before.time_base[1]}):
        raise ReviewError("mix context differs from the original native graph")
    request = read_cue_synthesis_input(value["request_json"].encode("utf-8"), expected_hash=value["request_sha256"])
    source_pin = value["source_audio"]
    if not isinstance(source_pin, dict) or set(source_pin) != {"path", "sha256", "size_bytes", "artifact_root"}:
        raise ReviewError("source audio descriptor is malformed")

    def path(raw, root):
        from engine.dubflow.mix import streaming
        if not isinstance(raw, str) or not isinstance(root, str):
            raise ReviewError("native mix paths require strings")
        candidate, admitted = Path(raw), Path(root)
        if (not candidate.is_absolute() or not admitted.is_absolute() or ".." in candidate.parts or
            ".." in admitted.parts or not candidate.is_relative_to(admitted)):
            raise ReviewError("native mix path is outside its admitted root")
        streaming._plain(candidate); streaming._plain(admitted)
        return candidate

    source_path = path(source_pin["path"], source_pin["artifact_root"])
    if (type(source_pin["size_bytes"]) is not int or not 44 <= source_pin["size_bytes"] <= 2**32+7 or
        source_path.stat().st_size != source_pin["size_bytes"]):
        raise ReviewError("original source audio size changed")

    def document(name):
        pin = value[name]
        fields = {"path", "sha256", "size_bytes", "artifact_root", "metadata_root"}
        if not isinstance(pin, dict) or set(pin) != fields:
            raise ReviewError("native mix metadata descriptor is malformed")
        path(pin["path"], pin["metadata_root"])
        path(pin["artifact_root"], pin["artifact_root"])
        return PinnedDocument.from_admitted_descriptor(pin)

    speech, template, accepted = (document(name) for name in ("original_tts_document", "mix_template", "accepted_tts_document"))
    mix = parse_mix_json(template.read())
    inputs = graph.get("canonical_inputs")
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 10000:
        raise ReviewError("native original graph lacks bounded canonical inputs")
    segments = []
    for item in inputs:
        if not isinstance(item, dict):
            raise ReviewError("canonical input requires an object")
        segment = TtsInput(item["segment_id"], item["source_utterance_id"], item["text"],
            _parse_point(item["start"], "start"), _parse_point(item["end"], "end"),
            source_language=item["source_language"], target_language=item["target_language"], confidence=item["confidence"],
            render_window_end=_parse_point(item["render_window_end"], "window") if "render_window_end" in item else None)
        if segment.to_dict() != item:
            raise ReviewError("canonical mix input representation changed")
        segments.append(segment)
    source = FileSource(source_path, _parse_point(mix["source_start"], "start"), _parse_point(mix["source_end"], "end"),
                        source_id=mix["source_id"], layout=mix["source_layout"])
    current = compose_audio(before, manifest_sha256=value["manifest_sha256"], source=source,
        source_sha256=source_pin["sha256"], canonical_inputs=segments,
        assignments={item.segment_id:speech for item in segments}, mix_template=template)
    return replace_audio_cue(current, after, request=request, replacement_document=accepted)


def produce_mix(raw: bytes, *, output_root: Path):
    from engine.dubflow.review.audio_composition import mix_audio_candidate
    from engine.dubflow.review.production import ReviewError
    from engine.dubflow.review.synthesis_input import _digest, _encode
    composition = prepare_composition(raw)
    output = Path(output_root)
    if not output.is_absolute() or ".." in output.parts:
        raise ReviewError("native private mix root must be absolute")
    mix = mix_audio_candidate(composition, output_root=output)
    composition_raw = _encode(composition.to_dict(), MAX_PACKET).decode("utf-8")
    mix_raw = mix.to_json() + "\n"
    result = {"schema_version":1, "kind":"review_private_mix_candidate", "context_sha256":_digest(raw),
        "composition_json":composition_raw, "composition_sha256":composition.input_hash,
        "mix_document_json":mix_raw, "mix_document_sha256":_digest(mix_raw.encode("utf-8")),
        "active_graph_published":False}
    _encode(result, MAX_PACKET)
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("context", "output-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--context-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.stdin.buffer.read(1) != b"\x01":
            raise ValueError("mix execution requires owned Python -I -S -B and native byte gate")
        _imports()
        from engine.dubflow.review.export import _json_bytes
        from engine.dubflow.review.synthesis_input import _encode
        result = produce_mix(_json_bytes(args.context, args.context_sha256), output_root=args.output_root)
        sys.stdout.buffer.write(_encode(result, MAX_PACKET)); sys.stdout.buffer.flush()
        return 0
    except Exception as error:
        sys.stderr.write(json.dumps({"code":"REVIEW_MIX_EXECUTION_FAILED", "message":str(error)[:2048]}, ensure_ascii=True)+"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

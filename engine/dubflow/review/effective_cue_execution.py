"""Isolated metadata preparation from native-admitted current cue bindings.

The byte gate and sealed context/binding hashes are supplied by the supervisor.
No inference, provisioning, media creation or durable SQL is performed here.
Native code independently admits the returned request and composition.
"""
from __future__ import annotations

from pathlib import Path
import sys

LIMIT = 64 * 1024 * 1024


def _imports():
    if __package__ in (None, ""):
        sys.path.append(str(Path(sys.executable).resolve().parent / "Lib/site-packages"))
        sys.path.append(str(Path(__file__).resolve().parents[3]))


def prepare_bound_cue(context_raw: bytes, bindings_raw: bytes, *, app_root: Path, profile_path: Path) -> dict:
    from engine.dubflow.mix import FileSource, streaming
    from engine.dubflow.review.audio_composition import PinnedDocument
    from engine.dubflow.review.effective_cue_plan import prepare_effective_cue
    from engine.dubflow.review.production import ReviewError
    from engine.dubflow.review.synthesis_input import _digest, _encode
    from engine.dubflow.review.wire import decode_review_json, read_review_snapshot
    from engine.dubflow.tts import TtsInput
    from engine.dubflow.tts.adapter import _parse_point
    if not isinstance(bindings_raw, bytes) or not 1 <= len(bindings_raw) <= LIMIT:
        raise ReviewError("native current cue bindings exceed their byte bound")
    value = decode_review_json(bindings_raw.decode("utf-8"))
    fields = {"schema_version", "kind", "current_token", "content_composition_json",
              "content_composition_sha256", "composition_snapshot_json", "composition_snapshot_sha256",
              "source_audio", "canonical_inputs", "documents", "mix_template"}
    if (not isinstance(value, dict) or set(value) != fields or type(value["schema_version"]) is not int or
        value["schema_version"] != 1 or value["kind"] != "review_native_effective_cue_bindings"):
        raise ReviewError("unknown native current cue bindings require explicit compatibility")
    for raw_key, pin_key in (("content_composition_json", "content_composition_sha256"),
                             ("composition_snapshot_json", "composition_snapshot_sha256")):
        if (not isinstance(value[raw_key], str) or not 1 <= len(value[raw_key].encode()) <= LIMIT or
            _digest(value[raw_key].encode()) != value[pin_key]):
            raise ReviewError("native current cue binding changed exact content bytes")
    composition = decode_review_json(value["content_composition_json"])
    source_pin = value["source_audio"]
    if not isinstance(source_pin, dict) or set(source_pin) != {"path", "sha256", "size_bytes", "artifact_root"}:
        raise ReviewError("native source PCM binding is malformed")
    path, root = Path(source_pin["path"]), Path(source_pin["artifact_root"])
    if (not path.is_absolute() or not root.is_absolute() or ".." in path.parts or ".." in root.parts or
        not path.is_relative_to(root)):
        raise ReviewError("native source PCM is outside its admitted root")
    streaming._plain(path); streaming._plain(root)
    if (type(source_pin["size_bytes"]) is not int or source_pin["size_bytes"] <= 0 or
        path.stat().st_size != source_pin["size_bytes"] or
        composition["source"]["path"] != str(path) or composition["source"]["sha256"] != source_pin["sha256"]):
        raise ReviewError("native source PCM differs from its admitted composition")
    source = composition["source"]
    source = FileSource(path, _parse_point(source["start"], "start"), _parse_point(source["end"], "end"),
                        source_id=source["source_id"], layout=source["layout"])

    def document(item):
        fields = {"path", "sha256", "size_bytes", "artifact_root"}
        if isinstance(item, dict) and "metadata_root" in item:
            fields.add("metadata_root")
        if not isinstance(item, dict) or set(item) != fields:
            raise ReviewError("native admitted document binding is malformed")
        return PinnedDocument(Path(item["path"]), item["sha256"], item["size_bytes"],
            Path(item["artifact_root"]), Path(item["metadata_root"]) if "metadata_root" in item else None)
    documents = value["documents"]
    originals = value["canonical_inputs"]
    if (not isinstance(documents, list) or not 1 <= len(documents) <= 10000 or
        not isinstance(originals, list) or not 1 <= len(originals) <= 10000):
        raise ReviewError("native current cue inventory exceeds its bound")
    inputs = []
    for item in originals:
        fields = {"segment_id", "source_utterance_id", "text", "normalized_text", "start", "end",
                  "source_language", "target_language", "confidence"}
        if isinstance(item, dict) and "render_window_end" in item:
            fields.add("render_window_end")
        if not isinstance(item, dict) or set(item) != fields:
            raise ReviewError("native original cue binding is malformed")
        segment = TtsInput(item["segment_id"], item["source_utterance_id"], item["text"],
            _parse_point(item["start"], "start"), _parse_point(item["end"], "end"),
            normalized_text=item["normalized_text"], source_language=item["source_language"],
            target_language=item["target_language"], confidence=item["confidence"],
            render_window_end=_parse_point(item["render_window_end"], "window") if "render_window_end" in item else None)
        if _encode(segment.to_dict(), LIMIT) != _encode(item, LIMIT):
            raise ReviewError("native original cue binding changed canonical mapping/confidence")
        inputs.append(segment)
    return prepare_effective_cue(context_raw, expected_token=value["current_token"],
        composition_raw=value["content_composition_json"].encode(),
        composition_snapshot=read_review_snapshot(decode_review_json(value["composition_snapshot_json"])),
        source=source, source_sha256=source_pin["sha256"], original_inputs=inputs,
        documents=tuple(document(item) for item in documents), mix_template=document(value["mix_template"]),
        app_root=app_root, profile_path=profile_path)


def main(argv=None):
    import argparse, json
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("context", "bindings", "app-root", "profile"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("context-sha256", "bindings-sha256"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.stdin.buffer.read(1) != b"\x01":
            raise ValueError("current cue preparation requires owned Python -I -S -B and native byte gate")
        _imports()
        from engine.dubflow.review.export import _json_bytes
        from engine.dubflow.review.synthesis_input import _encode
        packet = prepare_bound_cue(_json_bytes(args.context, args.context_sha256),
            _json_bytes(args.bindings, args.bindings_sha256), app_root=args.app_root, profile_path=args.profile)
        sys.stdout.buffer.write(_encode(packet, LIMIT) + b"\n")
        return 0
    except Exception as error:
        condition = str(error).encode("utf-8")[:1024].decode("utf-8", errors="ignore")
        sys.stderr.write(json.dumps({"code":"REVIEW_EFFECTIVE_CUE_PREPARATION_INVALID","message":condition},ensure_ascii=True)+"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

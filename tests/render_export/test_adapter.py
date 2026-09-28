from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from engine.dubflow.asr import TimeBase, TimeInterval, TimePoint
from engine.dubflow.export import (
    DeterministicFixtureRenderer,
    ExportAsset,
    ExportConfig,
    ExportError,
    ExportProvenance,
    ExportRequest,
    ExportStageError,
    LocalExportAdapter,
    MediaAsset,
    RenderBackendError,
    RenderRequest,
    RenderedOutput,
    parse_export_json,
    validate_export_result,
)


BASE = TimeBase(1, 1000)
SOURCE_HASH = "sha256:" + "1" * 64
SUBTITLE_HASH = "sha256:" + "2" * 64
DUB_HASH = "sha256:" + "3" * 64


def point(ticks: int) -> TimePoint:
    return TimePoint(ticks, BASE)


def media(*, has_audio: bool = True) -> MediaAsset:
    return MediaAsset("source", "媒体/source.mp4", SOURCE_HASH, TimeInterval(point(100), point(2100)), 1080, 1920, 90, has_audio)


def make_request(root: Path, *, subtitle=None, dub_audio=None, preserve_original_audio=True, has_audio=True) -> ExportRequest:
    return ExportRequest(
        media(has_audio=has_audio),
        root / "rendered.mp4",
        subtitle=subtitle,
        dub_audio=dub_audio,
        preserve_original_audio=preserve_original_audio,
    )


def make_adapter(request: ExportRequest, backend, *, config: ExportConfig | None = None, fallback=None) -> LocalExportAdapter:
    config = config or ExportConfig(requested_profile="fixture")
    return LocalExportAdapter(
        backend,
        config=config,
        provenance=ExportProvenance(
            "dubflow-export-test",
            "1.0.0",
            "fixture-renderer",
            "1",
            "python-stdlib",
            "timeline-v1",
            config.to_hash(),
            request.input_hash(),
            config.requested_profile,
            "fixture",
        ),
        fallback_backend=fallback,
    )


class ExportAdapterTests(unittest.TestCase):
    def test_subtitle_only_preserves_original_audio_and_publishes_pack_atomically(self) -> None:
        subtitle = ExportAsset("subtitle", "vietsub", "subtitle.ass", SUBTITLE_HASH, "ass")
        with tempfile.TemporaryDirectory() as directory:
            request = make_request(Path(directory), subtitle=subtitle)
            result = make_adapter(request, DeterministicFixtureRenderer()).export(request)
            self.assertEqual(result.audio_mode, "original")
            self.assertEqual(result.output["container"], "mp4")
            self.assertEqual(result.output["video_codec"], "h264")
            self.assertEqual(result.output["audio_codec"], "aac")
            self.assertEqual(result.output["width"], 1080)
            self.assertEqual(result.output["height"], 1920)
            self.assertEqual(result.output["rotation"], 90)
            self.assertTrue(Path(result.output["path"]).is_file())
            self.assertTrue(Path(result.editable_pack["path"]).is_file())
            self.assertEqual([item["kind"] for item in result.editable_pack["assets"]], ["subtitle"])
            self.assertEqual(result.output["content_hash"], "sha256:" + hashlib.sha256(Path(result.output["path"]).read_bytes()).hexdigest())
            self.assertFalse(list(Path(directory).glob("*.partial")))
            validate_export_result(result.to_dict())

    def test_dubbed_audio_and_subtitle_are_explicit_assets(self) -> None:
        subtitle = ExportAsset("subtitle", "vietsub", "subtitle.srt", SUBTITLE_HASH, "srt")
        audio = ExportAsset("dub_audio", "dub", "dub.wav", DUB_HASH, "wav", TimeInterval(point(100), point(2100)))
        with tempfile.TemporaryDirectory() as directory:
            request = make_request(Path(directory), subtitle=subtitle, dub_audio=audio, preserve_original_audio=False)
            result = make_adapter(request, DeterministicFixtureRenderer()).export(request)
            self.assertEqual(result.audio_mode, "dubbed")
            self.assertEqual([item["kind"] for item in result.editable_pack["assets"]], ["subtitle", "dub_audio"])

    def test_source_without_audio_does_not_create_fake_empty_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request = make_request(Path(directory), has_audio=False)
            result = make_adapter(request, DeterministicFixtureRenderer()).export(request)
            self.assertEqual(result.audio_mode, "none")
            self.assertIsNone(result.output["audio_codec"])
            self.assertEqual(result.editable_pack["assets"], [])

    def test_invalid_primary_metadata_uses_software_fallback_and_records_evidence(self) -> None:
        class BadRenderer:
            def render(self, request: RenderRequest) -> RenderedOutput:
                request.temporary_path.write_bytes(b"bad-metadata")
                return RenderedOutput("mp4", "h264", "aac", 1, 1, 0, request.source.duration)

        with tempfile.TemporaryDirectory() as directory:
            request = make_request(Path(directory))
            result = make_adapter(request, BadRenderer(), fallback=DeterministicFixtureRenderer()).export(request)
            self.assertEqual(result.provenance.hardware_profile, "cpu")
            self.assertTrue(all(item.fallback_used for item in result.failures))
            self.assertTrue(any(item.code == "OUTPUT_DIMENSION_CHANGED" for item in result.failures))
            self.assertTrue(Path(result.output["path"]).is_file())

    def test_missing_primary_output_is_typed_and_falls_back(self) -> None:
        class MissingOutputRenderer:
            def render(self, request: RenderRequest) -> RenderedOutput:
                return RenderedOutput("mp4", "h264", "aac", request.source.width, request.source.height, request.source.rotation, request.source.duration)

        with tempfile.TemporaryDirectory() as directory:
            request = make_request(Path(directory))
            result = make_adapter(request, MissingOutputRenderer(), fallback=DeterministicFixtureRenderer()).export(request)
            self.assertTrue(any(item.code == "RENDER_OUTPUT_MISSING" for item in result.failures))
            self.assertTrue(all(item.fallback_used for item in result.failures))
            self.assertTrue(request.output_path.is_file())

    def test_failed_render_quarantines_partial_and_never_reports_final_path(self) -> None:
        renderer = DeterministicFixtureRenderer(fail_with=RenderBackendError("GPU_OOM", "out of memory", retryable=False))
        with tempfile.TemporaryDirectory() as directory:
            request = make_request(Path(directory))
            with self.assertRaises(ExportStageError) as context:
                make_adapter(request, renderer).export(request)
            self.assertFalse(request.output_path.exists())
            self.assertTrue(list(Path(directory).glob("*.partial.failed")))
            self.assertTrue(any(item.code == "GPU_OOM" for item in context.exception.failures))

    def test_changed_condition_retry_and_existing_output_guard(self) -> None:
        class RetryRenderer:
            def __init__(self) -> None:
                self.calls = 0

            def render(self, request: RenderRequest) -> RenderedOutput:
                self.calls += 1
                if self.calls == 1:
                    raise RenderBackendError("TEMP", "retry with software", retryable=True)
                request.temporary_path.write_bytes(b"retry-ok")
                return RenderedOutput("mp4", "h264", "aac", request.source.width, request.source.height, request.source.rotation, request.source.duration)

        with tempfile.TemporaryDirectory() as directory:
            request = make_request(Path(directory))
            renderer = RetryRenderer()
            result = make_adapter(request, renderer, config=ExportConfig(requested_profile="fixture", max_attempts=2)).export(request)
            self.assertEqual(renderer.calls, 2)
            self.assertEqual(result.failures[0].attempt, 1)
            with self.assertRaisesRegex(ValueError, "OUTPUT_EXISTS"):
                make_adapter(request, DeterministicFixtureRenderer()).export(request)

    def test_request_rejects_ambiguous_audio_mode_and_wire_duplicates(self) -> None:
        audio = ExportAsset("dub_audio", "dub", "dub.wav", DUB_HASH, "wav")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "INVALID_AUDIO_MODE"):
                make_request(Path(directory), dub_audio=audio, preserve_original_audio=True)
            request = make_request(Path(directory))
            result = make_adapter(request, DeterministicFixtureRenderer()).export(request)
            invalid = result.to_dict()
            invalid["output"]["duration_end"]["ticks"] = "-0"
            with self.assertRaisesRegex(ValueError, "canonical decimal"):
                validate_export_result(invalid)
            duplicate = result.to_json().replace('"kind":"export_result"', '"kind":"export_result","kind":"export_result"', 1)
            with self.assertRaisesRegex(ValueError, "DUPLICATE_FIELD"):
                parse_export_json(duplicate)

    def test_request_rejects_output_path_aliasing_an_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.mp4"
            request_source = MediaAsset(
                "source",
                str(source_path),
                SOURCE_HASH,
                TimeInterval(point(100), point(2100)),
                1080,
                1920,
                0,
                True,
            )
            with self.assertRaisesRegex(ValueError, "must not overwrite the source"):
                ExportRequest(request_source, source_path)

    def test_schema_document_matches_result_surface(self) -> None:
        root = Path(__file__).resolve().parents[2]
        schema = json.loads((root / "contracts" / "export" / "schema-v1.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            request = make_request(Path(directory))
            result = make_adapter(request, DeterministicFixtureRenderer()).export(request)
            self.assertEqual(set(result.to_dict()), set(schema["required"]))
            self.assertEqual(schema["$id"], "https://dubflow.local/contracts/export/schema-v1.json")


if __name__ == "__main__":
    unittest.main()

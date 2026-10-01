from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
import zlib

from engine.dubflow.ocr.production import AppOwnedOcrBackend, ProductionOcrError, TextIntelligenceManifest
from engine.dubflow.worker.production_job import TextCue


def write_rgb_png(path: Path, width: int = 64, height: int = 64) -> None:
    pixels = bytearray(width * height * 3)
    for y in range(40, 49):
        for x in range(7, 57):
            offset = (y * width + x) * 3
            pixels[offset:offset + 3] = b"\xff\xff\xff"
    rows = b"".join(b"\x00" + pixels[row * width * 3:(row + 1) * width * 3] for row in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    payload = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    path.write_bytes(payload)


class ProductionTextIntelligenceTests(unittest.TestCase):
    def test_real_frame_detector_fuses_overlapping_asr_and_hashes_pixels(self) -> None:
        with TemporaryDirectory(prefix="dubflow-ocr-frame-") as directory:
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"source")
            frame = root / "frame-00000001.png"
            write_rgb_png(frame)
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            result = AppOwnedOcrBackend(model_manifest_path=Path("models/manifests/text-intelligence-v1.json")).analyze(
                job_id="job-frame",
                source_path=source,
                source_hash=source_hash,
                asr_cues=(TextCue("cue-1", 0, 1000, "Xin chao"),),
                frame_paths=(frame,),
            )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.observation_count, 1)
        self.assertIn("OCR_TEXT_FUSED_FROM_ASR_CUES", result.warnings)
        self.assertEqual(result.to_dict()["tracks"][0]["segments"][0]["text"], "Xin chao")
        self.assertTrue(result.to_dict()["provenance"]["frame_manifest_sha256"].startswith("sha256:"))

    def test_missing_detector_output_is_a_truthful_degraded_result(self) -> None:
        with TemporaryDirectory(prefix="dubflow-ocr-production-") as directory:
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"source")
            result = AppOwnedOcrBackend(model_manifest_path=Path("models/manifests/text-intelligence-v1.json")).analyze(
                job_id="job-1",
                source_path=source,
                source_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
                asr_cues=(TextCue("cue-1", 0, 1000, "Hello"),),
            )
        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.observation_count, 0)
        self.assertIn("OCR_DETECTOR_OUTPUT_UNAVAILABLE", result.warnings)
        self.assertTrue(result.to_dict()["provenance"]["source_sha256"].startswith("sha256:"))

    def test_valid_polygon_observations_are_tracked_and_asr_attached(self) -> None:
        with TemporaryDirectory(prefix="dubflow-ocr-production-") as directory:
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"source")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            observations = root / "clip.ocr.json"
            observations.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_sha256": source_hash,
                        "frame_count": 2,
                        "observations": [
                            {
                                "observation_id": "text-1",
                                "frame_index": 0,
                                "start_ticks": 0,
                                "end_ticks": 1000,
                                "text": "Hello",
                                "polygon": {"points": [[20, 20], [220, 20], [220, 60], [20, 60]]},
                                "confidence": "0.96",
                                "role_hint": "dialogue",
                                "angle_milli_degrees": 1200,
                            },
                            {
                                "observation_id": "text-2",
                                "frame_index": 1,
                                "start_ticks": 900,
                                "end_ticks": 1800,
                                "text": "Hello",
                                "bbox": {"x": 20, "y": 20, "width": 200, "height": 40},
                                "confidence": "0.96",
                                "role_hint": "dialogue",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            frame = root / "frame-00000001.png"
            frame.write_bytes(b"frame")
            result = AppOwnedOcrBackend(model_manifest_path=Path("models/manifests/text-intelligence-v1.json")).analyze(
                job_id="job-1",
                source_path=source,
                source_hash=source_hash,
                asr_cues=(TextCue("cue-1", 0, 1000, "Hello"),),
                frame_paths=(frame,),
            )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.observation_count, 2)
        self.assertEqual(result.orientation_observation_count, 1)
        document = result.to_dict()
        self.assertEqual(document["tracks"][0]["role"], "dialogue")
        self.assertIn("polygon", document["tracks"][0]["segments"][0])
        self.assertEqual(document["oriented_tracks"][0]["decision"], "keep")
        self.assertEqual(document["frame_evidence"]["frame_count"], 2)
        self.assertTrue(document["frame_evidence"]["media_sampled"])
        self.assertTrue(document["frame_evidence"]["frame_manifest_sha256"].startswith("sha256:"))

    def test_source_hash_mismatch_fails_closed(self) -> None:
        with TemporaryDirectory(prefix="dubflow-ocr-production-") as directory:
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"source")
            (root / "clip.ocr.json").write_text(
                json.dumps({"schema_version": 1, "source_sha256": "0" * 64, "observations": []}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ProductionOcrError, "OCR_SOURCE_MISMATCH"):
                AppOwnedOcrBackend(model_manifest_path=Path("models/manifests/text-intelligence-v1.json")).analyze(
                    job_id="job-1",
                    source_path=source,
                    source_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
                )

    def test_manifest_rejects_unpinned_artifact(self) -> None:
        with TemporaryDirectory(prefix="dubflow-ocr-manifest-") as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "profile_id": "test",
                        "runtime": {"id": "runtime", "version": "1", "app_owned": True},
                        "artifacts": [{"id": "bad", "path": "../escape", "sha256": "0" * 64, "size_bytes": 0}],
                        "license": {"spdx": "Apache-2.0"},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ProductionOcrError, "OCR_MODEL_MANIFEST_INVALID"):
                TextIntelligenceManifest.load(path)


if __name__ == "__main__":
    unittest.main()

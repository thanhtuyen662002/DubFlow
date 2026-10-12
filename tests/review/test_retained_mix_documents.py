import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.mix.adapter import parse_mix_json
from engine.dubflow.review.production import ReviewError
from engine.dubflow.review.retained_mix_documents import prepare_retained_mix_document, MAX_PACKET
from engine.dubflow.review.synthesis_input import _digest, _encode
from schema_assertions import assert_schema

ROLES = ("original_audio", "dialogue_stem", "final_mix")
REPO = Path(__file__).resolve().parents[2]


class RetainedMixDocumentTests(unittest.TestCase):
    def context(self, root):
        # Exact metadata fixture from a prior real producer packet, with no
        # native admission or physical long-media qualification claimed here.
        fixture = json.loads((REPO / "tests/review/native_mix_candidate_v1.json").read_text(encoding="utf-8"))
        packet = fixture["packet"]
        candidate = packet["mix_document_json"]
        mix = parse_mix_json(candidate)
        job = "job-retained-metadata"
        output = Path(root) / "output"
        namespace = hashlib.sha256(job.encode()).hexdigest()
        pins = []
        for role in ROLES:
            artifact = mix[role]
            relative = Path(".dubflow-review-media-v1") / namespace / (artifact["content_hash"][7:] + ".wav")
            audio = {"path_hint": str(output / relative),
                     "locator": {"schema_version": 1, "volume_id": "fixture-native-volume-pin", "root_kind": "output",
                                 "relative_path": str(relative), "content_hash": artifact["content_hash"],
                                 "size_bytes": str(44 + 4 * artifact["frame_count"])},
                     "sample_rate": 48000, "channels": 2, "frame_count": str(artifact["frame_count"])}
            pins.append({"role": role, "audio": audio})
        return {"schema_version": 1, "kind": "review_native_retained_mix_context", "job_id": job,
                "output_root": str(output), "volume_id": "fixture-native-volume-pin",
                "candidate_mix_json": candidate, "candidate_mix_sha256": _digest(candidate.encode()),
                "composition_sha256": mix["provenance"]["input_hash"], "retained_mix_audio": pins}

    def test_only_three_paths_change_and_all_semantic_evidence_is_preserved(self):
        with TemporaryDirectory(prefix="dubflow-retained-metadata-") as root:
            context = self.context(root)
            raw = _encode(context, MAX_PACKET)
            assert_schema(context, "native-retained-mix-context-v1.json")
            before = copy.deepcopy(context)
            result = prepare_retained_mix_document(raw)
            expected = parse_mix_json(context["candidate_mix_json"])
            for role, pin in zip(ROLES, context["retained_mix_audio"]):
                expected[role]["path"] = pin["audio"]["path_hint"]
            self.assertEqual(parse_mix_json(result["mix_document_json"]), expected)
            self.assertEqual(result["mix_document_sha256"], _digest(result["mix_document_json"].encode()))
            self.assertEqual(result["context_sha256"], _digest(raw))
            self.assertTrue(result["native_media_revalidation_required"])
            self.assertFalse(result["active_graph_published"])
            self.assertEqual(context, before)
            self.assertEqual(prepare_retained_mix_document(raw), result)
            assert_schema(result, "retained-mix-document-v1.json")

    def test_metadata_transform_does_not_read_media_or_run_inference(self):
        with TemporaryDirectory(prefix="dubflow-retained-no-media-") as root:
            raw = _encode(self.context(root), MAX_PACKET)
            with (patch.object(Path, "open", side_effect=AssertionError("media I/O is native-owned")),
                  patch("subprocess.Popen", side_effect=AssertionError("no producer or inference"))):
                result = prepare_retained_mix_document(raw)
            self.assertTrue(result["native_media_revalidation_required"])
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_native_oracle_fixture_matches_current_metadata_adapter(self):
        fixture = json.loads((REPO / "tests/review/native_retained_mix_document_v1.json").read_text(encoding="utf-8"))
        entry = fixture["windows" if os.name == "nt" else "posix"]
        self.assertEqual(prepare_retained_mix_document(_encode(entry["context"], MAX_PACKET)), entry["prepared"])

    def test_role_volume_job_path_hash_and_pcm_substitutions_refuse(self):
        with TemporaryDirectory(prefix="dubflow-retained-pins-") as root:
            context = self.context(root)
            mutations = [
                lambda v: v.update(job_id="foreign-job"),
                lambda v: v.update(volume_id="foreign-volume"),
                lambda v: v.update(output_root="relative-output"),
                lambda v: v.update(composition_sha256="sha256:" + "a" * 64),
                lambda v: v["retained_mix_audio"].reverse(),
                lambda v: v["retained_mix_audio"].pop(),
                lambda v: v["retained_mix_audio"][1].update(role="original_audio"),
                lambda v: v["retained_mix_audio"][0]["audio"].update(path_hint=str(Path(root) / "elsewhere.wav")),
                lambda v: v["retained_mix_audio"][0]["audio"].update(sample_rate=16000),
                lambda v: v["retained_mix_audio"][0]["audio"].update(channels=True),
                lambda v: v["retained_mix_audio"][0]["audio"].update(verified=True),
                lambda v: v["retained_mix_audio"][0]["audio"].update(frame_count="0001"),
                lambda v: v["retained_mix_audio"][0]["audio"].update(frame_count="1036800001"),
                lambda v: v["retained_mix_audio"][0]["audio"]["locator"].update(content_hash="sha256:" + "0" * 64),
                lambda v: v["retained_mix_audio"][0]["audio"]["locator"].update(relative_path="../other.wav"),
                lambda v: v["retained_mix_audio"][0]["audio"]["locator"].update(size_bytes=60),
                lambda v: v["retained_mix_audio"][0]["audio"]["locator"].update(size_bytes="44"),
                lambda v: v["retained_mix_audio"][0]["audio"]["locator"].update(size_bytes="4294967304"),
                lambda v: v["retained_mix_audio"][0]["audio"]["locator"].update(root_kind="source"),
                lambda v: v["retained_mix_audio"][0]["audio"]["locator"].update(schema_version=True),
            ]
            for index, mutate in enumerate(mutations):
                with self.subTest(case=index):
                    value = copy.deepcopy(context); mutate(value)
                    with self.assertRaises((ReviewError, ValueError)):
                        prepare_retained_mix_document(_encode(value, MAX_PACKET))
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_exact_hash_closed_versions_and_duplicate_fields_refuse(self):
        with TemporaryDirectory(prefix="dubflow-retained-schema-") as root:
            context = self.context(root)
            for mutate in [lambda v: v.update(schema_version=2), lambda v: v.update(schema_version=True),
                           lambda v: v.update(kind="unknown"), lambda v: v.update(extra=True),
                           lambda v: v.update(candidate_mix_sha256="sha256:" + "a" * 64)]:
                value = copy.deepcopy(context); mutate(value)
                with self.assertRaises((ReviewError, ValueError)):
                    prepare_retained_mix_document(_encode(value, MAX_PACKET))
            duplicate = _encode(context, MAX_PACKET).replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1)
            with self.assertRaises((ReviewError, ValueError)):
                prepare_retained_mix_document(duplicate)
            value = copy.deepcopy(context)
            value["candidate_mix_json"] = value["candidate_mix_json"].replace('"schema_version":1', '"schema_version":1,"schema_version":1', 1)
            value["candidate_mix_sha256"] = _digest(value["candidate_mix_json"].encode())
            with self.assertRaises((ReviewError, ValueError)):
                prepare_retained_mix_document(_encode(value, MAX_PACKET))
            with self.assertRaises(ReviewError):
                prepare_retained_mix_document(b"")

    def test_owned_cli_gate_hash_refusal_and_positive_metadata_result(self):
        python = os.environ.get("DUBFLOW_TEST_PYTHON", sys.executable)
        with TemporaryDirectory(prefix="dubflow-retained-cli-") as root:
            raw = _encode(self.context(root), MAX_PACKET)
            path = Path(root) / "context.json"; path.write_bytes(raw)
            entry = REPO / "engine/dubflow/review/retained_mix_documents.py"
            for gate, pin, expected in ((b"", _digest(raw), 2), (b"\x01", "sha256:" + "a" * 64, 2), (b"\x01", _digest(raw), 0)):
                result = subprocess.run([python, "-I", "-S", "-B", str(entry), "--context", str(path), "--context-sha256", pin],
                                        input=gate, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, expected, result.stderr)
                if expected == 0:
                    self.assertEqual(result.stderr, b"")
                    self.assertEqual(json.loads(result.stdout), prepare_retained_mix_document(raw))
                else:
                    self.assertEqual(result.stdout, b"")
                    self.assertEqual(json.loads(result.stderr)["code"], "REVIEW_MIX_RETARGET_FAILED")
            self.assertEqual(list(Path(root).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()

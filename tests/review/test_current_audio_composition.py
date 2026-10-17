from copy import deepcopy
from dataclasses import replace
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.review import ReviewError
from engine.dubflow.review.audio_composition import (PinnedDocument, read_audio_composition,
                                                   replace_audio_cue, rebind_audio_composition)
from engine.dubflow.review.export import MAX_JSON_BYTES
from engine.dubflow.review.synthesis_input import _digest, _encode
import test_audio_composition as audio_helpers
from test_audio_composition import sha


class CurrentAudioCompositionTests(unittest.TestCase):
    def pins(self, original, current, directory):
        template = Path(directory) / "before/mix_document.json"
        documents = {item.document.sha256: item.document for item in current.cues}
        return dict(snapshot=current.snapshot, manifest_sha256=original.manifest_sha256,
            source=original.source, source_sha256=original.source_sha256,
            original_inputs=tuple(item.canonical_input for item in original.cues),
            documents=tuple(documents.values()),
            mix_template=PinnedDocument(template, sha(template), template.stat().st_size, template.parent))

    def read(self, current, pins, *, value=None, raw=None):
        if raw is None:
            raw = _encode(current.to_dict() if value is None else value, MAX_JSON_BYTES)
        return read_audio_composition(raw, expected_sha256=_digest(raw), **pins)

    def fixture(self, directory):
        helpers = audio_helpers.AudioCompositionTests()
        original, store, config, voice = helpers.fixture(directory)
        after, request, pin = helpers.correction(original, store, config, voice, Path(directory) / "first")
        first = replace_audio_cue(original, after, request=request, replacement_document=pin)
        return helpers, original, first, store, config, voice

    def test_reopened_current_assignment_preserves_prior_cue_during_next_voice_edit(self):
        with TemporaryDirectory(prefix="dubflow-review-current-audio-") as directory:
            helpers, original, first, store, config, voice = self.fixture(directory)
            immutable = {item.document.path: (item.document.path.read_bytes(), item.document.path.stat().st_mtime_ns)
                         for item in first.cues}
            reopened = self.read(first, self.pins(original, first, directory))
            another = replace(voice, voice_id="voice-b", voice_version="2")
            after, request, pin = helpers.correction(reopened, store, config, voice,
                Path(directory) / "second", cue_index=1, new_voice=another)
            second = replace_audio_cue(reopened, after, request=request, replacement_document=pin)
            recovered = self.read(second, self.pins(original, second, directory))
            self.assertEqual(recovered, second)
            self.assertEqual(recovered.cues[0], first.cues[0])
            self.assertEqual(recovered.cues[1].provenance.voice_id, "voice-b")
            self.assertEqual(len(recovered.to_dict()["documents"]), 2)
            for path, identity in immutable.items():
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), identity)

    def test_original_content_at_restored_revision_reopens_without_counter_normalization(self):
        with TemporaryDirectory(prefix="dubflow-review-current-restored-") as directory:
            _, original, _, _, _, _ = self.fixture(directory)
            restored = replace(original, snapshot=replace(original.snapshot, revision=2))
            reopened = self.read(restored, self.pins(original, restored, directory))
            self.assertEqual(reopened.snapshot.revision, 2)
            self.assertEqual(reopened.cues, original.cues)
            stale = self.pins(original, restored, directory)
            stale["snapshot"] = original.snapshot
            with self.assertRaisesRegex(ReviewError, "snapshot"):
                self.read(restored, stale)

    def test_immutable_content_rebinds_to_public_revision_and_playback_only(self):
        with TemporaryDirectory(prefix="dubflow-composition-project-") as directory:
            _, original, first, _, _, _ = self.fixture(directory)
            raw = _encode(first.to_dict(), MAX_JSON_BYTES)
            current = replace(first.snapshot, revision=4, findings=tuple(
                replace(item, output_preview=str(Path(directory) / "retained-video.mp4"))
                for item in first.snapshot.findings))
            pins = self.pins(original, first, directory); pins.pop("snapshot")
            paths = {item.document.path for item in first.cues} | {item.segment.path for item in first.cues}
            physical = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
            projected = rebind_audio_composition(raw, expected_sha256=first.input_hash,
                content_snapshot=first.snapshot, current_snapshot=current, **pins)
            self.assertEqual(projected.snapshot, current)
            self.assertEqual(projected.cues, first.cues)
            self.assertNotEqual(projected.input_hash, first.input_hash)
            prior, next_value = first.to_dict(), projected.to_dict()
            prior.pop("review_snapshot_sha256"); next_value.pop("review_snapshot_sha256")
            self.assertEqual(_encode(prior, MAX_JSON_BYTES), _encode(next_value, MAX_JSON_BYTES))
            self.assertEqual(raw, _encode(first.to_dict(), MAX_JSON_BYTES))
            for p, identity in physical.items():
                self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), identity)

    def test_projection_refuses_content_diagnostic_plan_or_nonmonotonic_identity_change(self):
        with TemporaryDirectory(prefix="dubflow-composition-project-refuse-") as directory:
            _, original, first, _, _, _ = self.fixture(directory)
            pins = self.pins(original, first, directory); pins.pop("snapshot")
            raw = _encode(first.to_dict(), MAX_JSON_BYTES)
            mutations = [replace(first.snapshot, revision=0),
                         replace(first.snapshot, job_id="foreign"),
                         replace(first.snapshot, last_plan=None),
                         replace(first.snapshot, time_base=("1", "2000")),
                         replace(first.snapshot, findings=tuple(reversed(first.snapshot.findings)))]
            for values in ({"translated_text": "Lost prior edit"}, {"voice": "foreign"},
                           {"source_text": "foreign"}, {"confidence": "0.3"},
                           {"warning": "new diagnostic"}, {"start_ticks": "1"}):
                changed = replace(first.snapshot.findings[0], **values)
                mutations.append(replace(first.snapshot, revision=3,
                    findings=(changed,) + first.snapshot.findings[1:]))
            for current in mutations:
                with self.subTest(current=current):
                    with patch.object(PinnedDocument, "read", side_effect=AssertionError("invalid projection read")):
                        with self.assertRaisesRegex(ReviewError, "composition projection"):
                            rebind_audio_composition(raw, expected_sha256=first.input_hash,
                                content_snapshot=first.snapshot, current_snapshot=current, **pins)

    def test_projection_still_revalidates_retained_wave_and_exact_content_pin(self):
        with TemporaryDirectory(prefix="dubflow-composition-project-physical-") as directory:
            _, original, first, _, _, _ = self.fixture(directory)
            pins = self.pins(original, first, directory); pins.pop("snapshot")
            raw = _encode(first.to_dict(), MAX_JSON_BYTES)
            current = replace(first.snapshot, revision=3)
            with self.assertRaisesRegex(ReviewError, "trusted pin"):
                rebind_audio_composition(raw, expected_sha256=original.input_hash,
                    content_snapshot=first.snapshot, current_snapshot=current, **pins)
            wave = first.cues[0].segment.path
            data = wave.read_bytes(); wave.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            with self.assertRaisesRegex(ValueError, "hash"):
                rebind_audio_composition(raw, expected_sha256=first.input_hash,
                    content_snapshot=first.snapshot, current_snapshot=current, **pins)

    def test_distinct_metadata_roots_reopen_existing_v2_encoding(self):
        with TemporaryDirectory(prefix="dubflow-review-current-roots-") as directory:
            _, original, first, _, _, _ = self.fixture(directory)
            current = replace(first, cues=tuple(replace(item, document=replace(
                item.document, metadata_root=item.document.path.parent)) for item in first.cues))
            self.assertEqual(current.to_dict()["schema_version"], 2)
            self.assertEqual(self.read(current, self.pins(original, current, directory)), current)

    def test_serialized_paths_never_supply_document_or_source_authority(self):
        with TemporaryDirectory(prefix="dubflow-review-current-authority-") as directory:
            _, original, first, _, _, _ = self.fixture(directory)
            pins = self.pins(original, first, directory)
            for field in ("path", "artifact_root", "metadata_root"):
                value = deepcopy(first.to_dict())
                value["documents"][0][field] = str(Path(directory) / "foreign")
                with patch.object(PinnedDocument, "read", side_effect=AssertionError("untrusted path read")):
                    with self.assertRaisesRegex(ReviewError, "document inventory"):
                        self.read(first, pins, value=value)
            value = deepcopy(first.to_dict()); value["source"]["path"] = str(Path(directory) / "foreign.wav")
            with patch.object(PinnedDocument, "read", side_effect=AssertionError("untrusted path read")):
                with self.assertRaisesRegex(ReviewError, "admitted source"):
                    self.read(first, pins, value=value)

    def test_current_metadata_cannot_drop_prior_edit_or_change_geometry_confidence_order(self):
        with TemporaryDirectory(prefix="dubflow-review-current-semantic-") as directory:
            _, original, first, _, _, _ = self.fixture(directory)
            pins = self.pins(original, first, directory)
            changes = []
            value = deepcopy(first.to_dict()); value["cues"][0]["tts_input"] = original.cues[0].canonical_input.to_dict(); changes.append(value)
            value = deepcopy(first.to_dict()); value["cues"][0]["tts_input"]["start"]["ticks"] = "1"; changes.append(value)
            value = deepcopy(first.to_dict()); value["cues"][0]["tts_input"]["source_utterance_id"] = "foreign"; changes.append(value)
            value = deepcopy(first.to_dict()); value["cues"][0]["tts_input"]["confidence"] = math.nextafter(
                value["cues"][0]["tts_input"]["confidence"], 0.0); changes.append(value)
            value = deepcopy(first.to_dict()); value["cues"].reverse(); changes.append(value)
            value = deepcopy(first.to_dict()); value["cues"][1] = deepcopy(value["cues"][0]); changes.append(value)
            for value in changes:
                with self.subTest(value=value):
                    with self.assertRaisesRegex(ReviewError, "canonical cue"):
                        self.read(first, pins, value=value)
            value = deepcopy(first.to_dict()); value["cues"].pop()
            with self.assertRaisesRegex(ReviewError, "every original cue"):
                self.read(first, pins, value=value)

    def test_document_assignment_producer_and_inventory_substitution_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-current-binding-") as directory:
            _, original, first, _, _, _ = self.fixture(directory)
            pins = self.pins(original, first, directory)
            value = deepcopy(first.to_dict()); value["cues"][0]["document_sha256"] = "sha256:" + "b" * 64
            with self.assertRaisesRegex(ReviewError, "no admitted document"):
                self.read(first, pins, value=value)
            value = deepcopy(first.to_dict()); value["mix_producer_pin"]["producer_version"] = "foreign"
            with self.assertRaisesRegex(ReviewError, "canonical reconstruction"):
                self.read(first, pins, value=value)
            value = deepcopy(first.to_dict()); value["documents"].append(value["documents"][0])
            with self.assertRaisesRegex(ReviewError, "document inventory"):
                self.read(first, pins, value=value)
            value = deepcopy(first.to_dict()); value["cues"][0]["document_sha256"] = original.cues[0].document.sha256
            with self.assertRaisesRegex(ReviewError, "canonical reconstruction"):
                self.read(first, pins, value=value)

    def test_unknown_or_noncanonical_encoding_and_untrusted_hash_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-current-wire-") as directory:
            _, original, first, _, _, _ = self.fixture(directory)
            pins = self.pins(original, first, directory)
            raw = _encode(first.to_dict(), MAX_JSON_BYTES)
            with self.assertRaisesRegex(ReviewError, "trusted pin"):
                read_audio_composition(raw, expected_sha256="sha256:" + "b" * 64, **pins)
            for update in ({"schema_version": 3}, {"schema_version": True}, {"unexpected": True}):
                with self.assertRaisesRegex(ReviewError, "explicit compatibility"):
                    self.read(first, pins, value={**first.to_dict(), **update})
            with self.assertRaisesRegex(ReviewError, "canonical reconstruction"):
                self.read(first, pins, raw=raw + b"\n")
            with self.assertRaisesRegex(ReviewError, "duplicate JSON"):
                self.read(first, pins, raw=raw[:-1] + b',"schema_version":1}')

    def test_reopening_revalidates_actual_waveform_and_metadata_bytes(self):
        with TemporaryDirectory(prefix="dubflow-review-current-physical-") as directory:
            _, original, first, _, _, _ = self.fixture(directory)
            pins = self.pins(original, first, directory)
            path = Path(first.cues[0].segment.path)
            raw = path.read_bytes(); path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
            with self.assertRaisesRegex(ValueError, "hash"):
                self.read(first, pins)
            path.write_bytes(raw)
            metadata = first.cues[0].document.path
            raw = metadata.read_bytes(); metadata.write_bytes(raw[:-1] + b" ")
            with self.assertRaisesRegex(ReviewError, "hash"):
                self.read(first, pins)


if __name__ == "__main__":
    unittest.main()

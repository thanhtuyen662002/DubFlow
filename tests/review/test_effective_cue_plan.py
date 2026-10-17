from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.mix import FileSource, LocalAudioMixer, MixConfig, SourceAudio
from engine.dubflow.review import ReviewError, ReviewFinding, ReviewSnapshot
from engine.dubflow.review.audio_composition import PinnedDocument, compose_audio, replace_audio_cue
from engine.dubflow.review.cue_plan import catalog_voice, prepare_original_cue
from engine.dubflow.review.effective_cue_plan import CAPABILITY, prepare_effective_cue
from engine.dubflow.review.production import RegenerationPlan, _correction_stages
from engine.dubflow.review.synthesis_input import _digest, _encode, prepare_cue_synthesis, read_cue_synthesis_input
from engine.dubflow.review.synthesis_worker import production_cue_config
from engine.dubflow.tts import LocalTtsAdapter, ResourceProfile, TtsProvenance
from engine.dubflow.tts import vieneu
from test_audio_composition import sha
import test_cue_plan as cue_helpers
from test_cue_plan import PROFILE, REPO
import test_current_audio_composition as current_helpers
import test_synthesis_input as inputs
from schema_assertions import assert_schema


class EffectiveCuePlanTests(unittest.TestCase):
    def proposal(self, current, *, index=0, text="Một câu mới.", voice=None):
        before = current.snapshot
        old = before.findings[index]
        new = replace(old, translated_text=old.translated_text if text is None else text,
                      voice=old.voice if voice is None else voice)
        stages = _correction_stages(translation_changed=old.translated_text != new.translated_text,
                                   voice_changed=old.voice != new.voice)
        findings = tuple(new if item.finding_id == old.finding_id else item for item in before.findings)
        return replace(before, revision=before.revision + 1, findings=findings,
            last_plan=RegenerationPlan(old.finding_id, stages, ("asr", "ocr"),
                                      tuple("replacement:" + stage for stage in stages)))

    def fixture(self, directory, *, first_voice=False):
        # Tiny synthetic WAVs exercise metadata admission. They do not qualify
        # production inference, a native graph, installed GUI or actual undo.
        root = Path(directory)
        graph = json.loads(cue_helpers.CuePlanTests().context()["source_graph_json"])
        values = dict(graph["tts_provenance"]); values["resource"] = ResourceProfile(**values["resource"])
        previous = TtsProvenance(**values)
        config = production_cue_config()
        voice = catalog_voice(REPO, PROFILE, previous.voice_id)
        segments = (inputs.helpers.segment("cue-a"),
                    inputs.helpers.segment("cue-b", start=1000, end=2000, text="Tạm biệt"))
        findings = tuple(ReviewFinding("finding-" + item.segment_id, "TTS_FAILURE", str(item.start.ticks),
            str(item.end.ticks), "Source", item.text, None, voice.voice_id, None, "0.4", ("tts",),
            cue_id=item.segment_id) for item in segments)
        snapshot = ReviewSnapshot("job-effective", 0, findings)
        before = root / "before"; before.mkdir()
        document = LocalTtsAdapter(inputs.helpers.DeterministicFixtureEngine(), config=config, voice=voice,
            provenance=previous, output_dir=before / "tts").synthesize(segments, input_hash=previous.input_hash)
        speech = before / "tts_document.json"; speech.write_bytes(document.to_bytes())
        source_path = before / "source.wav"; source_path.write_bytes(inputs.helpers.make_wave([1234] * 32000))
        source = FileSource(source_path, inputs.helpers.point(0), inputs.helpers.point(2000),
                            source_id="fixture-source", layout="mono-source")
        mix = LocalAudioMixer(config=MixConfig(), output_dir=before / "mix").mix(
            SourceAudio(source.source_id, source_path.read_bytes(), source.start, source.end, source.layout),
            document.artifacts, input_hash=previous.input_hash)
        mix_path = before / "mix_document.json"; mix_path.write_text(mix.to_json(), encoding="utf-8")
        pin = PinnedDocument(speech, sha(speech), speech.stat().st_size, before)
        original = compose_audio(snapshot, manifest_sha256=inputs.PIN, source=source,
            source_sha256=sha(source_path), canonical_inputs=segments,
            assignments={item.segment_id: pin for item in segments},
            mix_template=PinnedDocument(mix_path, sha(mix_path), mix_path.stat().st_size, before))
        target = catalog_voice(REPO, PROFILE, "vi-hai-dang-vieneu3-v1") if first_voice else voice
        after = self.proposal(original, text=None if first_voice else "Lần sửa thứ nhất.", voice=target.voice_id)
        request = prepare_cue_synthesis(snapshot, after, manifest_sha256=inputs.PIN,
            original_input=segments[0], previous_provenance=previous,
            target_provenance=replace(previous, voice_id=target.voice_id, voice_version=target.voice_version,
                                      voice_hash=target.content_hash()), voice=target, config=config)
        replacement = root / "replacement"; replacement.mkdir()
        document = LocalTtsAdapter(inputs.helpers.DeterministicFixtureEngine(), config=config, voice=target,
            provenance=request.provenance, output_dir=replacement / "tts").synthesize(
            (request.segment,), input_hash=request.input_hash)
        speech = replacement / "tts_document.json"; speech.write_bytes(document.to_bytes())
        first = replace_audio_cue(original, after, request=request,
            replacement_document=PinnedDocument(speech, sha(speech), speech.stat().st_size, replacement))
        return original, first

    def context(self, current, after):
        before_raw = _encode(current.snapshot.to_dict(), 65536)
        token = {"job_id": current.snapshot.job_id, "revision": str(current.snapshot.revision),
                 "snapshot_sha256": _digest(before_raw), "graph_sha256": "sha256:" + "c" * 64,
                 "original_manifest_sha256": current.manifest_sha256}
        return {"schema_version": 1, "kind": "review_effective_cue_context", "capability": CAPABILITY,
            "before_token": token, "before_snapshot_json": before_raw.decode("utf-8"),
            "after_snapshot_json": _encode(after.to_dict(), 65536).decode("utf-8"),
            "current_composition_sha256": current.input_hash,
            "composition_snapshot_sha256": _digest(before_raw)}

    def arguments(self, original, current, directory, context):
        pins = current_helpers.CurrentAudioCompositionTests().pins(original, current, directory)
        pins.pop("snapshot"); pins.pop("manifest_sha256")
        return dict(expected_token=dict(context["before_token"]), composition_snapshot=current.snapshot,
            composition_raw=_encode(current.to_dict(), 65536), app_root=REPO, profile_path=PROFILE, **pins)

    def test_second_cue_request_uses_current_baseline_without_losing_first_translation(self):
        with TemporaryDirectory(prefix="dubflow-effective-cue-") as directory:
            original, first = self.fixture(directory)
            after = self.proposal(first, index=1)
            context = self.context(first, after)
            with patch.object(vieneu, "load_vieneu_voice", side_effect=AssertionError("planning cannot load models")):
                packet = prepare_effective_cue(_encode(context, 65536),
                    **self.arguments(original, first, directory, context))
            request = read_cue_synthesis_input(packet["request_json"].encode("utf-8"),
                                               expected_hash=packet["request_sha256"])
            self.assertEqual(request.segment.segment_id, "cue-b")
            self.assertEqual(request.segment.text, "Một câu mới.")
            self.assertEqual(request.to_dict()["before_snapshot_sha256"], context["before_token"]["snapshot_sha256"])
            self.assertEqual(after.findings[0], first.snapshot.findings[0])
            self.assertEqual(packet["before_token"]["revision"], "1")
            self.assertFalse(packet["model_weights_verified"])
            self.assertFalse(packet["active_graph_published"])
            assert_schema(context, "effective-cue-context-v1.json")
            assert_schema(packet, "prepared-effective-cue-request-v1.json")

    def test_same_cue_translation_retains_previously_changed_voice_and_provenance(self):
        with TemporaryDirectory(prefix="dubflow-effective-current-voice-") as directory:
            original, first = self.fixture(directory, first_voice=True)
            context = self.context(first, self.proposal(first))
            packet = prepare_effective_cue(_encode(context, 65536),
                **self.arguments(original, first, directory, context))
            request = read_cue_synthesis_input(packet["request_json"].encode("utf-8"), expected_hash=packet["request_sha256"])
            self.assertEqual(request.provenance.voice_id, "vi-hai-dang-vieneu3-v1")
            self.assertEqual(request.provenance.voice_hash, first.cues[0].provenance.voice_hash)
            self.assertNotEqual(request.provenance.voice_hash, original.cues[0].provenance.voice_hash)

    def test_restored_original_content_keeps_public_revision_two_in_new_request(self):
        with TemporaryDirectory(prefix="dubflow-effective-restored-") as directory:
            original, _ = self.fixture(directory)
            restored = replace(original, snapshot=replace(original.snapshot, revision=2))
            context = self.context(restored, self.proposal(restored))
            context["current_composition_sha256"] = original.input_hash
            context["composition_snapshot_sha256"] = _digest(_encode(original.snapshot.to_dict(), 65536))
            arguments = self.arguments(original, original, directory, context)
            packet = prepare_effective_cue(_encode(context, 65536), **arguments)
            request = read_cue_synthesis_input(packet["request_json"].encode("utf-8"), expected_hash=packet["request_sha256"])
            self.assertEqual(packet["before_token"]["revision"], "2")
            self.assertEqual(request.to_dict()["revision"], "3")
            self.assertEqual(packet["current_composition_sha256"], original.input_hash)
            self.assertNotEqual(packet["effective_composition_sha256"], original.input_hash)
            with self.assertRaisesRegex(ReviewError, "compatibility"):
                prepare_original_cue(_encode(context, 65536), app_root=REPO, profile_path=PROFILE)

    def test_retained_content_with_new_playback_revision_prepares_next_cue_and_preserves_prior_edit(self):
        with TemporaryDirectory(prefix="dubflow-effective-projected-cue-") as directory:
            original, first = self.fixture(directory, first_voice=True)
            current = replace(first, snapshot=replace(first.snapshot, revision=3, findings=tuple(
                replace(item, output_preview=str(Path(directory) / "retained.mp4"))
                for item in first.snapshot.findings)))
            context = self.context(current, self.proposal(current, index=1))
            context["current_composition_sha256"] = first.input_hash
            context["composition_snapshot_sha256"] = _digest(_encode(first.snapshot.to_dict(), 65536))
            arguments = self.arguments(original, first, directory, context)
            with patch.object(vieneu, "load_vieneu_voice", side_effect=AssertionError("projection loaded models")):
                packet = prepare_effective_cue(_encode(context, 65536), **arguments)
            request = read_cue_synthesis_input(packet["request_json"].encode(), expected_hash=packet["request_sha256"])
            self.assertEqual(request.to_dict()["revision"], "4")
            self.assertEqual(request.to_dict()["before_snapshot_sha256"], context["before_token"]["snapshot_sha256"])
            projection = json.loads(packet["effective_composition_json"])
            self.assertEqual(projection["review_snapshot_sha256"], context["before_token"]["snapshot_sha256"])
            self.assertEqual(projection["cues"], first.to_dict()["cues"])
            self.assertEqual(projection["documents"], first.to_dict()["documents"])
            self.assertEqual(packet["current_composition_sha256"], first.input_hash)
            self.assertEqual(_digest(packet["effective_composition_json"].encode()), packet["effective_composition_sha256"])
            self.assertEqual(first.cues[0].provenance.voice_id, "vi-hai-dang-vieneu3-v1")
            assert_schema(context, "effective-cue-context-v1.json")
            assert_schema(packet, "prepared-effective-cue-request-v1.json")

    def test_content_snapshot_pin_and_prior_semantics_cannot_be_substituted_during_projection(self):
        with TemporaryDirectory(prefix="dubflow-effective-project-binding-") as directory:
            original, first = self.fixture(directory)
            context = self.context(first, self.proposal(first))
            arguments = self.arguments(original, first, directory, context)
            changed = deepcopy(context); changed["composition_snapshot_sha256"] = "sha256:" + "d" * 64
            with self.assertRaisesRegex(ReviewError, "composition snapshot"):
                prepare_effective_cue(_encode(changed, 65536), **arguments)
            with self.assertRaisesRegex(ReviewError, "composition snapshot"):
                prepare_effective_cue(_encode(context, 65536), **{**arguments, "composition_snapshot": original.snapshot})
            lost = replace(first, snapshot=replace(first.snapshot, revision=3, findings=tuple(
                replace(f, translated_text=original.snapshot.findings[i].translated_text)
                for i,f in enumerate(first.snapshot.findings))))
            changed = self.context(lost, self.proposal(lost))
            changed["current_composition_sha256"] = first.input_hash
            changed["composition_snapshot_sha256"] = context["composition_snapshot_sha256"]
            with self.assertRaisesRegex(ReviewError, "changed admitted content"):
                prepare_effective_cue(_encode(changed, 65536),
                    **self.arguments(original, first, directory, changed))

    def test_wrong_token_composition_or_capability_and_unrelated_edits_refuse(self):
        with TemporaryDirectory(prefix="dubflow-effective-refusal-") as directory:
            original, first = self.fixture(directory)
            context = self.context(first, self.proposal(first))
            arguments = self.arguments(original, first, directory, context)
            for key in context["before_token"]:
                changed = deepcopy(context); changed["before_token"][key] = "foreign"
                with self.assertRaisesRegex(ReviewError, "current token"):
                    prepare_effective_cue(_encode(changed, 65536), **arguments)
            for change in ({"capability": "unknown"}, {"kind": "review_original_cue_context"}, {"schema_version": True}):
                with self.assertRaisesRegex(ReviewError, "compatibility"):
                    prepare_effective_cue(_encode({**context, **change}, 65536), **arguments)
            changed = deepcopy(context); changed["current_composition_sha256"] = original.input_hash
            with self.assertRaisesRegex(ReviewError, "trusted pin"):
                prepare_effective_cue(_encode(changed, 65536), **arguments)
            changed = deepcopy(context); after = json.loads(changed["after_snapshot_json"])
            after["findings"][1]["translated_text"] = "Unrelated change"
            changed["after_snapshot_json"] = _encode(after, 65536).decode("utf-8")
            with self.assertRaisesRegex(ReviewError, "unrelated cue"):
                prepare_effective_cue(_encode(changed, 65536), **arguments)


if __name__ == "__main__":
    unittest.main()

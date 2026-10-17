from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import unittest

from engine.dubflow.review import ReviewError, ReviewFinding, ReviewSnapshot, ReviewStore
from engine.dubflow.review.synthesis_input import prepare_cue_synthesis, read_cue_synthesis_input
from engine.dubflow.tts import LocalTtsAdapter, TtsConfig, approved_default_voice
from engine.dubflow.asr import TimeBase, TimePoint
import test_export as export_helpers
from schema_assertions import assert_schema

helpers = export_helpers.tts_helpers
PIN = "sha256:" + "a" * 64


class CueSynthesisInputTests(unittest.TestCase):
    def correction(self, directory, *, change_voice=False):
        voice = approved_default_voice(); config = TtsConfig(max_text_chars=512)
        finding = ReviewFinding("finding-a", "TTS_FAILURE", "0", "1000", "Hello", "Xin chào",
                                None, voice.voice_id, None, "0.4", ("tts",), cue_id="cue-a")
        other = replace(finding, finding_id="finding-b", cue_id="cue-b", start_ticks="1000", end_ticks="2000")
        before = ReviewSnapshot("job-review", 0, (finding, other))
        store = ReviewStore(Path(directory) / "review.json"); store.initialize(before)
        target = replace(voice, voice_id="voice-b", voice_version="2") if change_voice else voice
        after, _ = store.commit_correction(before, "finding-a", voice=target.voice_id if change_voice else None,
                                           translation=None if change_voice else "Ngồi yên.")
        return before, after, dict(manifest_sha256=PIN, original_input=helpers.segment("cue-a"),
                                  previous_provenance=helpers.make_provenance(config, voice),
                                  target_provenance=helpers.make_provenance(config, target), voice=target, config=config)

    def test_actual_store_correction_synthesizes_one_cue_with_exact_input_pin(self):
        with TemporaryDirectory(prefix="dubflow-review-input-") as directory:
            before, after, arguments = self.correction(directory)
            request = prepare_cue_synthesis(before, after, **arguments)
            self.assertEqual(request, prepare_cue_synthesis(before, after, **arguments))
            self.assertEqual(request.provenance.input_hash, request.input_hash)
            self.assertEqual(read_cue_synthesis_input(request.encoded, expected_hash=request.input_hash), request)
            assert_schema(request.to_dict(), "cue-synthesis-v1.json")
            self.assertEqual(request.segment.segment_id, "cue-a")
            self.assertEqual(request.segment.source_utterance_id, "source-cue-a")
            self.assertEqual(request.segment.text, "Ngồi yên.")
            self.assertEqual(request.segment.normalized_text, "Ngồi yên.")
            self.assertEqual(before.findings[1], after.findings[1])
            engine = helpers.DeterministicFixtureEngine()
            document = LocalTtsAdapter(engine, config=arguments["config"], voice=arguments["voice"],
                       provenance=request.provenance, output_dir=Path(directory) / "candidate").synthesize((request.segment,), input_hash=request.input_hash)
            self.assertEqual(document.source_segment_ids, ("cue-a",))
            self.assertEqual(len(document.artifacts), 1)
            self.assertEqual(document.provenance.input_hash, request.input_hash)

    def test_explicit_voice_change_keeps_model_and_producer_and_changes_request_pin(self):
        with TemporaryDirectory(prefix="dubflow-review-input-voice-") as directory:
            before, after, arguments = self.correction(directory, change_voice=True)
            request = prepare_cue_synthesis(before, after, **arguments)
            self.assertEqual(request.provenance.voice_id, "voice-b")
            self.assertEqual(request.provenance.model_hash, arguments["previous_provenance"].model_hash)
            self.assertEqual(request.segment.text, before.findings[0].translated_text)
            self.assertEqual(request.to_dict()["revision"], "1")

    def test_four_hour_and_wide_integer_source_ticks_keep_exact_mapping_and_window(self):
        with TemporaryDirectory(prefix="dubflow-review-input-ticks-") as directory:
            before, after, arguments = self.correction(directory)
            for index, start in enumerate((691200000, 9007199254740993)):
                base = TimeBase(1, 48000)
                original = replace(arguments["original_input"], start=TimePoint(start, base), end=TimePoint(start + 48000, base),
                                   render_window_end=TimePoint(start + 96000, base))
                shifted = replace(before.findings[0], start_ticks=str(start), end_ticks=str(start + 48000))
                old = replace(before, findings=(shifted,), time_base=("1", "48000"))
                store = ReviewStore(Path(directory) / f"wide-{index}.json"); store.initialize(old)
                new, _ = store.commit_correction(old, shifted.finding_id, translation="Ngồi yên.")
                request = prepare_cue_synthesis(old, new, **{**arguments, "original_input": original})
                self.assertEqual(request.segment.start, original.start)
                self.assertEqual(request.segment.end, original.end)
                self.assertEqual(request.segment.render_window_end, original.render_window_end)
                self.assertEqual(request.to_dict()["tts_input"]["start"]["ticks"], str(start))
                self.assertEqual(read_cue_synthesis_input(request.encoded, expected_hash=request.input_hash), request)

    def test_stale_revision_wrong_plan_unrelated_cue_and_source_mapping_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-input-binding-") as directory:
            before, after, arguments = self.correction(directory)
            changes = [replace(after, revision=3), replace(after, time_base=("1", "48000")),
                       replace(after, last_plan=replace(after.last_plan, stages_to_rerun=("tts",))),
                       replace(after, findings=(after.findings[0], replace(after.findings[1], translated_text="Other"))),
                       replace(after, findings=(replace(after.findings[0], start_ticks="1"), after.findings[1]))]
            for changed in changes:
                with self.assertRaises(ReviewError): prepare_cue_synthesis(before, changed, **arguments)
            with self.assertRaisesRegex(ReviewError, "canonical cue"):
                prepare_cue_synthesis(before, after, **{**arguments, "original_input": helpers.segment("cue-a", start=1)})

    def test_model_runtime_config_or_unchanged_voice_migration_never_happens_silently(self):
        with TemporaryDirectory(prefix="dubflow-review-input-model-") as directory:
            before, after, arguments = self.correction(directory)
            provenance = arguments["target_provenance"]
            for changed in (replace(provenance, runtime="new-runtime"), replace(provenance, producer_version="new-producer"),
                            replace(provenance, model_hash="sha256:" + "b" * 64), replace(provenance, config_hash="sha256:" + "b" * 64)):
                with self.assertRaises(ReviewError):
                    prepare_cue_synthesis(before, after, **{**arguments, "target_provenance": changed})
            voice = replace(arguments["voice"], voice_version="new-version")
            changed = helpers.make_provenance(arguments["config"], voice)
            with self.assertRaisesRegex(ReviewError, "unchanged voice"):
                prepare_cue_synthesis(before, after, **{**arguments, "target_provenance": changed, "voice": voice})

    def test_unknown_voice_and_overlong_cue_are_explicit_failures(self):
        with TemporaryDirectory(prefix="dubflow-review-input-limit-") as directory:
            before, after, arguments = self.correction(directory)
            changed = replace(after, findings=(replace(after.findings[0], voice=None), after.findings[1]))
            with self.assertRaisesRegex(ReviewError, "assigned voice"):
                prepare_cue_synthesis(before, changed, **arguments)
            changed = replace(after, findings=(replace(after.findings[0], translated_text="x" * (arguments["config"].max_text_chars + 1)), after.findings[1]))
            with self.assertRaisesRegex(ReviewError, "text limit"):
                prepare_cue_synthesis(before, changed, **arguments)

    def test_unknown_version_bad_pin_extra_fields_and_wrong_normalized_text_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-input-read-") as directory:
            before, after, arguments = self.correction(directory)
            request = prepare_cue_synthesis(before, after, **arguments)
            with self.assertRaisesRegex(ReviewError, "trusted pin"):
                read_cue_synthesis_input(request.encoded, expected_hash=PIN)
            for mutate in (lambda value: value.update(schema_version=2), lambda value: value.update(extra=True),
                           lambda value: value.update(revision=1), lambda value: value["tts_input"].update(normalized_text="Other"),
                           lambda value: value["producer_pin"].update(input_hash=PIN),
                           lambda value: value["config"].pop("sample_rate"), lambda value: value["voice"].pop("default"),
                           lambda value: value.update(config=[])):
                value = request.to_dict(); mutate(value)
                raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                with self.assertRaises(ReviewError):
                    read_cue_synthesis_input(raw, expected_hash="sha256:" + hashlib.sha256(raw).hexdigest())


if __name__ == "__main__": unittest.main()

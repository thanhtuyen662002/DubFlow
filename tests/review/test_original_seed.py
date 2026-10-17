from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.mix import LocalAudioMixer, MixConfig, SourceAudio
from engine.dubflow.review import ReviewError
from engine.dubflow.review.original_seed import original_seed
from engine.dubflow.review.synthesis_input import _digest, _encode
from engine.dubflow.tts import LocalTtsAdapter, TtsConfig, approved_default_voice
from engine.dubflow.worker.b2_audio import _dubbing_windows, _input_hash
from engine.dubflow.worker.tts_rewrites import BoundedDurationRewriteEngine, SpokenTextStore
import test_export as exports
import test_synthesis_input as inputs
from schema_assertions import assert_schema


class OriginalSeedTests(unittest.TestCase):
    def fixture(self, directory, *, b2=False):
        path, manifest = exports.ExportReviewTests().fixture(directory)
        if not b2:
            return path, manifest
        root = path.parent
        timeline = json.loads(Path(manifest["artifacts"]["editable_timeline"]["path"]).read_bytes())
        mappings = timeline["cues"]
        windows = _dubbing_windows(mappings, 1000)
        wave = root / "source.wav"; wave.write_bytes(inputs.helpers.make_wave([1234] * 16000))
        source_sha = _digest(wave.read_bytes())
        placement = {"schema_version":1,"kind":"dubbing_placement","recipe":"source-intercue-postroll-2000ms-gap120ms-v1",
            "canonical_cue_input_hash":_input_hash(mappings),"source_audio_sha256":source_sha,"source_end":inputs.helpers.point(1000).to_dict(),
            "time_base":{"numerator":"1","denominator":"1000"},"windows":list(windows),"gap_evidence":"recognized-source-cue-intervals;silence-not-certified"}
        placement_raw = _encode(placement, 65536)
        voice, config = approved_default_voice(), TtsConfig()
        segment = replace(inputs.helpers.segment("cue-a"), source_utterance_id="cue-a", confidence=0.1, source_language="en", render_window_end=inputs.helpers.point(1000))
        identity = "a" * 64
        spoken = SpokenTextStore(root / "tts", identity=identity, segments=(segment,))
        engine = BoundedDurationRewriteEngine(inputs.helpers.DeterministicFixtureEngine(), spoken)
        provenance = replace(inputs.helpers.make_provenance(config,voice), input_hash=_digest(placement_raw))
        speech = LocalTtsAdapter(engine, config=config, voice=voice, provenance=provenance, output_dir=root/"tts").synthesize(
            (segment,), input_hash=_digest(placement_raw), on_checkpoint=spoken.commit)
        mix = LocalAudioMixer(config=MixConfig(),output_dir=root/"mix").mix(
            SourceAudio("fixture-source",wave.read_bytes(),inputs.helpers.point(0),inputs.helpers.point(1000),"mono-source"),speech.artifacts,input_hash=inputs.PIN)
        values = {"dubbing_placement":placement_raw,"tts_document":speech.to_bytes(),
                  "dubbing_spoken_text":_encode(spoken.document(speech),65536),"mix_document":mix.to_json().encode()}
        for role, raw in values.items():
            target = root / (role + ".json"); target.write_bytes(raw)
            manifest["artifacts"][role] = {"path":str(target),"sha256":_digest(raw),"size_bytes":len(raw)}
        manifest["artifacts"]["source_audio"] = {"path":str(wave),"sha256":source_sha,"size_bytes":wave.stat().st_size}
        manifest["language"] = {"source_language":"en"}
        manifest["dubbing"]["enabled"] = True
        path.write_bytes(_encode(manifest,65536))
        return path, manifest

    def seed(self, path):
        return original_seed(path,expected_job_id="job-review",expected_manifest_sha256=_digest(path.read_bytes()))

    def test_b1_seed_has_no_invented_tts_and_original_bytes_stay_unchanged(self):
        with TemporaryDirectory(prefix="dubflow-original-b1-") as directory:
            path, manifest = self.fixture(directory)
            before = {role:Path(v["path"]).read_bytes() for role,v in manifest["artifacts"].items()}
            seed = self.seed(path); graph = json.loads(seed["graph_json"])
            self.assertEqual(graph["mode"],"b1"); self.assertEqual(graph["canonical_inputs"],[])
            self.assertIsNone(graph["tts_provenance"])
            self.assertEqual(set(graph["documents"]),{"editable_timeline","qc_report"})
            self.assertEqual(before,{role:Path(v["path"]).read_bytes() for role,v in manifest["artifacts"].items()})
            assert_schema(seed,"original-seed-v1.json")

    def test_b2_seed_binds_actual_original_inputs_recipe_and_documents_without_media_scan(self):
        with TemporaryDirectory(prefix="dubflow-original-b2-") as directory:
            path, manifest = self.fixture(directory,b2=True)
            with patch("wave.open",side_effect=AssertionError("metadata seed must not decode media")):
                seed = self.seed(path)
            graph = json.loads(seed["graph_json"])
            self.assertEqual(graph["mode"],"b2"); self.assertEqual(len(graph["documents"]),6)
            self.assertEqual(graph["canonical_inputs"][0]["text"],"Xin chào")
            self.assertEqual(graph["canonical_inputs"][0]["render_window_end"]["ticks"],"1000")
            self.assertEqual(graph["media_policy"],"revalidate-before-reuse")
            self.assertEqual(graph["producer_policy"],"explicit-release-admission")
            assert_schema(seed,"original-seed-v1.json")

    def test_legacy_missing_spoken_document_refuses_without_altering_exports(self):
        with TemporaryDirectory(prefix="dubflow-original-legacy-") as directory:
            path, manifest = self.fixture(directory,b2=True)
            del manifest["artifacts"]["dubbing_spoken_text"]; path.write_bytes(_encode(manifest,65536))
            before = path.read_bytes()
            with self.assertRaisesRegex(ReviewError,"compatibility adapter"):
                self.seed(path)
            self.assertEqual(path.read_bytes(),before)

    def test_mode_recipe_wrong_spoken_semantics_and_source_pin_refuse(self):
        with TemporaryDirectory(prefix="dubflow-original-pins-") as directory:
            path, manifest = self.fixture(directory,b2=True)
            for role, change in [
                ("dubbing_placement",lambda v:v.update(recipe="another-recipe")),
                ("dubbing_placement",lambda v:v.update(source_audio_sha256=inputs.PIN)),
                ("dubbing_spoken_text",lambda v:v["entries"][0].update(spoken_text="Foreign text")),
            ]:
                target = Path(manifest["artifacts"][role]["path"]); original = target.read_bytes()
                value = json.loads(original); change(value)
                exports.ExportReviewTests().update(path,manifest,role,value)
                with self.assertRaises((ReviewError,ValueError)): self.seed(path)
                exports.ExportReviewTests().update(path,manifest,role,json.loads(original))
            manifest["dubbing"]["enabled"] = False; path.write_bytes(_encode(manifest,65536))
            with self.assertRaisesRegex(ReviewError,"mode"):
                self.seed(path)

    def test_raced_metadata_hash_refuses_and_combined_budget_is_bounded(self):
        with TemporaryDirectory(prefix="dubflow-original-race-") as directory:
            path, manifest = self.fixture(directory)
            from engine.dubflow.review import original_seed as module
            read = module._json_bytes
            def changed(target,*args,**kwargs):
                raw = read(target,*args,**kwargs)
                if target.name == "editable_timeline.json": target.write_bytes(raw+b" ")
                return raw
            with patch.object(module,"_json_bytes",side_effect=changed), self.assertRaises(ReviewError): self.seed(path)
        with TemporaryDirectory(prefix="dubflow-original-bound-") as directory:
            path,_ = self.fixture(directory)
            with patch("engine.dubflow.review.original_seed.MAX_JSON_BYTES",1),self.assertRaisesRegex(ReviewError,"byte limit"):
                self.seed(path)


if __name__ == "__main__": unittest.main()

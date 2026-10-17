from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.mix import MixError
from engine.dubflow.review import ReviewError
from engine.dubflow.review.accepted_documents import AcceptedWavePin, prepare_accepted_cue_documents
from engine.dubflow.review.synthesis_input import _digest, _encode
from engine.dubflow.review.synthesis_worker import synthesize_cue_candidate
from engine.dubflow.tts import TtsError, parse_tts_json
import test_synthesis_input as inputs


class AcceptedDocumentsTests(unittest.TestCase):
    @contextmanager
    def fixture(self, *, render_window=True):
        with TemporaryDirectory(prefix="dubflow-review-accepted-documents-") as directory:
            root = Path(directory)
            before, after, args = inputs.CueSynthesisInputTests().correction(directory)
            if render_window:
                args["original_input"] = replace(args["original_input"], render_window_end=inputs.helpers.point(1200))
            request = inputs.prepare_cue_synthesis(before, after, **args)
            candidate = synthesize_cue_candidate(request.encoded, expected_hash=request.input_hash,
                output_root=root / "producer", engine_factory=lambda _: inputs.helpers.DeterministicFixtureEngine(),
                recipe_identity="fixture-source-recipe-v1")
            artifact = candidate.document.artifacts[0]
            accepted = root / "accepted"; accepted.mkdir()
            audio = accepted / (artifact.content_hash[7:] + ".wav")
            # Simulated native accepted copy for adapter tests; no Rust/GUI claim.
            audio.write_bytes(Path(artifact.path).read_bytes()); audio.chmod(stat.S_IRUSR)
            pin = AcceptedWavePin(audio, accepted, artifact.content_hash, audio.stat().st_size,
                                  artifact.sample_rate, artifact.channels, artifact.frame_count)
            try:
                yield request, candidate, pin
            finally:
                for path in accepted.glob("*.wav"):
                    path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def prepare(self, request, candidate, pin, **changes):
        args = dict(candidate_tts=changes.get("candidate_tts") or candidate.document_path.read_bytes(),
                    candidate_spoken_text=changes.get("candidate_spoken_text") or candidate.spoken_text_path.read_bytes(),
                    accepted_wave=pin, recipe_identity="fixture-source-recipe-v1")
        return prepare_accepted_cue_documents(request, **{**args, **changes})

    def test_only_audio_path_and_dependent_document_hash_change(self):
        with self.fixture() as (request, candidate, pin):
            original_tts = candidate.document_path.read_bytes()
            original_spoken = candidate.spoken_text_path.read_bytes()
            prepared = self.prepare(request, candidate, pin)
            expected = json.loads(original_tts); expected["artifacts"][0]["path"] = str(pin.path)
            self.assertEqual(parse_tts_json(prepared.tts), expected)
            expected_spoken = json.loads(original_spoken)
            expected_spoken["tts_document_hash"] = prepared.tts_sha256
            self.assertEqual(json.loads(prepared.spoken_text), expected_spoken)
            self.assertEqual(prepared.spoken_text_sha256, _digest(prepared.spoken_text))
            self.assertEqual(candidate.document_path.read_bytes(), original_tts)
            self.assertEqual(candidate.spoken_text_path.read_bytes(), original_spoken)
            self.assertEqual(self.prepare(request, candidate, pin), prepared)

    def test_discarded_candidate_files_do_not_break_accepted_metadata(self):
        with self.fixture() as (request, candidate, pin):
            tts, spoken = candidate.document_path.read_bytes(), candidate.spoken_text_path.read_bytes()
            Path(candidate.document.artifacts[0].path).unlink()
            candidate.document_path.unlink(); candidate.spoken_text_path.unlink()
            prepared = self.prepare(request, candidate, pin, candidate_tts=tts, candidate_spoken_text=spoken)
            self.assertEqual(parse_tts_json(prepared.tts)["artifacts"][0]["path"], str(pin.path))

    def test_v1_without_render_window_keeps_its_version_and_has_no_invented_window(self):
        with self.fixture(render_window=False) as (request, candidate, pin):
            prepared = self.prepare(request, candidate, pin)
            self.assertEqual(parse_tts_json(prepared.tts)["schema_version"], 1)
            self.assertNotIn("render_window_end", parse_tts_json(prepared.tts)["artifacts"][0])

    def test_producer_input_mapping_window_and_audio_metadata_substitution_refuse(self):
        with self.fixture() as (request, candidate, pin):
            raw = candidate.document_path.read_bytes()
            mutations = [
                lambda v: v["provenance"].update(input_hash=inputs.PIN),
                lambda v: v["provenance"].update(model_hash="sha256:" + "c" * 64),
                lambda v: v["artifacts"][0].update(source_utterance_id="foreign"),
                lambda v: v["artifacts"][0].update(confidence=0.8),
                lambda v: v["artifacts"][0].update(render_window_end=inputs.helpers.point(3000).to_dict()),
                lambda v: v["artifacts"][0].update(sample_rate=8000),
            ]
            for mutate in mutations:
                value = json.loads(raw); mutate(value)
                with self.assertRaises((ReviewError, TtsError)):
                    self.prepare(request, candidate, pin, candidate_tts=_encode(value, 65536))
            legacy = json.loads(raw); legacy["schema_version"] = 1
            del legacy["artifacts"][0]["render_window_end"]
            with self.assertRaisesRegex(ReviewError, "compatible version"):
                self.prepare(request, candidate, pin, candidate_tts=_encode(legacy, 65536))
            with self.assertRaisesRegex(ReviewError, "request object"):
                self.prepare(replace(request, segment=replace(request.segment, text="Khác", normalized_text=None)), candidate, pin)

    def test_wrong_spoken_recipe_or_semantics_refuse(self):
        with self.fixture() as (request, candidate, pin):
            spoken = json.loads(candidate.spoken_text_path.read_bytes())
            spoken["entries"][0]["spoken_text"] = "Lời không thuộc câu này."
            with self.assertRaisesRegex(ValueError, "spoken-text document differs"):
                self.prepare(request, candidate, pin, candidate_spoken_text=_encode(spoken, 65536))
            with self.assertRaisesRegex(ValueError, "spoken-text document differs"):
                self.prepare(request, candidate, pin, recipe_identity="another-source-recipe")

    def test_unsealed_same_size_corrupt_and_wrong_pin_audio_refuse(self):
        with self.fixture() as (request, candidate, pin):
            for changed in (replace(pin, sample_rate=0), replace(pin, frame_count=pin.frame_count + 1),
                            replace(pin, path=pin.path.with_suffix(".other")), replace(pin, size_bytes=True)):
                with self.assertRaises(ReviewError): self.prepare(request, candidate, changed)
            pin.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            with self.assertRaisesRegex(ReviewError, "unsealed"):
                self.prepare(request, candidate, pin)
            raw = pin.path.read_bytes(); pin.path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
            pin.path.chmod(stat.S_IRUSR)
            with self.assertRaises(MixError): self.prepare(request, candidate, pin)

    def test_duplicate_keys_trailing_values_and_nonobject_metadata_refuse(self):
        with self.fixture() as (request, candidate, pin):
            for raw in (b"[]", b"{} {}", b'{"a":1,"a":2}', b'{"nested":{"a":1,"a":2}}'):
                for field in ("candidate_tts", "candidate_spoken_text"):
                    with self.assertRaises((ReviewError, ValueError)):
                        self.prepare(request, candidate, pin, **{field: raw})


if __name__ == "__main__":
    unittest.main()

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.mix import FileSource, LocalAudioMixer, MixConfig, MixError, SourceAudio
from engine.dubflow.mix import streaming
from engine.dubflow.mix.adapter import MixFailure, MixProvenance, ResourceProfile, parse_mix_json
from engine.dubflow.review import ReviewError, ReviewFinding, ReviewSnapshot, ReviewStore
from engine.dubflow.review.audio_composition import (PinnedDocument, compose_audio,
                                                   replace_audio_cue, mix_audio_candidate)
from engine.dubflow.review.synthesis_input import prepare_cue_synthesis
from engine.dubflow.tts import LocalTtsAdapter, TtsConfig, approved_default_voice
import test_synthesis_input as inputs
from schema_assertions import assert_schema

helpers = inputs.helpers


def sha(path): return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AudioCompositionTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory); voice = approved_default_voice(); config = TtsConfig(max_text_chars=512)
        segments = (helpers.segment("cue-a"), helpers.segment("cue-b", start=1000, end=2000, text="Tạm biệt"))
        findings = tuple(ReviewFinding("finding-"+item.segment_id, "TTS_FAILURE", str(item.start.ticks),
                str(item.end.ticks), "Source", item.text, None, voice.voice_id, None, "0.4", ("tts",), cue_id=item.segment_id) for item in segments)
        snapshot = ReviewSnapshot("job-composition", 0, findings)
        before = root / "before"; before.mkdir()
        document = LocalTtsAdapter(helpers.DeterministicFixtureEngine(), config=config, voice=voice,
                provenance=helpers.make_provenance(config, voice), output_dir=before/"tts").synthesize(segments, input_hash=helpers.INPUT_HASH)
        speech = before/"tts_document.json"; speech.write_bytes(document.to_bytes())
        source_path = before/"source.wav"; source_path.write_bytes(helpers.make_wave([1234]*32000))
        source = FileSource(source_path, helpers.point(0), helpers.point(2000), source_id="fixture-source", layout="mono-source")
        mix = LocalAudioMixer(config=MixConfig(), output_dir=before/"mix").mix(
                SourceAudio(source.source_id, source_path.read_bytes(), source.start, source.end, source.layout),
                document.artifacts, input_hash=helpers.INPUT_HASH)
        mix_path = before/"mix_document.json"; mix_path.write_text(mix.to_json(), encoding="utf-8")
        speech_pin = PinnedDocument(speech, sha(speech), speech.stat().st_size, before)
        composition = compose_audio(snapshot, manifest_sha256=inputs.PIN, source=source, source_sha256=sha(source_path),
                canonical_inputs=segments, assignments={item.segment_id:speech_pin for item in segments},
                mix_template=PinnedDocument(mix_path, sha(mix_path), mix_path.stat().st_size, before))
        store = ReviewStore(root/"review.json"); store.initialize(snapshot)
        return composition, store, config, voice

    def correction(self, current, store, config, voice, directory, *, cue_index=0, new_voice=None):
        selected = current.cues[cue_index]
        after, _ = store.commit_correction(current.snapshot, current.snapshot.findings[cue_index].finding_id,
                voice=new_voice.voice_id if new_voice else None, translation=None if new_voice else "Ngồi yên.")
        target_voice = new_voice or voice
        request = prepare_cue_synthesis(current.snapshot, after, manifest_sha256=inputs.PIN,
                original_input=selected.canonical_input, previous_provenance=selected.provenance,
                target_provenance=helpers.make_provenance(config,target_voice), voice=target_voice, config=config)
        candidate = Path(directory); candidate.mkdir()
        document = LocalTtsAdapter(helpers.DeterministicFixtureEngine(), config=config, voice=target_voice,
                provenance=request.provenance, output_dir=candidate/"tts").synthesize((request.segment,), input_hash=request.input_hash)
        path = candidate/"tts_document.json"; path.write_bytes(document.to_bytes())
        return after, request, PinnedDocument(path, sha(path), path.stat().st_size, candidate)

    def test_one_replacement_retains_unrelated_wave_document_and_voice_pin(self):
        with TemporaryDirectory(prefix="dubflow-review-compose-") as directory:
            current, store, config, voice = self.fixture(directory)
            original_wave = Path(current.cues[1].segment.path).read_bytes()
            after, request, pin = self.correction(current,store,config,voice,Path(directory)/"replacement")
            updated = replace_audio_cue(current,after,request=request,replacement_document=pin)
            assert_schema(updated.to_dict(), "audio-composition-v1.json")
            self.assertNotEqual(updated.input_hash,current.input_hash)
            self.assertEqual(updated.cues[1],current.cues[1])
            self.assertEqual(Path(updated.cues[1].segment.path).read_bytes(),original_wave)
            self.assertEqual(updated.cues[0].canonical_input.normalized_text,"Ngồi yên.")
            self.assertEqual(len(updated.to_dict()["documents"]),2)

    def test_successive_text_and_voice_corrections_keep_separate_provenance(self):
        with TemporaryDirectory(prefix="dubflow-review-compose-sequential-") as directory:
            original,store,config,voice = self.fixture(directory)
            after,request,pin = self.correction(original,store,config,voice,Path(directory)/"first")
            first = replace_audio_cue(original,after,request=request,replacement_document=pin)
            another = replace(voice, voice_id="voice-b", voice_version="2")
            after,request,pin = self.correction(first,store,config,voice,Path(directory)/"second",cue_index=1,new_voice=another)
            second = replace_audio_cue(first,after,request=request,replacement_document=pin)
            self.assertEqual(second.cues[0],first.cues[0])
            self.assertEqual(second.cues[0].provenance.voice_id,voice.voice_id)
            self.assertEqual(second.cues[1].provenance.voice_id,another.voice_id)
            self.assertNotEqual(second.cues[0].document.sha256,second.cues[1].document.sha256)
            self.assertEqual(second.cues[1].canonical_input,original.cues[1].canonical_input)
            assert_schema(second.to_dict(), "audio-composition-v1.json")

    def test_stale_snapshot_wrong_manifest_or_replacement_document_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-compose-binding-") as directory:
            current,store,config,voice = self.fixture(directory)
            after,request,pin = self.correction(current,store,config,voice,Path(directory)/"replacement")
            for changed in (replace(current,snapshot=replace(current.snapshot,revision=3)),
                            replace(current,manifest_sha256="sha256:"+"b"*64)):
                with self.assertRaises(ReviewError):
                    replace_audio_cue(changed,after,request=request,replacement_document=pin)
            with self.assertRaisesRegex(ReviewError,"exactly one"):
                replace_audio_cue(current,after,request=request,replacement_document=current.cues[0].document)

    def test_missing_assignment_foreign_cue_and_source_hash_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-compose-source-") as directory:
            current,_,_,_ = self.fixture(directory)
            before = Path(directory)/"before"; mix = before/"mix_document.json"
            args=dict(manifest_sha256=inputs.PIN,source=current.source,source_sha256=current.source_sha256,
                    canonical_inputs=tuple(item.canonical_input for item in current.cues),
                    assignments={item.canonical_input.segment_id:item.document for item in current.cues},
                    mix_template=PinnedDocument(mix,sha(mix),mix.stat().st_size,before))
            for update in ({"assignments":{"cue-a":current.cues[0].document}},
                           {"source_sha256":"sha256:"+"b"*64},
                           {"canonical_inputs":(replace(current.cues[0].canonical_input,segment_id="foreign"),current.cues[1].canonical_input)}):
                with self.assertRaises(ReviewError): compose_audio(current.snapshot,**{**args,**update})

    def test_same_size_corrupt_wave_is_not_admitted(self):
        with TemporaryDirectory(prefix="dubflow-review-compose-corrupt-") as directory:
            current,store,config,voice = self.fixture(directory)
            after,request,pin = self.correction(current,store,config,voice,Path(directory)/"replacement")
            wave = next((Path(directory)/"replacement/tts").glob("*.wav")); data=wave.read_bytes()
            wave.write_bytes(data[:-1]+bytes([data[-1]^1]))
            with self.assertRaises(MixError): replace_audio_cue(current,after,request=request,replacement_document=pin)

    def test_historical_mix_producer_needs_explicit_admission_before_output(self):
        with TemporaryDirectory(prefix="dubflow-review-compose-producer-") as directory:
            current,_,_,_=self.fixture(directory); target=Path(directory)/"new mix"
            with self.assertRaisesRegex(ReviewError,"producer/config differs"):
                mix_audio_candidate(current,output_root=target)
            self.assertFalse(target.exists())

    def admitted_mixer_stub(self, current, directory):
        # Exercise the review boundary without requiring NumPy in Integration.
        # The streaming DSP has separate actual PCM tests; this is a stub result.
        config = MixConfig(requested_profile="cpu", resource=ResourceProfile(1, 512, 8))
        config_hash = streaming._hash_json({"dsp": config.to_dict(), "limits": streaming.StreamLimits().__dict__,
                "producer": streaming.PRODUCER_VERSION, "runtime": streaming.NUMPY_VERSION})
        provenance = MixProvenance("dubflow-aud-0", streaming.PRODUCER_VERSION, streaming.BACKEND_ID,
                "owned-python/numpy-"+streaming.NUMPY_VERSION, "timeline-v1", config_hash,
                current.source_sha256, current.input_hash, "cpu", "cpu", config.resource)
        producer = {key:value for key,value in provenance.to_dict().items() if key not in {"input_hash","source_hash"}}
        current = replace(current,mix_producer_pin=json.dumps(producer,sort_keys=True,separators=(",",":")))
        provenance = replace(provenance,input_hash=current.input_hash)
        document = streaming._restore(parse_mix_json((Path(directory)/"before/mix_document.json").read_bytes()))
        return current, replace(document,provenance=provenance)

    def test_successful_mix_admits_complete_verified_cue_assignment(self):
        with TemporaryDirectory(prefix="dubflow-review-compose-success-") as directory:
            current,_,_,_=self.fixture(directory)
            current,document=self.admitted_mixer_stub(current,directory)
            with patch("engine.dubflow.review.audio_composition.StreamingAudioMixer") as mixer:
                mixer.return_value.mix.return_value=document
                self.assertEqual(mix_audio_candidate(current,output_root=Path(directory)/"candidate"),document)
                args,kwargs=mixer.return_value.mix.call_args
                self.assertEqual(args,(current.source,tuple(item.segment for item in current.cues)))
                self.assertEqual(kwargs,{"input_hash":current.input_hash})

    def test_mixer_failure_for_verified_waveform_or_wrong_input_refuses(self):
        with TemporaryDirectory(prefix="dubflow-review-compose-output-") as directory:
            current,_,_,_=self.fixture(directory)
            current,document=self.admitted_mixer_stub(current,directory)
            failed=replace(document,failures=(MixFailure("AUDIO_MISSING","cue-a","fixture mixer lost verified wave"),))
            wrong_input=replace(document,provenance=replace(document.provenance,input_hash="sha256:"+"b"*64))
            for output in (failed,wrong_input):
                with patch("engine.dubflow.review.audio_composition.StreamingAudioMixer") as mixer:
                    mixer.return_value.mix.return_value=output
                    with self.assertRaises(ReviewError):
                        mix_audio_candidate(current,output_root=Path(directory)/"candidate")


if __name__ == "__main__": unittest.main()

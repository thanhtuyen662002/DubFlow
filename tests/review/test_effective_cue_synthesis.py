"""Current requests through existing isolated TTS/acceptance adapters.

Tiny fixture engines and simulated native copies exercise metadata and PCM
contracts. They do not qualify native execution, real speech or graph undo.
"""
from dataclasses import replace
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.review.accepted_documents import AcceptedWavePin, prepare_accepted_cue_documents
from engine.dubflow.review.cue_execution import candidate_packet
from engine.dubflow.review.effective_cue_plan import prepare_effective_cue
from engine.dubflow.review.synthesis_input import _digest, _encode, read_cue_synthesis_input
from engine.dubflow.review.synthesis_worker import synthesize_cue_candidate
from schema_assertions import assert_schema
import test_effective_cue_plan as plans
import test_synthesis_worker as workers


class EffectiveCueSynthesisTests(unittest.TestCase):
    def prepare(self, directory, *, index=0, revision=2):
        helper = plans.EffectiveCuePlanTests()
        original, content = helper.fixture(directory, first_voice=True)
        current = replace(content, snapshot=replace(content.snapshot, revision=revision))
        proposal = helper.proposal(current, index=index, text="Người tôi vô cùng trân trọng.")
        context = helper.context(current, proposal)
        context["current_composition_sha256"] = content.input_hash
        snapshot = _encode(content.snapshot.to_dict(), 65536)
        context["composition_snapshot_sha256"] = _digest(snapshot)
        args = helper.arguments(original, content, directory, context)
        packet = prepare_effective_cue(_encode(context, 65536), **args)
        request = read_cue_synthesis_input(packet["request_json"].encode(), expected_hash=packet["request_sha256"])
        # Every preexisting fixture input/document/media is an independent pin.
        pins = {p:(p.read_bytes(),p.stat().st_mtime_ns) for p in Path(directory).rglob("*") if p.is_file()}
        return request, packet, current, proposal, pins

    def candidate(self, request, directory, engine):
        return synthesize_cue_candidate(request.encoded, expected_hash=request.input_hash,
            output_root=Path(directory)/"current-private", engine_factory=lambda _:engine,
            recipe_identity="a"*64)

    def assert_preserved(self, pins):
        for path, identity in pins.items():
            self.assertEqual((path.read_bytes(),path.stat().st_mtime_ns),identity)

    def test_restored_current_counter_and_prior_voice_survive_single_cue_tts_and_acceptance(self):
        with TemporaryDirectory(prefix="dubflow-effective-synthesis-") as directory:
            request, packet, current, proposal, pins = self.prepare(directory)
            self.assertEqual(packet["before_token"]["revision"],"2")
            self.assertEqual(request.to_dict()["revision"],"3")
            self.assertEqual(request.voice.voice_id,"vi-hai-dang-vieneu3-v1")
            self.assertEqual(current.snapshot.findings[1],proposal.findings[1])
            engine=workers.TrackingEngine()
            candidate=self.candidate(request,directory,engine)
            self.assertEqual([s.segment_id for s in engine.segments],["cue-a"])
            self.assertEqual(candidate.document.provenance,request.provenance)
            self.assertEqual(engine.closed,1)
            producer=candidate_packet(request,candidate,"a"*64)
            assert_schema({**producer,"model_weights_verified":True},"cue-candidate-v1.json")
            artifact=candidate.document.artifacts[0]
            accepted=Path(directory)/"simulated-native-output";accepted.mkdir()
            path=accepted/(artifact.content_hash[7:]+".wav")
            path.write_bytes(Path(artifact.path).read_bytes());path.chmod(stat.S_IRUSR)
            try:
                pin=AcceptedWavePin(path,accepted,artifact.content_hash,path.stat().st_size,
                    artifact.sample_rate,artifact.channels,artifact.frame_count)
                result=prepare_accepted_cue_documents(request,candidate_tts=producer["tts_document_json"].encode(),
                    candidate_spoken_text=producer["spoken_text_json"].encode(),accepted_wave=pin,recipe_identity="a"*64)
                speech=json.loads(result.tts);spoken=json.loads(result.spoken_text)
                self.assertEqual(speech["artifacts"][0]["path"],str(path))
                self.assertEqual(spoken["entries"][0]["original_input"],request.segment.to_dict())
                self.assertEqual(spoken["tts_document_hash"],result.tts_sha256)
                self.assertEqual(speech["provenance"]["voice_id"],"vi-hai-dang-vieneu3-v1")
                self.assert_preserved(pins)
            finally:
                path.chmod(stat.S_IRUSR|stat.S_IWUSR)

    def test_other_cue_execution_preserves_prior_edits_and_resume_keeps_pcm_without_new_inference(self):
        with TemporaryDirectory(prefix="dubflow-effective-other-cue-") as directory:
            request, packet, current, proposal, pins = self.prepare(directory,index=1,revision=3)
            self.assertEqual(proposal.findings[0],current.snapshot.findings[0])
            self.assertEqual(proposal.findings[0].voice,"vi-hai-dang-vieneu3-v1")
            self.assertEqual(request.to_dict()["revision"],"4")
            engine=workers.TrackingEngine();candidate=self.candidate(request,directory,engine)
            self.assertEqual([s.segment_id for s in engine.segments],["cue-b"])
            audio=Path(candidate.document.artifacts[0].path)
            audio_pin=(audio.read_bytes(),audio.stat().st_mtime_ns)
            tts=candidate.document_path.read_bytes();spoken=candidate.spoken_text_path.read_bytes()
            self.candidate(request,directory,engine)
            self.assertEqual(len(engine.segments),1)
            self.assertEqual((audio.read_bytes(),audio.stat().st_mtime_ns),audio_pin)
            self.assertEqual(candidate.document_path.read_bytes(),tts)
            self.assertEqual(candidate.spoken_text_path.read_bytes(),spoken)
            observation=json.loads((candidate.document_path.parent/"execution_observation_v1.json").read_bytes())
            self.assertEqual(observation["chunks"][0]["status"],"skipped")
            self.assert_preserved(pins)


if __name__=="__main__":
    unittest.main()

from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.review.cue_plan import catalog_voice, prepare_original_cue
from engine.dubflow.review.production import RegenerationPlan, ReviewError, ReviewFinding, ReviewSnapshot, _correction_stages
from engine.dubflow.review.synthesis_input import _digest, _encode, read_cue_synthesis_input
from engine.dubflow.review.synthesis_worker import production_cue_config
from engine.dubflow.tts import TtsInput, TtsProvenance
from engine.dubflow.tts import vieneu
from engine.dubflow.worker.b2_audio import B2_TTS_PRODUCER_VERSION
from schema_assertions import assert_schema
import test_synthesis_input as inputs

REPO = Path(__file__).resolve().parents[2]
PROFILE = REPO / "models/manifests/production-cpu-v1.json"
PIN = "sha256:" + "a" * 64


class CuePlanTests(unittest.TestCase):
    def context(self, *, voice=False):
        original_voice = catalog_voice(REPO, PROFILE, "vi-truc-ly-vieneu3-v1")
        config = production_cue_config()
        provenance = TtsProvenance("dubflow-production-tts", B2_TTS_PRODUCER_VERSION, vieneu.ENGINE_ID,
            vieneu.RUNTIME_ID, "timeline-v1", config.content_hash(), PIN, original_voice.model_id,
            original_voice.model_version, original_voice.model_hash, original_voice.content_hash(),
            original_voice.voice_id, original_voice.voice_version, "cpu", "cpu", config.resource)
        segment = TtsInput("cue-a", "cue-a", "Xin chào", inputs.helpers.point(100), inputs.helpers.point(900),
            source_language="en", confidence=0.4, render_window_end=inputs.helpers.point(1000))
        finding = ReviewFinding("finding-a", "TTS_FAILURE", "100", "900", "Hello", "Xin chào", None,
            original_voice.voice_id, None, "0.4", ("tts", "mix", "render"), cue_id="cue-a", confidence_source="cue.confidence")
        other = replace(finding, finding_id="finding-b", cue_id="cue-b", start_ticks="1000", end_ticks="1900")
        before = ReviewSnapshot("job-plan", 0, (finding, other))
        changed = replace(finding, voice="vi-hai-dang-vieneu3-v1") if voice else replace(finding, translated_text="Ngồi yên.")
        stages = _correction_stages(translation_changed=not voice, voice_changed=voice)
        after = replace(before, revision=1, findings=(changed, other), last_plan=RegenerationPlan("finding-a", stages,
            ("asr", "ocr"), tuple("replacement:" + stage for stage in stages)))
        graph = {"schema_version":1,"kind":"review_original_graph","job_id":"job-plan","revision":"0","mode":"b2",
            "manifest_sha256":PIN,"time_base":before.to_dict()["time_base"],"canonical_inputs":[segment.to_dict()],
            "tts_provenance":provenance.to_dict()}
        return {"schema_version":1,"kind":"review_original_cue_context","manifest_sha256":PIN,
            "before_snapshot_json":_encode(before.to_dict(),65536).decode(),"after_snapshot_json":_encode(after.to_dict(),65536).decode(),
            "source_graph_json":_encode(graph,65536).decode()}

    def prepare(self, context, **kwargs):
        return prepare_original_cue(_encode(context,65536),app_root=kwargs.get("app_root",REPO),profile_path=kwargs.get("profile_path",PROFILE))

    def test_original_translation_request_retains_pins_and_never_provisions_or_runs_models(self):
        context = self.context()
        with patch.object(vieneu,"load_vieneu_voice",side_effect=AssertionError("metadata planning must not provision weights")):
            packet = self.prepare(context)
        request = read_cue_synthesis_input(packet["request_json"].encode(),expected_hash=packet["request_sha256"])
        self.assertEqual(request.segment.normalized_text,"Ngồi yên.")
        self.assertEqual(request.segment.source_utterance_id,"cue-a")
        self.assertEqual(request.segment.start.ticks,100)
        self.assertEqual(request.segment.render_window_end.ticks,1000)
        self.assertEqual(request.provenance.producer_version,B2_TTS_PRODUCER_VERSION)
        self.assertFalse(packet["model_weights_verified"])
        self.assertEqual(packet["context_sha256"],_digest(_encode(context,65536)))
        assert_schema(request.to_dict(),"cue-synthesis-v1.json")
        assert_schema(context,"original-cue-context-v1.json")
        assert_schema(packet,"prepared-cue-request-v1.json")

    def test_selected_catalog_voice_changes_only_voice_identity_and_preserves_config(self):
        context = self.context(voice=True); packet = self.prepare(context)
        request = read_cue_synthesis_input(packet["request_json"].encode(),expected_hash=packet["request_sha256"])
        previous = json.loads(context["source_graph_json"])["tts_provenance"]
        self.assertEqual(request.voice.voice_id,"vi-hai-dang-vieneu3-v1")
        self.assertEqual(request.segment.text,"Xin chào")
        self.assertEqual(request.provenance.model_hash,previous["model_hash"])
        self.assertEqual(request.provenance.config_hash,previous["config_hash"])
        self.assertEqual(request.provenance.runtime,previous["runtime"])
        self.assertNotEqual(request.provenance.voice_hash,previous["voice_hash"])

    def test_stale_context_wrong_job_mode_producer_or_model_refuses(self):
        for case in range(6):
            context = self.context(); graph = json.loads(context["source_graph_json"])
            if case==0: graph["job_id"]="other-job"
            elif case==1: graph["mode"]="b1"
            elif case==2: graph["tts_provenance"]["producer_version"]="unadmitted-version"
            elif case==3: graph["tts_provenance"]["model_hash"]="sha256:"+"b"*64
            elif case==4: graph["schema_version"]=2
            else: graph["tts_provenance"]["config_hash"]="sha256:"+"b"*64
            context["source_graph_json"]=_encode(graph,65536).decode()
            with self.assertRaises(ReviewError): self.prepare(context)

    def test_changes_to_unrelated_cue_or_source_range_and_unknown_voice_refuse(self):
        for case in range(4):
            context = self.context(); after=json.loads(context["after_snapshot_json"])
            if case==0: after["findings"][1]["translated_text"]="Câu khác"
            elif case==1: after["findings"][0]["range"]["end_ticks"]="901"
            elif case==2: after["findings"][0]["voice"]="unlisted-voice"
            else: after["revision"]=2
            context["after_snapshot_json"]=_encode(after,65536).decode()
            with self.assertRaises(ReviewError): self.prepare(context)

    def test_catalog_recipe_inventory_and_same_voice_profile_changes_need_compatibility(self):
        with TemporaryDirectory(prefix="dubflow-cue-plan-catalog-") as directory:
            app=Path(directory); manifests=app/"models/manifests"; manifests.mkdir(parents=True)
            profile=manifests/PROFILE.name; shutil.copyfile(PROFILE,profile)
            recipe=manifests/"production-vieneu-v1.json"; shutil.copyfile(REPO/"models/manifests"/recipe.name,recipe)
            original=recipe.read_bytes()
            for case in range(4):
                value=json.loads(original)
                if case==0: value["backend"]="unknown-backend"
                elif case==1: value["license_id"]="unapproved-license"
                elif case==2: value["artifacts"].pop()
                else: value["voices"][0]["description"]+=" altered catalog bytes"
                recipe.write_bytes(_encode(value,65536))
                with self.assertRaises(ReviewError): self.prepare(self.context(),app_root=app,profile_path=profile)
            self.assertEqual(set(app.iterdir()),{app/"models"})

    def test_owned_isolated_cli_observes_byte_gate_and_returns_utf8_request_without_sql(self):
        with TemporaryDirectory(prefix="dubflow-cue-plan-cli-") as directory:
            path=Path(directory)/"context.json"; raw=_encode(self.context(),65536); path.write_bytes(raw)
            command=[sys.executable,"-I","-S","-B",str(REPO/"engine/dubflow/review/cue_plan.py"),"--context",str(path),
                "--context-sha256",_digest(raw),"--app-root",str(REPO),"--profile",str(PROFILE)]
            result=subprocess.run(command,input=b"\x01",stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,check=False)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors="replace")); self.assertEqual(result.stderr,b"")
            packet=json.loads(result.stdout); self.assertIn("Ngồi yên.",packet["request_json"])
            refused=subprocess.run(command,input=b"\x00",stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,check=False)
            self.assertEqual(refused.returncode,2); self.assertEqual(refused.stdout,b"")
            self.assertEqual(path.read_bytes(),raw)
            self.assertEqual(list(path.parent.iterdir()),[path])


if __name__ == "__main__": unittest.main()

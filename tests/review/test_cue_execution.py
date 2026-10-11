from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.review.cue_execution import accepted_packet, candidate_packet
from engine.dubflow.review.cue_plan import catalog_voice, prepare_original_cue
from engine.dubflow.review.existing_models import admit_existing_cue_pack
from engine.dubflow.review.production import ReviewError
from engine.dubflow.review.synthesis_input import _digest, _encode, read_cue_synthesis_input
from engine.dubflow.tts import vieneu
from engine.dubflow.tts.neural_vits import _json_bytes
from schema_assertions import assert_schema
import test_accepted_documents as documents
import test_cue_plan as plans


class CueExecutionTests(unittest.TestCase):
    def installed(self, root, *, voice=False):
        root = Path(root)
        app, models = root / "app", root / "models"
        metadata = app / "models/manifests"
        metadata.mkdir(parents=True)
        selection = json.loads(plans.PROFILE.read_bytes())
        profile = json.loads((plans.REPO / selection["tts_neural_profile"]).read_bytes())
        payload = {name: ("synthetic contract data: " + name).encode() for name in vieneu.FILES}
        payload["voices.json"] = _encode({"presets":{item["name"]:{} for item in profile["voices"]}},65536)
        records = {name:{"sha256":hashlib.sha256(raw).hexdigest(),"size_bytes":len(raw)} for name,raw in payload.items()}
        tree = hashlib.sha256(_json_bytes(records)).hexdigest()
        prefix = "tts/packs/vieneu-" + tree + "/"
        profile["model_tree_sha256"] = tree
        for artifact in profile["artifacts"]:
            name = artifact["path"].split("/",3)[3]
            artifact.update(path=prefix+name, **records[name])
        (metadata / "production-cpu-v1.json").write_bytes(_encode(selection,65536))
        recipe_path = app / selection["tts_neural_profile"]
        recipe_path.write_bytes(_encode(profile,65536))
        for name, raw in payload.items():
            path = models / prefix / name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw)
        context = plans.CuePlanTests().context(voice=voice)
        original = catalog_voice(app,metadata/"production-cpu-v1.json","vi-truc-ly-vieneu3-v1")
        graph = json.loads(context["source_graph_json"])
        graph["tts_provenance"].update(model_hash=original.model_hash,voice_hash=original.content_hash())
        context["source_graph_json"] = _encode(graph,65536).decode()
        packet = prepare_original_cue(_encode(context,65536),app_root=app,profile_path=metadata/"production-cpu-v1.json")
        request = read_cue_synthesis_input(packet["request_json"].encode(),expected_hash=packet["request_sha256"])
        return request, dict(app_root=app,model_root=models,profile_path=metadata/"production-cpu-v1.json"), models/prefix

    def test_existing_pack_admits_both_presets_without_provisioning_or_writes(self):
        for voice in (False,True):
            with TemporaryDirectory(prefix="dubflow-review-existing-pack-") as directory:
                request, args, pack_root = self.installed(directory,voice=voice)
                before = {p.relative_to(directory):p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()}
                with patch.object(vieneu,"load_vieneu_voice",side_effect=AssertionError("no provisioning")):
                    pack = admit_existing_cue_pack(request,**args)
                self.assertEqual(pack.path,pack_root)
                self.assertEqual(pack.voice_name,request.voice.display_name)
                self.assertEqual(pack.model_hash,request.voice.model_hash)
                self.assertEqual(before,{p.relative_to(directory):p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()})

    def test_missing_corrupt_or_foreign_weights_refuse_without_repair(self):
        for condition in ("missing","corrupt","foreign","partial"):
            with TemporaryDirectory(prefix="dubflow-review-existing-drift-") as directory:
                request,args,pack_root = self.installed(directory)
                path = pack_root / "LICENSE"
                if condition=="missing": path.unlink()
                elif condition=="corrupt":
                    raw=path.read_bytes(); path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
                else: (pack_root / ("foreign.bin" if condition=="foreign" else ".LICENSE.partial")).write_bytes(b"unadmitted")
                before = {p.relative_to(directory):p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()}
                with self.assertRaises((ReviewError,OSError)): admit_existing_cue_pack(request,**args)
                self.assertEqual(before,{p.relative_to(directory):p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()})

    def test_missing_native_model_root_and_changed_voice_refuse_without_creation(self):
        with TemporaryDirectory(prefix="dubflow-review-existing-root-") as directory:
            request,args,_ = self.installed(directory)
            missing = Path(directory)/"never provision here"
            with self.assertRaises((ReviewError,OSError)):
                admit_existing_cue_pack(request,**{**args,"model_root":missing})
            self.assertFalse(missing.exists())
            with self.assertRaisesRegex(ReviewError,"profile differs"):
                admit_existing_cue_pack(replace(request,voice=replace(request.voice,voice_version="foreign")),**args)

    @unittest.skipIf(os.name=="nt","Windows link privileges are a separate native lane")
    def test_linked_model_is_refused_even_with_the_pinned_bytes(self):
        with TemporaryDirectory(prefix="dubflow-review-existing-link-") as directory:
            request,args,pack = self.installed(directory)
            path=pack/"LICENSE"; outside=Path(directory)/"same bytes"
            outside.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(outside)
            with self.assertRaises((ReviewError,OSError,ValueError)):
                admit_existing_cue_pack(request,**args)

    def context(self,request,candidate,pin):
        return {"schema_version":1,"kind":"review_native_accepted_cue_context","request_json":request.encoded.decode(),
            "request_sha256":request.input_hash,"candidate_tts_json":candidate.document_path.read_text(encoding="utf-8"),
            "candidate_spoken_text_json":candidate.spoken_text_path.read_text(encoding="utf-8"),"recipe_identity":"fixture-source-recipe-v1",
            "accepted_wave":{"path":str(pin.path),"root":str(pin.root),"sha256":pin.sha256,"size_bytes":pin.size_bytes,
            "sample_rate":pin.sample_rate,"channels":pin.channels,"frame_count":pin.frame_count}}

    def test_candidate_and_accepted_packets_preserve_request_and_only_retarget_storage(self):
        with documents.AcceptedDocumentsTests().fixture() as (request,candidate,pin):
            packet=candidate_packet(request,candidate,"fixture-source-recipe-v1")
            self.assertEqual(packet["request_sha256"],request.input_hash)
            self.assertNotIn("model_weights_verified",packet) # Only production entry can make that assertion.
            context=self.context(request,candidate,pin)
            prepared=accepted_packet(_encode(context,262144))
            self.assertEqual(prepared["request_sha256"],request.input_hash)
            tts=json.loads(prepared["tts_document_json"]); expected=json.loads(packet["tts_document_json"])
            expected["artifacts"][0]["path"]=str(pin.path)
            self.assertEqual(tts,expected)
            self.assertEqual(_digest(prepared["tts_document_json"].encode()),prepared["tts_document_sha256"])
            assert_schema(context,"native-accepted-cue-context-v1.json")
            assert_schema(prepared,"accepted-cue-documents-v1.json")
            production={**packet,"recipe_identity":"a"*64,"model_weights_verified":True}
            assert_schema(production,"cue-candidate-v1.json")
            for change in ("request_sha256","recipe_identity","kind"):
                bad={**context,change:"foreign"}
                with self.assertRaises((ReviewError,ValueError)):accepted_packet(_encode(bad,262144))

    def command(self,*args):
        return [os.environ.get("DUBFLOW_TEST_PYTHON",sys.executable),"-I","-S","-B",
            str(plans.REPO/"engine/dubflow/review/cue_execution.py"),*args]

    def test_native_gate_and_bad_context_pin_refuse_before_reading_models_or_output(self):
        with TemporaryDirectory(prefix="dubflow-review-execution-cli-") as directory:
            path=Path(directory)/"context.json";path.write_bytes(b"{}")
            for gate,pin in ((b"",_digest(b"{}")),(b"\x01",plans.PIN)):
                result=subprocess.run(self.command("accept","--context",str(path),"--context-sha256",pin),
                    input=gate,capture_output=True,timeout=20)
                self.assertEqual(result.returncode,2)
                self.assertEqual(result.stdout,b"")
                self.assertEqual(json.loads(result.stderr)["code"],"REVIEW_CUE_EXECUTION_FAILED")
                self.assertEqual(list(Path(directory).iterdir()),[path])

    def test_isolated_owned_accept_cli_returns_exact_utf8_documents(self):
        with documents.AcceptedDocumentsTests().fixture() as (request,candidate,pin):
            raw=_encode(self.context(request,candidate,pin),262144)
            context_path=candidate.document_path.parent/"native-context.json";context_path.write_bytes(raw)
            result=subprocess.run(self.command("accept","--context",str(context_path),"--context-sha256",_digest(raw)),
                input=b"\x01",capture_output=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stderr,b"")
            self.assertEqual(json.loads(result.stdout.decode("utf-8")),accepted_packet(raw))


if __name__=="__main__":
    unittest.main()

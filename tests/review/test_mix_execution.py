from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.mix import streaming
from engine.dubflow.mix.adapter import parse_mix_json
from engine.dubflow.review.audio_composition import PinnedDocument
from engine.dubflow.review.mix_execution import prepare_composition, produce_mix
from engine.dubflow.review.production import ReviewError
from engine.dubflow.review.synthesis_input import _digest, _encode
from schema_assertions import assert_schema
import test_audio_composition as audio


class MixExecutionTests(unittest.TestCase):
    def fixture(self, root):
        root=Path(root)
        helper=audio.AudioCompositionTests()
        current,store,config,voice=helper.fixture(root)
        after,request,pin=helper.correction(current,store,config,voice,root/"replacement")
        metadata=root/"sealed metadata";metadata.mkdir()
        def document(pin):
            target=metadata/(pin.sha256[7:]+".json")
            target.write_bytes(pin.read())
            return PinnedDocument(target,pin.sha256,pin.size_bytes,pin.artifact_root,metadata).to_dict()
        template_path=root/"before/mix_document.json"
        template=PinnedDocument(template_path,audio.sha(template_path),template_path.stat().st_size,root/"before")
        graph={"schema_version":1,"kind":"review_original_graph","job_id":current.snapshot.job_id,"revision":"0",
            "mode":"b2","manifest_sha256":current.manifest_sha256,"time_base":current.snapshot.to_dict()["time_base"],
            "canonical_inputs":[item.canonical_input.to_dict() for item in current.cues]}
        context={"schema_version":1,"kind":"review_native_mix_context","manifest_sha256":current.manifest_sha256,
            "before_snapshot_json":_encode(current.snapshot.to_dict(),65536).decode(),"after_snapshot_json":_encode(after.to_dict(),65536).decode(),
            "source_graph_json":_encode(graph,65536).decode(),"request_json":request.encoded.decode(),"request_sha256":request.input_hash,
            "source_audio":{"path":str(current.source.path),"sha256":current.source_sha256,
                "size_bytes":current.source.path.stat().st_size,"artifact_root":str(root/"before")},
            "original_tts_document":document(current.cues[0].document),"mix_template":document(template),
            "accepted_tts_document":document(pin)}
        return context,current,request,template

    def test_sealed_metadata_and_original_audio_have_distinct_confined_roots(self):
        with TemporaryDirectory(prefix="dubflow-review-mix-context-") as root:
            context,current,request,_=self.fixture(root)
            assert_schema(context,"native-mix-context-v1.json")
            updated=prepare_composition(_encode(context,65536))
            self.assertEqual(updated.cues[0].canonical_input,request.segment)
            self.assertEqual(updated.cues[1].canonical_input,current.cues[1].canonical_input)
            self.assertEqual(updated.cues[1].segment,current.cues[1].segment)
            self.assertEqual(updated.cues[1].provenance,current.cues[1].provenance)
            self.assertNotEqual(updated.cues[1].document.metadata_root,updated.cues[1].document.artifact_root)
            assert_schema(updated.to_dict(),"audio-composition-v2.json")
            self.assertEqual(current.to_dict()["schema_version"],1)
            assert_schema(current.to_dict(),"audio-composition-v1.json")

    def test_wrong_roots_source_pin_request_or_version_refuse_without_output(self):
        with TemporaryDirectory(prefix="dubflow-review-mix-binding-") as root:
            context,_,_,_=self.fixture(root)
            before={p:p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}
            for case in range(6):
                wrong=json.loads(_encode(context,65536))
                if case==0: wrong["schema_version"]=2
                elif case==1: wrong["request_sha256"]="sha256:"+"b"*64
                elif case==2: wrong["source_audio"]["sha256"]="sha256:"+"c"*64
                elif case==3: wrong["source_audio"]["size_bytes"]+=1
                elif case==4: wrong["original_tts_document"]["metadata_root"]=str(Path(root)/"before")
                else: wrong["accepted_tts_document"]["artifact_root"]=str(Path(root)/"sealed metadata")
                with self.assertRaises((ReviewError,ValueError)):
                    prepare_composition(_encode(wrong,65536))
            self.assertEqual(before,{p:p.read_bytes() for p in Path(root).rglob("*") if p.is_file()})

    def test_private_packet_binds_composition_without_running_speech_or_sql(self):
        with TemporaryDirectory(prefix="dubflow-review-mix-packet-") as root:
            context,_,_,template=self.fixture(root)
            raw=_encode(context,65536);composition=prepare_composition(raw)
            # Contract fixture only. Real streamed DSP is qualified separately.
            document=streaming._restore(parse_mix_json(template.read()))
            document=replace(document,provenance=replace(document.provenance,input_hash=composition.input_hash))
            with (patch("engine.dubflow.review.audio_composition.mix_audio_candidate",return_value=document) as mixer,
                    patch("engine.dubflow.tts.LocalTtsAdapter.synthesize",side_effect=AssertionError("TTS cannot rerun")) as tts):
                packet=produce_mix(raw,output_root=Path(root)/"private mix")
            self.assertEqual(tts.call_count,0)
            self.assertEqual(mixer.call_args.args,(composition,))
            self.assertEqual(packet["context_sha256"],_digest(raw))
            self.assertEqual(packet["composition_sha256"],_digest(packet["composition_json"].encode()))
            self.assertEqual(packet["mix_document_sha256"],_digest(packet["mix_document_json"].encode()))
            self.assertFalse(packet["active_graph_published"])
            assert_schema(packet,"mix-candidate-v1.json")

    def test_failed_mix_keeps_all_original_files_and_does_not_publish(self):
        with TemporaryDirectory(prefix="dubflow-review-mix-failure-") as root:
            context,_,_,_=self.fixture(root)
            before={p:p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}
            with patch("engine.dubflow.review.audio_composition.mix_audio_candidate",side_effect=ReviewError("injected mix failure")):
                with self.assertRaisesRegex(ReviewError,"injected mix"):
                    produce_mix(_encode(context,65536),output_root=Path(root)/"private mix")
            self.assertFalse((Path(root)/"private mix").exists())
            self.assertEqual(before,{p:p.read_bytes() for p in Path(root).rglob("*") if p.is_file()})

    def test_isolated_gate_and_context_hash_refuse_before_any_candidate_write(self):
        repo=Path(__file__).resolve().parents[2]
        python=os.environ.get("DUBFLOW_TEST_PYTHON",sys.executable)
        with TemporaryDirectory(prefix="dubflow-review-mix-cli-") as root:
            path=Path(root)/"context.json";path.write_bytes(b"{}")
            for gate,pin in ((b"",_digest(b"{}")),(b"\x01","sha256:"+"a"*64)):
                result=subprocess.run([python,"-I","-S","-B",str(repo/"engine/dubflow/review/mix_execution.py"),
                    "--context",str(path),"--context-sha256",pin,"--output-root",str(Path(root)/"private mix")],
                    input=gate,capture_output=True,timeout=20)
                self.assertEqual(result.returncode,2)
                self.assertEqual(result.stdout,b"")
                self.assertEqual(json.loads(result.stderr)["code"],"REVIEW_MIX_EXECUTION_FAILED")
                self.assertEqual(list(Path(root).iterdir()),[path])


if __name__=="__main__":
    unittest.main()

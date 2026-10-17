from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.review import ReviewError
from engine.dubflow.review.effective_cue_execution import prepare_bound_cue
from engine.dubflow.review.synthesis_input import _digest, _encode, read_cue_synthesis_input
from engine.dubflow.tts import vieneu
from schema_assertions import assert_schema
from test_cue_plan import REPO, PROFILE
import test_effective_cue_plan as cue_helpers

ENTRY = REPO / "engine/dubflow/review/effective_cue_execution.py"


class EffectiveCueDispatchTests(unittest.TestCase):
    def fixture(self, directory):
        helper = cue_helpers.EffectiveCuePlanTests()
        original, content = helper.fixture(directory, first_voice=True)
        current = replace(content, snapshot=replace(content.snapshot, revision=3, findings=tuple(
            replace(f, output_preview=str(Path(directory) / "retained.mp4")) for f in content.snapshot.findings)))
        context = helper.context(current, helper.proposal(current, index=1))
        context["current_composition_sha256"] = content.input_hash
        snapshot_raw = _encode(content.snapshot.to_dict(), 65536)
        context["composition_snapshot_sha256"] = _digest(snapshot_raw)
        args = helper.arguments(original, content, directory, context)
        source = original.source.path
        bindings = {"schema_version":1,"kind":"review_native_effective_cue_bindings",
            "current_token":context["before_token"],"content_composition_json":args["composition_raw"].decode(),
            "content_composition_sha256":content.input_hash,"composition_snapshot_json":snapshot_raw.decode(),
            "composition_snapshot_sha256":_digest(snapshot_raw),
            "source_audio":{"path":str(source),"sha256":original.source_sha256,
                            "size_bytes":source.stat().st_size,"artifact_root":str(source.parent)},
            "canonical_inputs":[item.to_dict() for item in args["original_inputs"]],
            "documents":[pin.to_dict() for pin in sorted(args["documents"],key=lambda p:p.sha256)],
            "mix_template":args["mix_template"].to_dict()}
        assert_schema(bindings, "native-effective-cue-bindings-v1.json")
        return context, bindings, content

    def command(self, context, bindings, directory, flags=("-I", "-S", "-B")):
        root = Path(directory)
        context_path = root / "native-context.json"
        binding_path = root / "native-bindings.json"
        context_raw = _encode(context, 65536); bindings_raw = _encode(bindings, 65536)
        context_path.write_bytes(context_raw); binding_path.write_bytes(bindings_raw)
        return [sys.executable, *flags, str(ENTRY), "--context", str(context_path),
            "--context-sha256", _digest(context_raw), "--bindings", str(binding_path),
            "--bindings-sha256", _digest(bindings_raw), "--app-root", str(REPO), "--profile", str(PROFILE)]

    def test_owned_isolated_subprocess_prepares_current_revision_without_touching_media(self):
        with TemporaryDirectory(prefix="dubflow-effective-dispatch-") as directory:
            context, bindings, content = self.fixture(directory)
            command = self.command(context, bindings, directory)
            media = {p:(p.read_bytes(),p.stat().st_mtime_ns) for p in
                     {item.document.path for item in content.cues} | {item.segment.path for item in content.cues}}
            result = subprocess.run(command, input=b"\x01", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, b"")
            packet = json.loads(result.stdout)
            request = read_cue_synthesis_input(packet["request_json"].encode(), expected_hash=packet["request_sha256"])
            self.assertEqual(packet["before_token"]["revision"], "3")
            self.assertEqual(request.to_dict()["revision"], "4")
            self.assertEqual(json.loads(packet["effective_composition_json"])["cues"], content.to_dict()["cues"])
            self.assertFalse(packet["model_weights_verified"])
            self.assertFalse(packet["active_graph_published"])
            assert_schema(packet, "prepared-effective-cue-request-v1.json")
            for p,identity in media.items():
                self.assertEqual((p.read_bytes(),p.stat().st_mtime_ns),identity)

    def test_byte_gate_and_isolation_refuse_before_reading_missing_native_files(self):
        missing = Path("missing-native-effective-input.json")
        command = [sys.executable,"-I","-S","-B",str(ENTRY),"--context",str(missing),
            "--context-sha256","sha256:"+"a"*64,"--bindings",str(missing),
            "--bindings-sha256","sha256:"+"a"*64,"--app-root",str(REPO),"--profile",str(PROFILE)]
        for command_value, gate in ((command,b"\x00"),([item for item in command if item!="-I"],b"\x01")):
            result = subprocess.run(command_value,input=gate,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
            self.assertEqual(result.returncode,2)
            self.assertEqual(result.stdout,b"")
            error = json.loads(result.stderr)
            self.assertIn("native byte gate",error["message"])

    def test_bound_dispatch_refuses_stale_token_unknown_fields_or_substituted_source_identity(self):
        with TemporaryDirectory(prefix="dubflow-effective-dispatch-refuse-") as directory:
            context, bindings, _ = self.fixture(directory)
            changes=[]
            changed=deepcopy(bindings);changed["current_token"]["revision"]="2";changes.append(changed)
            changed=deepcopy(bindings);changed["extra"]=True;changes.append(changed)
            changed=deepcopy(bindings);changed["source_audio"]["sha256"]="sha256:"+"b"*64;changes.append(changed)
            changed=deepcopy(bindings);changed["content_composition_sha256"]="sha256:"+"b"*64;changes.append(changed)
            with patch.object(vieneu,"load_vieneu_voice",side_effect=AssertionError("metadata dispatch loaded weights")):
                for changed in changes:
                    with self.assertRaises(ReviewError):
                        prepare_bound_cue(_encode(context,65536),_encode(changed,65536),app_root=REPO,profile_path=PROFILE)

    def test_bound_dispatch_rechecks_actual_pcm_and_preserves_original_failure_evidence(self):
        with TemporaryDirectory(prefix="dubflow-effective-dispatch-corrupt-") as directory:
            context, bindings, content = self.fixture(directory)
            path=content.cues[0].segment.path
            raw=path.read_bytes();bad=raw[:-1]+bytes([raw[-1]^1]);path.write_bytes(bad)
            with self.assertRaisesRegex(ValueError,"hash"):
                prepare_bound_cue(_encode(context,65536),_encode(bindings,65536),app_root=REPO,profile_path=PROFILE)
            self.assertEqual(path.read_bytes(),bad)


if __name__=="__main__":
    unittest.main()

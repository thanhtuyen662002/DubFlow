import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine.dubflow.review.longform_clock_execution import admit_context, execute_context
from engine.dubflow.review.longform_execution import _metadata
from engine.dubflow.review.longform_plan import encode_plan, plan_hash
from engine.dubflow.review.longform_windows import clock_spec, read_window
from engine.dubflow.review.production import ReviewError


def pin(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class ClockExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dubflow-clock-entry-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.probe = self.root/"source.mp4", self.root/"ffprobe.exe"
        self.source.write_bytes(b"source"); self.probe.write_bytes(b"probe")
        fixture = json.loads((Path(__file__).parent/"native_decoded_clock_windows_v1.json").read_bytes())
        self.case = fixture["cases"][0]
        self.context = dict(schema_version=1, kind="review_native_clock_execution_context",
            clock_spec=self.case["spec"], ordinal=1, cursor=self.case["start"], anchor=None, overlap=[],
            source=dict(path=str(self.source),sha256=pin(b"source"),size_bytes="6"),
            source_root=str(self.root),ffprobe=dict(path=str(self.probe),sha256=pin(b"probe"),size_bytes="5"),
            media_root=str(self.root),processing_clock_recipe_sha256=pin(b"clock recipe"),
            processing_release_sha256=pin(b"native complete processing release"),
            packet_limit=4096,timeout_seconds=10)

    def encoded(self, context=None):
        return encode_plan(self.context if context is None else context)

    def test_contracts_keep_exact_signed64_integer_boundaries(self):
        root=Path(__file__).resolve().parents[2]
        for name,properties in (("decoded-clock-window-v1.json",("overlap","items","properties")),
                ("native-clock-execution-context-v1.json",("$defs","overlap","items","properties"))):
            value=json.loads((root/"contracts/review"/name).read_bytes())
            if name=="decoded-clock-window-v1.json":value=value["properties"]
            for key in properties:value=value[key]
            self.assertEqual(value["pts"]["minimum"],-(1<<63))
            self.assertEqual(value["pts"]["maximum"],(1<<63)-1)
            self.assertEqual(value["packet_pos"]["maximum"],(1<<63)-1)

    def test_closed_native_context_preserves_wide_pts_and_refuses_changed_pins_or_policy(self):
        raw=self.encoded()
        self.assertEqual(admit_context(raw,plan_hash(raw)),self.context)
        changed=copy.deepcopy(self.context)
        fixture=json.loads((Path(__file__).parent/"native_decoded_clock_windows_v1.json").read_bytes())
        changed["clock_spec"]=fixture["cases"][1]["spec"];changed["cursor"]=fixture["cases"][1]["start"]
        self.assertEqual(admit_context(self.encoded(changed),plan_hash(self.encoded(changed)))["cursor"],"9007199254740995")
        for field,value in (("ordinal",True),("cursor","-49"),("packet_limit",16385),
                ("timeout_seconds",181),("unknown",None)):
            v=copy.deepcopy(self.context);v[field]=value;raw=self.encoded(v)
            with self.subTest(field=field),self.assertRaises(ReviewError):admit_context(raw,plan_hash(raw))
        raw=self.encoded()
        with self.assertRaises(ReviewError):admit_context(raw,pin(b"wrong context"))
        v=copy.deepcopy(self.context);v["source"]["sha256"]=pin(b"foreign")
        with self.assertRaises(ReviewError):admit_context(self.encoded(v),plan_hash(self.encoded(v)))

    def test_owned_window_result_binds_context_ordinal_recipe_and_false_authority(self):
        raw=self.encoded();window=self.case["windows"][0].encode();calls=[]
        with patch("engine.dubflow.review.longform_windows.probe_window",return_value=window)as producer:
            encoded=execute_context(raw,expected_hash=plan_hash(raw),context_fence=lambda:calls.append("fenced"))
        result=json.loads(encoded)
        self.assertEqual(result["context_sha256"],plan_hash(raw))
        self.assertEqual(result["ordinal"],1);self.assertEqual(result["window_json"],window.decode())
        self.assertEqual(result["window_sha256"],plan_hash(window))
        self.assertEqual(result["processing_clock_recipe_sha256"],self.context["processing_clock_recipe_sha256"])
        self.assertFalse(result["native_decoded_clock_admitted"]);self.assertFalse(result["active_graph_published"])
        self.assertEqual(calls,["fenced","fenced"])
        self.assertEqual(producer.call_args.kwargs["source_pin"],self.context["source"])
        self.assertEqual(producer.call_args.kwargs["expected_overlap"],[])

    def test_refused_context_fence_never_starts_decoder_or_returns_a_result(self):
        raw=self.encoded()
        def refuse():raise ReviewError("native context changed")
        with patch("engine.dubflow.review.longform_windows.probe_window")as producer:
            with self.assertRaisesRegex(ReviewError,"native context changed"):
                execute_context(raw,expected_hash=plan_hash(raw),context_fence=refuse)
            producer.assert_not_called()

    def test_owned_executable_dedup_does_not_waive_single_link_source_context_checks(self):
        alias=self.root/"owned-probe-dedup.exe"
        os.link(self.probe,alias)
        raw=self.encoded()
        self.assertEqual(admit_context(raw,plan_hash(raw))["ffprobe"],self.context["ffprobe"])
        source_alias=self.root/"foreign-source-hardlink.mp4"
        os.link(self.source,source_alias)
        from engine.dubflow.mix import MixError
        with self.assertRaises(MixError):admit_context(raw,plan_hash(raw))

    def test_identical_context_bytes_at_a_new_identity_refuse_after_actual_physical_fence(self):
        raw=self.encoded();context_path=self.root/"sealed-context.json"
        context_path.write_bytes(raw);context_path.chmod(stat.S_IREAD)
        self.addCleanup(lambda:context_path.chmod(stat.S_IWRITE)if context_path.exists()else None)
        context_pin=dict(path=str(context_path),sha256=plan_hash(raw),size_bytes=str(len(raw)))
        observed={}
        import time
        deadline=time.monotonic()+10
        _metadata(context_pin,self.root,4*1024*1024,deadline,observed=observed)
        def fence():_metadata(context_pin,self.root,4*1024*1024,deadline,observed=observed)
        def changed(**unused):
            replacement=self.root/"replacement-context.json";replacement.write_bytes(raw)
            context_path.chmod(stat.S_IWRITE);context_path.unlink()
            replacement.rename(context_path);context_path.chmod(stat.S_IREAD)
            return self.case["windows"][0].encode()
        with patch("engine.dubflow.review.longform_windows.probe_window",side_effect=changed):
            with self.assertRaisesRegex(ReviewError,"physical identity"):
                execute_context(raw,expected_hash=plan_hash(raw),context_fence=fence)

    def test_isolated_entry_requires_byte_gate_before_context_read_or_engine_import(self):
        entry=Path(__file__).resolve().parents[2]/"engine/dubflow/review/longform_clock_execution.py"
        flags=subprocess.CREATE_NO_WINDOW if os.name=="nt"else 0
        result=subprocess.run((sys.executable,"-I","-S","-B",str(entry),"--context",
            str(self.root/"does-not-exist.json"),"--context-sha256",pin(b"unused")),
            input=b"\0",capture_output=True,timeout=10,creationflags=flags)
        self.assertEqual(result.returncode,2);self.assertEqual(result.stdout,b"")
        self.assertIn(b"native byte gate",result.stderr)

    @unittest.skipUnless(os.environ.get("DUBFLOW_REVIEW_TEST_MEDIA_ROOT"),"explicit owned media fixture not selected")
    def test_actual_isolated_entry_runs_owned_vfr_probe_and_returns_one_bound_window(self):
        media=Path(os.environ["DUBFLOW_REVIEW_TEST_MEDIA_ROOT"])
        ffmpeg,probe=media/"ffmpeg.exe",media/"ffprobe.exe"
        before={p:(pin(p.read_bytes()),p.stat().st_mtime_ns)for p in(ffmpeg,probe)}
        source=self.root/"new isolated clock VFR.mkv"
        flags=subprocess.CREATE_NO_WINDOW if os.name=="nt"else 0
        made=subprocess.run((str(ffmpeg),"-hide_banner","-loglevel","error","-nostdin",
            "-f","lavfi","-i","testsrc2=size=96x64:rate=20:duration=4","-vf",
            "select=not(eq(mod(n\\,5)\\,1)),setpts=PTS+2/TB","-fps_mode","passthrough",
            "-enc_time_base","1:1000","-c:v","ffv1","-g","4","-an",str(source)),
            capture_output=True,timeout=20,creationflags=flags)
        self.assertEqual(made.returncode,0,made.stderr);self.assertEqual(made.stderr,b"")
        source_before=(pin(source.read_bytes()),source.stat().st_mtime_ns)
        context=copy.deepcopy(self.context)
        context.update(clock_spec=clock_spec(source_sha256=source_before[0],ffprobe_sha256=before[probe][0],
            video_stream_index=0,time_base=(1,1000),start_tick=2000,end_tick=6000),cursor="2000",
            source=dict(path=str(source),sha256=source_before[0],size_bytes=str(source.stat().st_size)),
            ffprobe=dict(path=str(probe),sha256=before[probe][0],size_bytes=str(probe.stat().st_size)),
            media_root=str(media),packet_limit=17,timeout_seconds=20)
        raw=self.encoded(context);context_path=self.root/"clock-entry-context.json"
        context_path.write_bytes(raw);context_path.chmod(stat.S_IREAD)
        self.addCleanup(lambda:context_path.chmod(stat.S_IWRITE)if context_path.exists()else None)
        entry=Path(__file__).resolve().parents[2]/"engine/dubflow/review/longform_clock_execution.py"
        result=subprocess.run((sys.executable,"-I","-S","-B",str(entry),"--context",str(context_path),
            "--context-sha256",plan_hash(raw)),input=b"\x01",capture_output=True,timeout=30,creationflags=flags)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stderr,b"")
        value=json.loads(result.stdout);self.assertEqual(value["context_sha256"],plan_hash(raw))
        window=read_window(value["window_json"].encode(),spec=context["clock_spec"],cursor=2000,anchor=None)
        self.assertEqual(window["pts"][0],"2000");self.assertFalse(window["complete"])
        self.assertFalse(value["native_decoded_clock_admitted"])
        self.assertEqual((pin(source.read_bytes()),source.stat().st_mtime_ns),source_before)
        for p,v in before.items():self.assertEqual((pin(p.read_bytes()),p.stat().st_mtime_ns),v)


if __name__=="__main__":unittest.main()

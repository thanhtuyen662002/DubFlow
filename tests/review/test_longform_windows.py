import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from engine.dubflow.review.longform_plan import encode_plan, plan_hash
from engine.dubflow.review.longform_probe import probe_plan_resumable
from engine.dubflow.review.longform_windows import (
    _observation, _seek, clock_spec, probe_window, read_window, resume_plan)
from engine.dubflow.review.production import ReviewError


def pin(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def observed(points, *, packets=None, stream=2):
    return dict(packets=packets if packets is not None else len(points),
        frames=[dict(pts=p, key_frame=1, packet_pos=100+i, duration=10)
                for i, p in enumerate(points)])


class DecodedClockWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dubflow-window-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe, self.source = self.root/"ffprobe.exe", self.root/"source.mp4"
        self.exe.write_bytes(b"owned test executable")
        self.source.write_bytes(b"owned test source")
        self.spec = clock_spec(source_sha256=pin(self.source.read_bytes()),
            ffprobe_sha256=pin(self.exe.read_bytes()), video_stream_index=2,
            time_base=(1,1000), start_tick=0, end_tick=80)
        self.args = dict(ffprobe_path=self.exe, media_root=self.root,
            executable_pin=dict(sha256=self.spec["ffprobe_sha256"], size_bytes=str(self.exe.stat().st_size)),
            source_pin=dict(path=str(self.source), sha256=self.spec["source_sha256"],
                            size_bytes=str(self.source.stat().st_size)),
            source_root=self.root, packet_limit=6, timeout_seconds=5)
        self.seen = []

    def process(self, argv, consume, **bounds):
        interval = argv[argv.index("-read_intervals")+1]
        self.seen.append(interval)
        # Keep a complete guard GOP before the packet-limited decoder flush tail.
        points = range(0,60,10) if interval.startswith("%") else range(30,80,10)
        frames = [dict(pts=p,key_frame=1,pkt_pos=str(100+p//10),duration=10) for p in points]
        payload = dict(frames=frames, streams=[dict(index=2,nb_read_packets=str(len(frames)))])
        return consume(iter((json.dumps(payload).encode(),)))

    def test_window_checkpoint_resume_uses_only_new_window_and_reopens_all_points(self):
        journal = {}
        def save(n, raw):
            journal[n] = raw
            return journal[n]
        args = dict(spec=self.spec, read_checkpoint=journal.get, checkpoint=save, fence=lambda:None,
            context_sha256=pin(b"context"), processing_recipe_sha256=pin(b"recipe"),
            target_ticks=25, visual_dependencies=lambda a,b:pin(b"visual"),
            probe_args={k:v for k,v in self.args.items() if k!="timeout_seconds"}, timeout_seconds=5)
        with patch("engine.dubflow.review.longform_probe._consume_process", side_effect=self.process):
            first = probe_plan_resumable(**args)
            self.assertFalse(first["complete"])
            self.assertEqual(first["next_tick"], "30")
            original = journal[1]
            second = resume_plan(**args)
            self.assertTrue(second["complete"])
            self.assertEqual(second["new_windows"], 1)
            self.assertEqual(second["plan"]["frame_count"], "8")
            self.assertEqual(second["plan"]["frame_clock_sha256"],
                             pin("".join(str(i)+"\n" for i in range(0,80,10)).encode()))
            self.assertEqual(journal[1], original)
            self.assertEqual(self.seen, ["%+#6","0.030000%+#6","0.030000%"])
            third = resume_plan(**args)
            self.assertTrue(third["complete"])
            self.assertEqual(third["new_windows"],0)
            self.assertEqual(len(self.seen),3)

    def test_seek_reopens_exact_packet_anchor_and_overlap_instead_of_trusting_time(self):
        anchor = dict(pts="40",packet_pos="104")
        good = [dict(pts=p,key_frame=1,packet_pos=100+p//10,duration=10) for p in range(40,80,10)]
        for changed in ("missing","position","overlap"):
            def process(argv, consume, **bounds):
                frames=copy.deepcopy(good)
                if changed=="missing":frames=frames[1:]
                if changed=="position":frames[0]["pkt_pos"]=999
                payload=[dict(pts=f["pts"],key_frame=f["key_frame"],
                              pkt_pos=f.get("pkt_pos",f["packet_pos"]),duration=f["duration"]) for f in frames]
                return consume(iter((json.dumps(dict(frames=payload,
                    streams=[dict(index=2,nb_read_packets="4")])).encode(),)))
            with self.subTest(changed=changed), patch(
                    "engine.dubflow.review.longform_probe._consume_process", side_effect=process):
                overlap=good[:2]
                if changed=="overlap":overlap=copy.deepcopy(overlap);overlap[1]["packet_pos"]=999
                with self.assertRaises(ReviewError):
                    probe_window(spec=self.spec,cursor=40,anchor=anchor,expected_overlap=overlap,**self.args)

    def test_packet_limit_never_grants_eof_without_matching_unbounded_suffix(self):
        def process(argv, consume, **bounds):
            data=self.process(argv,consume,**bounds)
            if argv[argv.index("-read_intervals")+1]=="0.040000%":
                data["frames"][-1]["duration"]=9
            return data
        with patch("engine.dubflow.review.longform_probe._consume_process",side_effect=process):
            with self.assertRaisesRegex(ReviewError,"EOF observation differs"):
                probe_window(spec=self.spec,cursor=40,anchor=dict(pts="40",packet_pos="104"),**self.args)

    def test_no_advancing_keyframes_missing_duration_or_wrong_end_refuse(self):
        for mode in ("keyframes","duration","end"):
            def process(argv,consume,**bounds):
                points=range(0,60,10) if mode=="keyframes" else range(40,80,10)
                frames=[dict(pts=p,key_frame=0 if mode=="keyframes" else 1,
                    pkt_pos=str(100+p//10),duration=10) for p in points]
                if mode=="duration":
                    for f in frames:del f["duration"]
                if mode=="end":frames[-1]["duration"]=9
                return consume(iter((json.dumps(dict(frames=frames,streams=[
                    dict(index=2,nb_read_packets=str(len(frames)))])).encode(),)))
            with self.subTest(mode=mode),patch(
                    "engine.dubflow.review.longform_probe._consume_process",side_effect=process):
                with self.assertRaises(ReviewError):
                    probe_window(spec=self.spec,cursor=0 if mode=="keyframes" else 40,
                        anchor=None if mode=="keyframes" else dict(pts="40",packet_pos="104"),**self.args)

    def test_native_checkpoint_refusal_or_changed_saved_bytes_never_exposes_complete_plan(self):
        def refuse(n, raw):
            raise ReviewError("native quota refused checkpoint")
        args=dict(spec=self.spec,read_checkpoint=lambda n:None,checkpoint=refuse,fence=lambda:None,
            context_sha256=pin(b"context"),processing_recipe_sha256=pin(b"recipe"),target_ticks=25,
            visual_dependencies=lambda a,b:pin(b"visual"),
            probe_args={k:v for k,v in self.args.items() if k!="timeout_seconds"},timeout_seconds=5)
        with patch("engine.dubflow.review.longform_probe._consume_process",side_effect=self.process):
            with self.assertRaisesRegex(ReviewError,"native quota refused"):
                resume_plan(**args)
            args["checkpoint"]=lambda n,raw:raw+b"\n"
            with self.assertRaisesRegex(ReviewError,"readback changed"):
                resume_plan(**args)

    def test_strict_observation_shape_packet_bounds_wide_integer_and_seek_compatibility(self):
        payload=dict(frames=[dict(pts=9007199254740995,key_frame=1,pkt_pos="123",duration=1)],
                     streams=[dict(index=2,nb_read_packets="1")])
        self.assertEqual(_observation(json.dumps(payload).encode(),2)["frames"][0]["pts"],9007199254740995)
        for path,value in (("pts",1.5),("key_frame",True),("pkt_pos","-1"),("duration",0)):
            changed=copy.deepcopy(payload);changed["frames"][0][path]=value
            with self.subTest(path=path),self.assertRaises(ReviewError):
                _observation(json.dumps(changed).encode(),2)
        with self.assertRaises(ReviewError):_observation(json.dumps(payload).encode(),1)
        self.assertEqual(_seek(dict(pts="-3",packet_pos="0"),(1,2)),"-1.500000")
        with self.assertRaises(ReviewError):
            _seek(dict(pts=str(2**63-1),packet_pos="0"),(1,1))

    def test_cancelled_or_late_native_fence_refuses_without_worker_start(self):
        args=dict(spec=self.spec,read_checkpoint=lambda n:None,checkpoint=lambda n,r:r,fence=lambda:None,
            context_sha256=pin(b"context"),processing_recipe_sha256=pin(b"recipe"),target_ticks=25,
            visual_dependencies=lambda a,b:pin(b"visual"),probe_args=self.args)
        with patch("engine.dubflow.review.longform_windows.probe_window") as not_started:
            with self.assertRaises(ReviewError):resume_plan(cancelled=lambda:True,**args)
            not_started.assert_not_called()

    def test_empty_writer_sections_are_supported_but_foreign_or_missing_sections_refuse(self):
        payload=dict(frames=[dict(pts=0,key_frame=1,pkt_pos="1",duration=10)],
                     programs=[],stream_groups=[],streams=[dict(index=2,nb_read_packets="1")])
        self.assertEqual(_observation(json.dumps(payload).encode(),2)["packets"],1)
        for name,value in (("programs",[{}]),("stream_groups",[{}]),("foreign",[])):
            changed=copy.deepcopy(payload);changed[name]=value
            with self.subTest(name=name),self.assertRaises(ReviewError):
                _observation(json.dumps(changed).encode(),2)
        del payload["streams"]
        with self.assertRaises(ReviewError):_observation(json.dumps(payload).encode(),2)

    def test_saved_prefix_overlap_and_planning_reopen_refuse_changed_records(self):
        journal={}
        args=dict(spec=self.spec,read_checkpoint=journal.get,
            checkpoint=lambda n,r:journal.setdefault(n,r),fence=lambda:None,
            context_sha256=pin(b"context"),processing_recipe_sha256=pin(b"recipe"),target_ticks=25,
            visual_dependencies=lambda a,b:pin(b"visual"),
            probe_args={k:v for k,v in self.args.items() if k!="timeout_seconds"},timeout_seconds=5)
        with patch("engine.dubflow.review.longform_probe._consume_process",side_effect=self.process):
            resume_plan(**args);resume_plan(**args)
            saved=journal[1]
            v=json.loads(saved);v["overlap"][1]["pts"]=51
            journal[1]=encode_plan(v)
            with self.assertRaisesRegex(ReviewError,"overlap"):
                resume_plan(**args)
            journal[1]=saved
            calls={}
            def changed(n):
                calls[n]=calls.get(n,0)+1
                raw=journal.get(n)
                return raw+b"\n" if raw is not None and calls[n]>1 else raw
            args["read_checkpoint"]=changed
            with self.assertRaisesRegex(ReviewError,"changed before planning"):
                resume_plan(**args)

    @unittest.skipUnless(os.environ.get("DUBFLOW_REVIEW_TEST_MEDIA_ROOT"),"explicit owned media fixture not selected")
    def test_actual_vfr_ffv1_source_resumes_windows_without_missing_or_duplicate_pts(self):
        media=Path(os.environ["DUBFLOW_REVIEW_TEST_MEDIA_ROOT"])
        ffmpeg,probe=media/"ffmpeg.exe",media/"ffprobe.exe"
        before={p:(pin(p.read_bytes()),p.stat().st_mtime_ns) for p in (ffmpeg,probe)}
        source=self.root/"new VFR windows [source].mkv"
        command=(str(ffmpeg),"-hide_banner","-loglevel","error","-nostdin","-f","lavfi","-i",
            "testsrc2=size=96x64:rate=20:duration=4","-vf",
            "select=not(eq(mod(n\\,5)\\,1)),setpts=PTS+2/TB","-fps_mode","passthrough",
            "-enc_time_base","1:1000","-c:v","ffv1","-g","4","-an","-y",str(source))
        made=subprocess.run(command,capture_output=True,timeout=20)
        self.assertEqual(made.returncode,0,made.stderr);self.assertEqual(made.stderr,b"")
        source_before=(pin(source.read_bytes()),source.stat().st_mtime_ns)
        spec=clock_spec(source_sha256=source_before[0],ffprobe_sha256=before[probe][0],
                        video_stream_index=0,time_base=(1,1000),start_tick=2000,end_tick=6000)
        journal={}
        args=dict(spec=spec,read_checkpoint=journal.get,checkpoint=lambda n,r:journal.setdefault(n,r),
            fence=lambda:None,context_sha256=pin(b"new exact windows context"),
            processing_recipe_sha256=pin(b"selected owned decoder fixture"),
            target_ticks=500,visual_dependencies=lambda a,b:pin(b"visual"),timeout_seconds=20,
            probe_args=dict(ffprobe_path=probe,media_root=media,
              executable_pin=dict(sha256=before[probe][0],size_bytes=str(probe.stat().st_size)),
              source_pin=dict(path=str(source),sha256=source_before[0],size_bytes=str(source.stat().st_size)),
              source_root=self.root,packet_limit=17))
        for unused in range(16):
            result=resume_plan(**args)
            if result["complete"]:break
        self.assertTrue(result["complete"])
        expected=[2000+i*50 for i in range(80) if i%5!=1]
        self.assertEqual(result["plan"]["frame_count"],str(len(expected)))
        self.assertEqual(result["plan"]["frame_clock_sha256"],pin("".join(str(p)+"\n" for p in expected).encode()))
        self.assertGreater(len(journal),1)
        self.assertEqual((pin(source.read_bytes()),source.stat().st_mtime_ns),source_before)
        for p,value in before.items():self.assertEqual((pin(p.read_bytes()),p.stat().st_mtime_ns),value)

    @unittest.skipUnless(os.environ.get("DUBFLOW_REVIEW_TEST_MEDIA_ROOT"),"explicit owned media fixture not selected")
    def test_actual_reordered_b_frames_match_full_decoded_pts_after_window_seeks(self):
        media=Path(os.environ["DUBFLOW_REVIEW_TEST_MEDIA_ROOT"])
        ffmpeg,probe=media/"ffmpeg.exe",media/"ffprobe.exe"
        before={p:(pin(p.read_bytes()),p.stat().st_mtime_ns) for p in (ffmpeg,probe)}
        source=self.root/"new reordered window source.mkv"
        made=subprocess.run((str(ffmpeg),"-hide_banner","-loglevel","error","-nostdin",
            "-f","lavfi","-i","testsrc2=size=96x64:rate=20:duration=4","-vf",
            "setpts=PTS+2/TB","-fps_mode","passthrough","-enc_time_base","1:1000",
            "-c:v","mpeg4","-bf","2","-g","8","-q:v","4","-an",str(source)),
            capture_output=True,timeout=20)
        self.assertEqual(made.returncode,0,made.stderr);self.assertEqual(made.stderr,b"")
        source_before=(pin(source.read_bytes()),source.stat().st_mtime_ns)
        full=subprocess.run((str(probe),"-v","error","-select_streams","0","-show_frames",
            "-show_streams","-show_entries","frame=pts,pict_type:stream=has_b_frames",
            "-of","json",str(source)),capture_output=True,timeout=20)
        self.assertEqual(full.returncode,0,full.stderr)
        decoded=json.loads(full.stdout)
        self.assertGreater(decoded["streams"][0]["has_b_frames"],0)
        self.assertIn("B",[v["pict_type"]for v in decoded["frames"]])
        expected=[v["pts"]for v in decoded["frames"]]
        journal={}
        spec=clock_spec(source_sha256=source_before[0],ffprobe_sha256=before[probe][0],
            video_stream_index=0,time_base=(1,1000),start_tick=2000,end_tick=6000)
        args=dict(spec=spec,read_checkpoint=journal.get,checkpoint=lambda n,r:journal.setdefault(n,r),
            fence=lambda:None,context_sha256=pin(b"new B-frame context"),
            processing_recipe_sha256=pin(b"owned B-frame decoder fixture"),
            target_ticks=500,visual_dependencies=lambda a,b:pin(b"visual"),timeout_seconds=20,
            probe_args=dict(ffprobe_path=probe,media_root=media,
              executable_pin=dict(sha256=before[probe][0],size_bytes=str(probe.stat().st_size)),
              source_pin=dict(path=str(source),sha256=source_before[0],size_bytes=str(source.stat().st_size)),
              source_root=self.root,packet_limit=25))
        for unused in range(16):
            result=resume_plan(**args)
            if result["complete"]:break
        self.assertTrue(result["complete"]);self.assertGreater(len(journal),1)
        self.assertEqual(result["plan"]["frame_count"],str(len(expected)))
        self.assertEqual(result["plan"]["frame_clock_sha256"],pin("".join(str(p)+"\n"for p in expected).encode()))
        self.assertEqual((pin(source.read_bytes()),source.stat().st_mtime_ns),source_before)
        for p,value in before.items():self.assertEqual((pin(p.read_bytes()),p.stat().st_mtime_ns),value)


if __name__=="__main__":unittest.main()

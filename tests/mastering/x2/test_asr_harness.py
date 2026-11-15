"""Offline tests for probe_source.py and asr_cues.py ($0: no network, no ASR runtime, no ffprobe needed).

The transcriber and ffprobe are replaced by fakes. Their outputs are test inputs only, never dataset entries.
"""
import copy, json, os, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import asr_cues  # noqa: E402
import probe_source  # noqa: E402
import validate_manifest as vm  # noqa: E402

TEMPLATE = os.path.join(HERE, "..", "..", "..", "docs", "mastering", "x2", "dataset-manifest.template.json")


def seg(start, end, text="x"):
    return SimpleNamespace(start=start, end=end, text=text)


def fake_probe(duration_ms):
    return lambda path: {"sha256": probe_source.sha256_file(path), "duration_ms": duration_ms}


VERSIONS = lambda: {"faster-whisper": "test", "ctranslate2": "test"}


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.media = Path(self.base, "src", "d-s1.mp4")
        self.media.parent.mkdir()
        self.media.write_bytes(b"test media bytes")

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, transcriber, duration_ms=60000):
        return asr_cues.run(str(self.media), source_id="d-s1", language="en", model_dir="/models/faster-whisper-small",
                            base=self.base, cues_rel="cues/d-s1.json", transcriber=transcriber,
                            probe=fake_probe(duration_ms), versions=VERSIONS)

    def cue_file(self):
        return json.loads(Path(self.base, "cues", "d-s1.json").read_text(encoding="utf-8"))

    def test_speech_gives_ok_cues_and_a_computed_ratio(self):
        patch = self.go(lambda m: [seg(0.0, 10.0), seg(5.0, 15.0), seg(30.0, 36.0)])
        self.assertEqual((patch["asr_status"], patch["cue_count"], patch["cue_rejected_count"]), ("ok", 3, 0))
        self.assertEqual(patch["speech_ratio"], 0.35)
        self.assertEqual(patch["cues_method"], "local-asr")
        self.assertEqual(patch["path"], "src/d-s1.mp4")
        doc = self.cue_file()
        self.assertEqual(doc["params"]["local_files_only"], True)
        self.assertEqual(doc["media_sha256"], patch["sha256"])

    def test_no_segments_is_ok_with_zero_cues_and_an_empty_hashed_file(self):
        patch = self.go(lambda m: [])
        self.assertEqual((patch["asr_status"], patch["cue_count"], patch["speech_ratio"]), ("ok", 0, 0.0))
        self.assertEqual(self.cue_file()["cues"], [])
        self.assertEqual(len(patch["cues_sha256"]), 64)
        self.assertIn("no speech detected", self.cue_file()["speech_ratio_note"])

    def test_an_asr_exception_is_failed_never_no_speech(self):
        def boom(media):
            raise RuntimeError("model.bin missing")
        patch = self.go(boom)
        self.assertEqual(patch["asr_status"], "failed")
        self.assertIsNone(patch["cue_count"])
        self.assertIsNone(patch["speech_ratio"])
        doc = self.cue_file()
        self.assertNotIn("cues", doc)
        self.assertIn("model.bin missing", doc["error"])

    def test_an_exception_while_iterating_segments_is_also_failed(self):
        def lazy(media):
            yield seg(0.0, 1.0)
            raise MemoryError("out of memory")
        self.assertEqual(self.go(lazy)["asr_status"], "failed")

    def test_impossible_cues_are_kept_as_rejected_not_deleted_or_clamped(self):
        patch = self.go(lambda m: [seg(1.0, 2.0), seg(3.0, 3.0), seg(59.5, 60.4)], duration_ms=60000)
        doc = self.cue_file()
        self.assertEqual([c["reason"] for c in doc["rejected"]], ["invalid_span", "after_source_end"])
        self.assertEqual(doc["rejected"][1]["end_ms"], 60400)  # copied, not clamped
        self.assertEqual((patch["cue_count"], patch["cue_rejected_count"]), (1, 2))

    def test_a_short_real_cue_in_a_long_source_keeps_a_nonzero_ratio(self):
        patch = self.go(lambda m: [seg(5.0, 5.1)], duration_ms=4 * 3600 * 1000)
        self.assertGreater(patch["speech_ratio"], 0)

    def test_media_outside_the_base_is_refused(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other, "x.mp4")
            outside.write_bytes(b"x")
            with self.assertRaises(ValueError):
                asr_cues.run(str(outside), source_id="d-s1", language="en", model_dir="m", base=self.base,
                             cues_rel="c.json", transcriber=lambda m: [], probe=fake_probe(1000), versions=VERSIONS)

    def test_the_patch_passes_verify_files_for_the_technical_fields(self):
        patches = {}
        for sid, segments in (("d-s1", [seg(0.0, 36.0)]), ("d-s2", [])):
            media = Path(self.base, "src", f"{sid}.mp4")
            media.write_bytes(f"bytes {sid}".encode())
            patches[sid] = asr_cues.run(str(media), source_id=sid, language="en", model_dir="m/faster-whisper-small",
                                        base=self.base, cues_rel=f"cues/{sid}.json", transcriber=lambda m, s=segments: s,
                                        probe=fake_probe(60000), versions=VERSIONS)
        m = json.loads(Path(TEMPLATE).read_text(encoding="utf-8"))
        for s in m["splits"]["REAL-DEV"]["sources"]:
            s.update(patches[s["source_id"]])
        found = vm.problems(m, "draft", "REAL-DEV", self.base)
        # Only the rights fields (which the harness must never fill) remain.
        self.assertTrue(found)
        self.assertTrue(all("rights" in p or "API not cleared" in p for p in found), found)


class ApplyPatchRefusesRights(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name, "m.json")
        self.path.write_text(Path(TEMPLATE).read_text(encoding="utf-8"), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_rights_field_is_refused(self):
        for field in sorted(asr_cues.RIGHTS_FIELDS):
            with self.subTest(field=field), self.assertRaises(ValueError):
                asr_cues.apply_patch(self.path, "REAL-DEV", "d-s1", {"sha256": "a" * 64, field: True})
        before = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsNone(before["splits"]["REAL-DEV"]["sources"][0]["rights_confirmed_by"])

    def test_technical_fields_are_written_and_rights_stay_null(self):
        source = asr_cues.apply_patch(self.path, "REAL-DEV", "d-s1", {"sha256": "a" * 64, "asr_status": "ok"})
        self.assertEqual(source["sha256"], "a" * 64)
        for field in ("rights_confirmed_by", "rights_confirmed_at", "third_party_processing_allowed"):
            self.assertIsNone(source[field])

    def test_unknown_fields_are_refused(self):
        with self.assertRaises(ValueError):
            asr_cues.apply_patch(self.path, "REAL-DEV", "d-s1", {"synthetic": True})


class ProbeSource(unittest.TestCase):
    def fake_run(self, payload, code=0):
        return lambda *a, **k: SimpleNamespace(returncode=code, stdout=json.dumps(payload), stderr="bad file")

    def test_duration_and_codecs_come_from_ffprobe_and_the_hash_from_the_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "v.mp4")
            f.write_bytes(b"abc")
            info = {"format": {"duration": "248.0466", "size": "3", "format_name": "mov,mp4"},
                    "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                                {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}]}
            out = probe_source.probe(f, run=self.fake_run(info))
            self.assertEqual(out["duration_ms"], 248047)
            self.assertEqual(out["sha256"], "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
            self.assertEqual((out["video_codec"], out["width"], out["audio_sample_rate"], out["has_audio"]), ("h264", 1920, 48000, True))

    def test_an_ffprobe_failure_is_raised_not_guessed(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "v.mp4")
            f.write_bytes(b"abc")
            with self.assertRaises(RuntimeError):
                probe_source.probe(f, run=self.fake_run({}, code=1))
            with self.assertRaises(RuntimeError):
                probe_source.probe(f, run=self.fake_run({"format": {}, "streams": []}))


if __name__ == "__main__":
    unittest.main()

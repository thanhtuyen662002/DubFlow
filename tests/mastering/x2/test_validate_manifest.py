"""Regression tests for validate_manifest.py ($0, offline). Run: python -B -m unittest tests.mastering.x2.test_validate_manifest

Fixtures here are test inputs for the validator only. They are not dataset entries and never go into a manifest.
"""
import copy, hashlib, json, os, sys, tempfile, unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import validate_manifest as vm  # noqa: E402

TEMPLATE = os.path.join(HERE, "..", "..", "..", "docs", "mastering", "x2", "dataset-manifest.template.json")


def cleared(source_id, **extra):
    """A source with every draft field filled (test values only)."""
    s = {"source_id": source_id, "path": f"/test/{source_id}.mp4", "sha256": "a" * 63 + source_id[-1],
         "duration_ms": 60000, "language": "en", "genre": "documentary", "speech_ratio": 0.6,
         "license": "test-licence", "license_evidence": "test-evidence", "rights_confirmed_by": "test-person",
         "rights_confirmed_at": "2026-01-01", "third_party_processing_allowed": True,
         "third_party_processing_scope": "transcript_text_only: test value",
         "cues_sha256": "b" * 64, "cues_method": "local-asr", "asr_status": "ok", "cue_count": 12,
         "asr_model": "faster-whisper-small", "synthetic": False}
    s.update(extra)
    return s


def manifest(dev_sources, holdout_sources=()):
    m = json.loads(Path(TEMPLATE).read_text(encoding="utf-8"))
    m = copy.deepcopy(m)
    m["splits"]["REAL-DEV"]["sources"] = list(dev_sources)
    m["splits"]["REAL-HOLDOUT"]["sources"] = list(holdout_sources)
    return m


def dev_problems(m):
    return vm.problems(m, "draft", "REAL-DEV")


class NoSpeechSources(unittest.TestCase):
    def test_a_source_where_asr_ran_and_found_no_speech_is_valid(self):
        quiet = cleared("d-s2", language=None, speech_ratio=0.0, cue_count=0)
        self.assertEqual(dev_problems(manifest([cleared("d-s1"), quiet])), [])

    def test_a_failed_asr_run_is_not_a_no_speech_source(self):
        failed = cleared("d-s2", speech_ratio=0.0, cue_count=0, asr_status="failed")
        self.assertTrue(any("ASR" in p for p in dev_problems(manifest([cleared("d-s1"), failed]))))

    def test_an_unknown_asr_outcome_is_rejected(self):
        unknown = cleared("d-s2", speech_ratio=0.0, cue_count=0)
        del unknown["asr_status"]
        self.assertTrue(any("ASR" in p for p in dev_problems(manifest([cleared("d-s1"), unknown]))))

    def test_cue_count_must_be_recorded(self):
        missing = cleared("d-s2")
        del missing["cue_count"]
        self.assertTrue(any("cue_count" in p for p in dev_problems(manifest([cleared("d-s1"), missing]))))

    def test_no_cues_with_measured_speech_is_contradictory(self):
        odd = cleared("d-s2", speech_ratio=0.4, cue_count=0)
        self.assertTrue(any("speech" in p for p in dev_problems(manifest([cleared("d-s1"), odd]))))

    def test_cues_without_speech_is_contradictory(self):
        odd = cleared("d-s2", speech_ratio=0.0, cue_count=5)
        self.assertTrue(any("speech" in p for p in dev_problems(manifest([cleared("d-s1"), odd]))))

    def test_an_empty_cue_file_still_needs_its_hash_and_method(self):
        quiet = cleared("d-s2", speech_ratio=0.0, cue_count=0, cues_sha256=None)
        self.assertTrue(any("cue" in p for p in dev_problems(manifest([cleared("d-s1"), quiet]))))


class ProcessingScope(unittest.TestCase):
    def test_a_bare_true_without_a_named_scope_is_not_draft_ready(self):
        for scope in (None, "", "frames_ok", "everything"):
            with self.subTest(scope=scope):
                found = vm.draft("REAL-DEV", {"sources": [cleared("d-s1", third_party_processing_scope=scope), cleared("d-s2")]})
                self.assertTrue(any("third_party_processing_scope" in p for p in found), found)

    def test_transcript_text_only_is_a_known_scope(self):
        found = vm.draft("REAL-DEV", {"sources": [cleared("d-s1"), cleared("d-s2")]})
        self.assertFalse(any("scope" in p for p in found), found)


class DevDoesNotWaitForHoldout(unittest.TestCase):
    def test_dev_draft_readiness_needs_no_holdout_sources(self):
        self.assertEqual(dev_problems(manifest([cleared("d-s1"), cleared("d-s2")], holdout_sources=[])), [])

    def test_holdout_still_may_not_reuse_a_dev_source(self):
        m = manifest([cleared("d-s1"), cleared("d-s2")], holdout_sources=[cleared("d-s1")])
        self.assertTrue(any("share a source" in p for p in dev_problems(m)))


class ExistingRulesStillHold(unittest.TestCase):
    def test_synthetic_sources_never_count(self):
        self.assertTrue(any("synthetic" in p for p in dev_problems(manifest([cleared("d-s1"), cleared("d-s2", synthetic=True)]))))

    def test_rights_must_be_confirmed_by_a_person(self):
        self.assertTrue(any("rights" in p for p in dev_problems(manifest([cleared("d-s1"), cleared("d-s2", rights_confirmed_by=None)]))))

    def test_the_shipped_template_is_not_ready(self):
        m = json.loads(Path(TEMPLATE).read_text(encoding="utf-8"))
        self.assertTrue(dev_problems(m))
        self.assertEqual(vm.structure(m), [])


class CueFilesAreRecomputed(unittest.TestCase):
    """--verify-files: count, sha256 and speech_ratio come from the real cue file, never from typed values."""

    def write(self, directory, name, cues):
        raw = json.dumps({"asr_model": "faster-whisper-small", "params": {"vad_filter": True}, "cues": cues}).encode()
        Path(directory, name).write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def media(self, directory, source_id):
        """A stand-in media file (test bytes, not a video) so the media sha256 check has something real to hash."""
        raw = f"test media {source_id}".encode()
        Path(directory, f"{source_id}.mp4").write_bytes(raw)
        return {"path": f"{source_id}.mp4", "sha256": hashlib.sha256(raw).hexdigest()}

    def check(self, sources, directory):
        for s in sources:
            if s.get("path", "").startswith("/test/"):
                s.update(self.media(directory, s["source_id"]))
        return vm.problems(manifest(sources), "draft", "REAL-DEV", directory)

    def test_ratio_is_the_union_of_cue_intervals(self):
        cues = [{"start_ms": 0, "end_ms": 10000}, {"start_ms": 5000, "end_ms": 15000}, {"start_ms": 30000, "end_ms": 36000}]
        self.assertEqual(vm.speech_ratio(cues, 60000), 0.35)
        self.assertEqual(vm.speech_ratio([], 60000), 0.0)

    def test_matching_files_pass_including_an_empty_cue_file(self):
        with tempfile.TemporaryDirectory() as d:
            spoken = self.write(d, "s1.json", [{"start_ms": 0, "end_ms": 36000, "text": "x"}])
            quiet = self.write(d, "s2.json", [])
            sources = [cleared("d-s1", cues_path="s1.json", cues_sha256=spoken, cue_count=1, speech_ratio=0.6),
                       cleared("d-s2", cues_path="s2.json", cues_sha256=quiet, cue_count=0, speech_ratio=0.0)]
            self.assertEqual(self.check(sources, d), [])

    def test_a_ratio_typed_as_zero_over_real_cues_is_caught(self):
        with tempfile.TemporaryDirectory() as d:
            spoken = self.write(d, "s1.json", [{"start_ms": 0, "end_ms": 36000, "text": "x"}])
            quiet = self.write(d, "s2.json", [{"start_ms": 1000, "end_ms": 2000, "text": "ghost"}])
            sources = [cleared("d-s1", cues_path="s1.json", cues_sha256=spoken, cue_count=1, speech_ratio=0.6),
                       cleared("d-s2", cues_path="s2.json", cues_sha256=quiet, cue_count=0, speech_ratio=0.0)]
            found = self.check(sources, d)
            self.assertTrue(any("file has 1 cues" in p for p in found), found)
            self.assertTrue(any("cue file gives 0.0166" in p for p in found), found)

    def test_an_edited_cue_file_is_caught_by_its_hash(self):
        with tempfile.TemporaryDirectory() as d:
            spoken = self.write(d, "s1.json", [{"start_ms": 0, "end_ms": 36000, "text": "x"}])
            self.write(d, "s2.json", [])
            sources = [cleared("d-s1", cues_path="s1.json", cues_sha256=spoken, cue_count=1, speech_ratio=0.6),
                       cleared("d-s2", cues_path="s2.json", cues_sha256="0" * 64, cue_count=0, speech_ratio=0.0)]
            self.assertTrue(any("cues_sha256 does not match" in p for p in self.check(sources, d)))

    def test_a_missing_cue_file_is_not_silence(self):
        with tempfile.TemporaryDirectory() as d:
            sources = [cleared("d-s1", cues_path="none.json"), cleared("d-s2", cues_path="none2.json")]
            self.assertTrue(any("not found" in p for p in self.check(sources, d)))

    def test_ratio_must_be_between_zero_and_one(self):
        self.assertTrue(any("outside" in p for p in dev_problems(manifest([cleared("d-s1", speech_ratio=1.4), cleared("d-s2")]))))


class CuePrecisionAndBounds(unittest.TestCase):
    """Owner protocol 2026-11-15 §IV.C1/C4/D4: rounding never turns speech into silence; impossible cue times and a
    missing or different media file are reported, never repaired."""

    helper = CueFilesAreRecomputed()

    def test_a_short_real_cue_in_a_long_source_is_not_silence(self):
        four_hours = 4 * 3600 * 1000
        ratio = vm.speech_ratio([{"start_ms": 0, "end_ms": 100}], four_hours)
        self.assertGreater(ratio, 0)  # was round(…, 3) == 0.0 before the fix
        long_quiet = cleared("d-s2", duration_ms=four_hours, cue_count=1, speech_ratio=ratio)
        self.assertEqual(dev_problems(manifest([cleared("d-s1"), long_quiet])), [])

    def test_the_short_cue_source_also_passes_file_verification(self):
        four_hours = 4 * 3600 * 1000
        with tempfile.TemporaryDirectory() as d:
            spoken = self.helper.write(d, "s1.json", [{"start_ms": 0, "end_ms": 36000, "text": "x"}])
            short = self.helper.write(d, "s2.json", [{"start_ms": 5000, "end_ms": 5100, "text": "ok"}])
            sources = [cleared("d-s1", cues_path="s1.json", cues_sha256=spoken, cue_count=1, speech_ratio=0.6),
                       cleared("d-s2", cues_path="s2.json", cues_sha256=short, cue_count=1, duration_ms=four_hours,
                               speech_ratio=100 / four_hours)]
            self.assertEqual(self.helper.check(sources, d), [])

    def bounds(self, cue):
        with tempfile.TemporaryDirectory() as d:
            spoken = self.helper.write(d, "s1.json", [{"start_ms": 0, "end_ms": 36000, "text": "x"}])
            odd = self.helper.write(d, "s2.json", [cue])
            sources = [cleared("d-s1", cues_path="s1.json", cues_sha256=spoken, cue_count=1, speech_ratio=0.6),
                       cleared("d-s2", cues_path="s2.json", cues_sha256=odd, cue_count=1, speech_ratio=0.5)]
            return self.helper.check(sources, d)

    def test_a_cue_after_the_end_of_the_source_is_reported(self):
        self.assertTrue(any("after the source ends" in p for p in self.bounds({"start_ms": 59000, "end_ms": 61000})))

    def test_a_zero_length_cue_is_reported(self):
        self.assertTrue(any("invalid span" in p for p in self.bounds({"start_ms": 2000, "end_ms": 2000})))

    def test_a_negative_start_is_reported(self):
        self.assertTrue(any("invalid span" in p for p in self.bounds({"start_ms": -5, "end_ms": 2000})))

    def test_fractional_milliseconds_are_reported(self):
        self.assertTrue(any("whole milliseconds" in p for p in self.bounds({"start_ms": 0.5, "end_ms": 2000})))

    def test_a_missing_media_file_fails_even_when_everything_typed_matches(self):
        with tempfile.TemporaryDirectory() as d:
            quiet = self.helper.write(d, "s.json", [])
            sources = [cleared(sid, path=f"{sid}.mp4", cues_path="s.json", cues_sha256=quiet, cue_count=0, speech_ratio=0.0)
                       for sid in ("d-s1", "d-s2")]
            found = vm.problems(manifest(sources), "draft", "REAL-DEV", d)
            self.assertTrue(any("source file d-s1.mp4 not found" in p for p in found), found)

    def test_a_media_file_with_another_hash_fails(self):
        with tempfile.TemporaryDirectory() as d:
            quiet = self.helper.write(d, "s.json", [])
            sources = [cleared(sid, cues_path="s.json", cues_sha256=quiet, cue_count=0, speech_ratio=0.0) for sid in ("d-s1", "d-s2")]
            for s in sources:
                s.update(self.helper.media(d, s["source_id"]))
            Path(d, "d-s2.mp4").write_bytes(b"swapped")
            found = vm.problems(manifest(sources), "draft", "REAL-DEV", d)
            self.assertEqual([p for p in found if "sha256 does not match d-s2.mp4" in p], ["REAL-DEV d-s2: sha256 does not match d-s2.mp4"])


if __name__ == "__main__":
    unittest.main()

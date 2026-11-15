"""Regression tests for validate_manifest.py ($0, offline). Run: python -B -m unittest tests.mastering.x2.test_validate_manifest

Fixtures here are test inputs for the validator only. They are not dataset entries and never go into a manifest.
"""
import copy, hashlib, json, os, sys, tempfile, unittest

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
         "cues_sha256": "b" * 64, "cues_method": "local-asr", "asr_status": "ok", "cue_count": 12,
         "asr_model": "faster-whisper-small", "synthetic": False}
    s.update(extra)
    return s


def manifest(dev_sources, holdout_sources=()):
    m = json.load(open(TEMPLATE, encoding="utf-8"))
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
        m = json.load(open(TEMPLATE, encoding="utf-8"))
        self.assertTrue(dev_problems(m))
        self.assertEqual(vm.structure(m), [])


class CueFilesAreRecomputed(unittest.TestCase):
    """--verify-files: count, sha256 and speech_ratio come from the real cue file, never from typed values."""

    def write(self, directory, name, cues):
        raw = json.dumps({"asr_model": "faster-whisper-small", "params": {"vad_filter": True}, "cues": cues}).encode()
        open(os.path.join(directory, name), "wb").write(raw)
        return hashlib.sha256(raw).hexdigest()

    def check(self, sources, directory):
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
            self.assertTrue(any("cue file gives 0.017" in p for p in found), found)

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


if __name__ == "__main__":
    unittest.main()

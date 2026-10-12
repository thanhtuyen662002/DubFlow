"""Keep the native cross-language oracle bound to the actual Python producer."""
import hashlib
import json
from pathlib import Path
import unittest

from engine.dubflow.review.corrected_timeline import derive_corrected_timeline
from engine.dubflow.review.wire import read_review_snapshot


class NativeTimelineOracleTests(unittest.TestCase):
    def test_native_fixture_matches_current_python_producer_and_exact_voice_identity(self):
        repo = Path(__file__).resolve().parents[2]
        value = json.loads((Path(__file__).parent / "native_corrected_timeline_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(value["source_pin_encoding"], "LF-normalized UTF-8 source; not release fingerprints")
        for name, expected in value["source_sha256"].items():
            actual = (repo / name).read_bytes().replace(b"\r\n", b"\n")
            self.assertEqual(hashlib.sha256(actual).hexdigest(), expected, name)
        original = value["original_json"].encode("utf-8")
        before = read_review_snapshot(value["before"])
        self.assertGreater(before.revision, 2**53)
        self.assertEqual(before.time_base, ("1", "48000"))
        self.assertEqual(derive_corrected_timeline(original, before, read_review_snapshot(value["translated"])),
                         value["expected_translated_json"].encode("utf-8"))
        self.assertIs(derive_corrected_timeline(original, before, read_review_snapshot(value["voice"])), original)


if __name__ == "__main__":
    unittest.main()

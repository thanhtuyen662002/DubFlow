"""Offline tests for the 1a script-draft harness: mock transport only, no network, no money ($0).

Every source, cue and answer here is a test fixture; nothing is a dataset entry or a human label.
"""
import copy, json, os, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import api_scope  # noqa: E402
import asr_cues  # noqa: E402
import probe_source  # noqa: E402
import script_draft as sd  # noqa: E402
import validate_manifest as vm  # noqa: E402

TEMPLATE = os.path.join(HERE, "..", "..", "..", "docs", "mastering", "x2", "dataset-manifest.template.json")
SCOPE = "transcript_text_only: test fixture"


def word(start, end, text):
    return SimpleNamespace(start=start, end=end, word=text)


def seg(start, end, text):
    return SimpleNamespace(start=start, end=end, text=text, words=[word(start, end, text)])


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        m = json.loads(Path(TEMPLATE).read_text(encoding="utf-8"))
        for s in m["splits"]["REAL-DEV"]["sources"]:
            sid = s["source_id"]
            media = Path(self.base, sid, f"{sid}.mp4")
            media.parent.mkdir()
            media.write_bytes(f"fixture media {sid}".encode())
            segments = [seg(i * 2.0, i * 2.0 + 1.5, f"{sid} cue {i} says something") for i in range(12)]
            patch = asr_cues.run(str(media), source_id=sid, language="en", model_dir="m/faster-whisper-small",
                                 base=self.base, cues_rel=f"cues/{sid}.json", transcriber=lambda _m, g=segments: g,
                                 probe=lambda p: {"sha256": probe_source.sha256_file(p), "duration_ms": 60_000, "has_audio": True},
                                 versions=lambda: {})
            s.update(patch)
            s.update(license="test fixture", license_evidence="test fixture", rights_confirmed_by="Test",
                     rights_confirmed_at="2026-11-15T00:00:00Z", third_party_processing_allowed=True,
                     third_party_processing_scope=SCOPE)
        self.manifest = m
        self.out = Path(self.base, "out")
        self.assertEqual(vm.problems(m, "draft", "REAL-DEV", self.base), [])

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, transport, cap=3.0, calls=4):
        self.ledger = sd.Ledger(cap, calls, path=Path(self.base, "ledger.jsonl"))
        return sd.run(self.manifest, "REAL-DEV", self.base, self.out, transport, self.ledger, synthetic=True, approval=None)


class Requests(Fixture):
    def test_the_request_is_text_only_and_passes_the_transcript_scope(self):
        for source, cues in sd.load_inputs(self.manifest, "REAL-DEV", self.base):
            req = sd.build_request(source, cues, sd.QUOTA[source["source_id"]])
            self.assertEqual(api_scope.preflight(req, [source]), api_scope.TRANSCRIPT_TEXT_ONLY)
            body = json.dumps(req)
            for leaked in (source["path"], source["sha256"], source["cues_sha256"], "mp4"):
                self.assertNotIn(leaked, body)
            self.assertNotIn("tools", req)
            self.assertEqual(req["output_config"]["format"]["schema"], sd.SCHEMA)

    def test_a_changed_cue_file_stops_before_any_request(self):
        path = Path(self.base, self.manifest["splits"]["REAL-DEV"]["sources"][0]["cues_path"])
        path.write_bytes(path.read_bytes() + b" ")
        calls = []
        with self.assertRaises(sd.StopRun):
            self.go(lambda r: calls.append(r))
        self.assertEqual(calls, [])

    def test_a_split_that_is_not_draft_ready_stops(self):
        self.manifest["splits"]["REAL-DEV"]["sources"][1]["third_party_processing_allowed"] = None
        with self.assertRaises(sd.StopRun):
            self.go(sd.mock_transport())

    def test_a_source_whose_scope_is_unknown_is_blocked_before_spending(self):
        self.manifest["splits"]["REAL-DEV"]["sources"][0]["third_party_processing_scope"] = "frames_and_audio"
        calls = []
        with self.assertRaises((sd.StopRun, api_scope.ScopeError)):  # the draft gate already names it
            self.go(lambda r: calls.append(r))
        self.assertEqual(calls, [])


class DryRun(Fixture):
    def test_mock_run_writes_a_synthetic_unlocked_draft_with_cue_provenance(self):
        draft = self.go(sd.mock_transport())
        self.assertEqual((draft["status"], draft["method"], draft["writer_saw_footage"]), ("DRAFT_NOT_LOCKED", "ai-draft-with-cues", False))
        self.assertEqual(len(draft["sentences"]), sum(q[t] for q in sd.QUOTA.values() for t in sd.TYPES))
        self.assertTrue(all(s["synthetic"] and s["ai_generated"] and not s["hard_negative"] for s in draft["sentences"]))
        self.assertNotIn("none", {s["planned_type"] for s in draft["sentences"]})
        self.assertTrue(all(c.startswith(s["source_ids"][0] + "#") for s in draft["sentences"] for c in s["cue_ids"]))
        self.assertTrue(Path(self.out, "narration-draft.json").is_file())
        self.assertEqual(self.ledger.calls, 2)

    def test_mock_sentences_can_never_pass_the_eval_gate(self):
        draft = self.go(sd.mock_transport())
        m = copy.deepcopy(self.manifest)
        m["splits"]["REAL-DEV"]["sentences"] = draft["sentences"]
        self.assertIn("REAL-DEV: synthetic sentences", vm.problems(m, "eval", "REAL-DEV", self.base))


class Answers(Fixture):
    def answer(self, sid, bad_cue=False, kinds=None):
        kinds = kinds or sd.QUOTA[sid]
        items = []
        for kind in sd.TYPES:
            for j in range(kinds[kind]):
                items.append({"text": f"{kind} {j}", "planned_type": kind, "factual": kind == "direct",
                              "cues": [f"{sid}#99" if bad_cue and not items else f"{sid}#{j}"]})
        return {"sentences": items}

    def test_an_invented_cue_id_is_rejected_and_recorded_never_repaired(self):
        bad, good = self.answer("d-s1", bad_cue=True), self.answer("d-s1")
        draft = self.go(sd.mock_transport(answers=[bad, good, self.answer("d-s2")]))
        self.assertEqual(self.ledger.calls, 3)  # the bad answer drops a sentence, so its counts fail: one more ask
        self.assertTrue(any("invalid answer" in n for n in draft["notes"]))
        self.assertNotIn("d-s1#99", {c for s in draft["sentences"] for c in s["cue_ids"]})

    def test_a_cue_of_the_other_source_does_not_exist_for_this_one(self):
        ids = {"d-s1#0"}
        ans = {"sentences": [{"text": "x", "planned_type": "direct", "factual": True, "cues": ["d-s2#0"]}]}
        with self.assertRaises(ValueError):
            sd.validate_answer(ans, "d-s1", ids, {"direct": 1, "general": 0, "illustrative": 0, "min_factual": 0})

    def test_a_no_footage_sentence_type_is_refused(self):
        ans = {"sentences": [{"text": "x", "planned_type": "none", "factual": False, "cues": []}]}
        with self.assertRaises(ValueError):
            sd.validate_answer(ans, "d-s1", set(), {"direct": 0, "general": 0, "illustrative": 0, "min_factual": 0})

    def test_extra_fields_such_as_labels_are_refused(self):
        ans = {"sentences": [{"text": "x", "planned_type": "general", "factual": False, "cues": [], "label": "ok"}]}
        with self.assertRaises(ValueError):
            sd.validate_answer(ans, "d-s1", set(), {"direct": 0, "general": 1, "illustrative": 0, "min_factual": 0})

    def test_two_invalid_answers_stop_the_run(self):
        with self.assertRaises(sd.StopRun):
            self.go(sd.mock_transport(answers=["not json", {"sentences": []}]))
        self.assertEqual(self.ledger.calls, 2)

    def test_a_refusal_stops_the_run(self):
        def refuse(request):
            return {"model": request["model"], "stop_reason": "refusal", "content": [], "usage": {"input_tokens": 10, "output_tokens": 0}}
        with self.assertRaises(sd.StopRun):
            self.go(refuse)
        self.assertEqual(self.ledger.calls, 1)


class Budget(Fixture):
    def test_a_cap_below_the_worst_case_stops_before_the_first_call(self):
        calls = []
        with self.assertRaises(sd.BudgetError):
            self.go(lambda r: calls.append(r), cap=0.10)
        self.assertEqual(calls, [])

    def test_the_call_limit_holds(self):
        with self.assertRaises(sd.BudgetError):
            self.go(sd.mock_transport(), calls=1)
        self.assertEqual(self.ledger.calls, 1)

    def test_a_response_without_usage_is_charged_at_worst_and_stops(self):
        transport = sd.mock_transport()
        def no_usage(request):
            response = dict(transport(request))
            response.pop("usage")
            return response
        with self.assertRaises(sd.StopRun):
            self.go(no_usage)
        self.assertEqual(self.ledger.calls, 1)
        self.assertGreater(self.ledger.spent, 0.3)  # 16,000 output tokens at $20/M alone is $0.32
        row = json.loads(Path(self.base, "ledger.jsonl").read_text().splitlines()[0])
        self.assertFalse(row["measured"])

    def test_measured_usage_is_priced_and_logged_after_each_call(self):
        self.go(sd.mock_transport(usage={"input_tokens": 1_000_000, "output_tokens": 0}), cap=20.0)
        rows = [json.loads(line) for line in Path(self.base, "ledger.jsonl").read_text().splitlines()]
        self.assertEqual([r["cost_usd"] for r in rows], [4.0, 4.0])
        self.assertEqual(rows[-1]["spent_usd"], 8.0)

    def test_the_next_call_waits_on_spent_plus_worst_case(self):
        # first call costs $2.0 measured; the second one's worst case (> $0.32) no longer fits under $2.3
        with self.assertRaises(sd.BudgetError):
            self.go(sd.mock_transport(usage={"input_tokens": 500_000, "output_tokens": 0}), cap=2.3)
        self.assertEqual(self.ledger.calls, 1)

    def test_an_unpriced_model_is_refused(self):
        with self.assertRaises(sd.BudgetError):
            sd.Ledger(1.0, 1).worst({"model": "unknown", "max_tokens": 10, "messages": []})

    def test_a_zero_cap_is_refused(self):
        with self.assertRaises(sd.BudgetError):
            sd.Ledger(0, 1)


class Estimate(Fixture):
    def test_estimate_spends_nothing_and_bounds_the_worst_case(self):
        e = sd.estimate(self.manifest, "REAL-DEV", self.base)
        self.assertEqual(len(e["requests"]), 2)
        self.assertGreaterEqual(e["worst_all_attempts_usd"], e["worst_one_attempt_usd"] * sd.MAX_ATTEMPTS - 1e-9)
        self.assertGreater(e["worst_one_attempt_usd"], e["typical_usd"])


if __name__ == "__main__":
    unittest.main()

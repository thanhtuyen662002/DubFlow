import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from engine.dubflow.review.longform_plan import encode_plan, plan_chunks, plan_hash, read_plan
from engine.dubflow.review.production import ReviewError


def pin(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


class LongformPlanTests(unittest.TestCase):
    def plan(self, pts=(-100, -99, -93, -70, -69, -50), **changes):
        args = dict(context_sha256=pin("context"), source_sha256=pin("source"),
                    processing_recipe_sha256=pin("renderer+encoder+configuration"),
                    time_base=(1, 1000), start_tick=-100, end_tick=-40, target_ticks=10,
                    max_frames=3, visual_dependencies=lambda left, right: pin("unchanged visual input"))
        args.update(changes)
        return plan_chunks(iter(pts), **args)

    def read(self, plan, raw=None):
        raw = encode_plan(plan) if raw is None else raw
        return read_plan(raw, expected_hash=plan_hash(raw), **{
            k: plan[k] for k in ("context_sha256", "source_sha256", "processing_recipe_sha256")})

    def test_vfr_negative_origin_has_exact_once_half_open_frame_coverage(self):
        pts = (-100, -99, -93, -70, -69, -50)
        result = self.plan(pts)
        self.assertEqual(self.read(result), result)
        self.assertEqual([(c["start_tick"], c["end_tick"]) for c in result["chunks"]],
                         [("-100", "-70"), ("-70", "-50"), ("-50", "-40")])
        recovered = []
        for chunk in result["chunks"]:
            actual = [p for p in pts if int(chunk["start_tick"]) <= p < int(chunk["end_tick"])]
            recovered.extend(actual)
            self.assertEqual(int(chunk["frame_count"]), len(actual))
            self.assertEqual(chunk["frame_clock_sha256"], pin("".join(f"{p}\n" for p in actual)))
        self.assertEqual(recovered, list(pts))
        self.assertEqual(result["frame_clock_sha256"], pin("".join(f"{p}\n" for p in pts)))

    def test_wide_nonzero_pts_and_reduced_rational_do_not_pass_through_float(self):
        origin = 2**53 + 17
        result = self.plan((origin, origin + 1, origin + 7, origin + 8), time_base=(1001, 30000),
                           start_tick=origin, end_tick=origin + 11, target_ticks=7)
        self.assertEqual(result["chunks"][0]["end_tick"], str(origin + 7))
        self.assertEqual(result["chunks"][1]["start_tick"], str(origin + 7))
        self.assertEqual(self.read(result), result)

    def test_clock_is_consumed_incrementally_and_frame_limit_bounds_dense_chunks(self):
        consumed, first_callback = 0, []

        def frames():
            nonlocal consumed
            for p in range(100_000):
                consumed += 1
                yield p

        def dependencies(left, right):
            if not first_callback:
                first_callback.append(consumed)
            return pin("visual")

        result = self.plan(frames(), start_tick=0, end_tick=100_000, target_ticks=1_000_000,
                           max_frames=1000, visual_dependencies=dependencies)
        self.assertEqual(first_callback, [1001])  # One lookahead; never list(frame_pts).
        self.assertEqual(len(result["chunks"]), 100)
        self.assertTrue(all(c["frame_count"] == "1000" for c in result["chunks"]))
        self.assertEqual(result["frame_count"], "100000")
        self.read(result)

    def test_new_request_or_one_changed_local_caption_preserves_unrelated_chunk_keys(self):
        original = self.plan()
        new_context = self.plan(context_sha256=pin("later current token"))
        self.assertNotEqual(plan_hash(encode_plan(original)), plan_hash(encode_plan(new_context)))
        self.assertEqual(original["chunks"], new_context["chunks"])
        changed = self.plan(visual_dependencies=lambda left, right:
                            pin("changed caption" if left <= -69 < right else "unchanged visual input"))
        self.assertEqual([a["identity"] != b["identity"] for a, b in zip(original["chunks"], changed["chunks"])],
                         [False, True, False])
        for key in ("source_sha256", "processing_recipe_sha256"):
            other = self.plan(**{key: pin("different admitted input")})
            self.assertTrue(all(a["identity"] != b["identity"] for a, b in zip(original["chunks"], other["chunks"])))

    def test_sparse_held_frame_is_never_split_or_replaced_by_fabricated_fps(self):
        result = self.plan((0, 601_000), start_tick=0, end_tick=1_000_000, target_ticks=300_000)
        self.assertEqual([(c["start_tick"], c["end_tick"], c["frame_count"]) for c in result["chunks"]],
                         [("0", "601000", "1"), ("601000", "1000000", "1")])

    def test_empty_duplicate_reversed_foreign_and_noninteger_clocks_refuse(self):
        for pts in ((), (-100, -100), (-100, -101), (-99,), (-101,), (-100, -40),
                    (-100, -99.0), (-100, True), (-(2**63) - 1,)):
            with self.subTest(pts=pts), self.assertRaises(ReviewError):
                self.plan(pts)
        for kwargs in ({"time_base": (2, 2000)}, {"time_base": (True, 1000)}, {"max_frames": True},
                       {"target_ticks": 0}, {"max_chunks": 4097}, {"timeout_seconds": 181}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ReviewError):
                self.plan(**kwargs)
        with self.assertRaisesRegex(ReviewError, "source duration"):
            self.plan((-(2**63),), start_tick=-(2**63), end_tick=2**63-1,
                      time_base=(1, 2**63-1))

    def test_six_hour_limit_uses_exact_rational_duration(self):
        result = self.plan((0,), time_base=(1, 1000), start_tick=0, end_tick=21_600_000)
        self.read(result)
        with self.assertRaises(ReviewError):
            self.plan((0,), time_base=(1, 1000), start_tick=0, end_tick=21_600_001)

    def test_chunk_count_and_expired_planning_deadline_refuse_partial_plan(self):
        with self.assertRaisesRegex(ReviewError, "chunk bound"):
            self.plan(range(100), start_tick=0, end_tick=100, target_ticks=1, max_chunks=2)
        with patch("engine.dubflow.review.longform_plan.time.monotonic", side_effect=(10.0, 41.0)):
            with self.assertRaisesRegex(ReviewError, "deadline"):
                self.plan()

    def test_pinned_reader_refuses_substituted_keys_coverage_counts_and_noncanonical_bytes(self):
        original = self.plan()
        mutations = (
            lambda p: p.update(schema_version=True),
            lambda p: p.update(extra="unrecognized"),
            lambda p: p.update(frame_count="7"),
            lambda p: p["source_interval"].update(start_tick="-0100"),
            lambda p: p["chunks"][0].update(identity=pin("foreign")),
            lambda p: p["chunks"][1].update(start_tick="-69"),
            lambda p: p["chunks"][0].update(frame_count="0"),
            lambda p: p["time_base"].update(numerator="2", denominator="2000"),
        )
        for change in mutations:
            value = copy.deepcopy(original)
            change(value)
            with self.subTest(value=value), self.assertRaises(ReviewError):
                self.read(value)
        with self.assertRaises(ReviewError):
            self.read(original, json.dumps(original, indent=2).encode())
        raw = encode_plan(original)
        with self.assertRaises(ReviewError):
            read_plan(raw, expected_hash=pin("not these bytes"), **{k: original[k] for k in
                      ("context_sha256", "source_sha256", "processing_recipe_sha256")})
        with self.assertRaises(ReviewError):
            read_plan(raw, expected_hash=plan_hash(raw), context_sha256=pin("stale context"),
                      source_sha256=original["source_sha256"], processing_recipe_sha256=original["processing_recipe_sha256"])

    def test_malformed_packets_never_become_a_trusted_clock_plan(self):
        plan = self.plan()
        for raw in (b'\xff', b'{"schema_version":1,"schema_version":1}', b'NaN',
                    b'{}' * (2 * 1024 * 1024 + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(ReviewError):
                read_plan(raw, expected_hash=plan_hash(raw), **{k: plan[k] for k in
                          ("context_sha256", "source_sha256", "processing_recipe_sha256")})


if __name__ == "__main__":
    unittest.main()

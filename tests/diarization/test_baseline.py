from __future__ import annotations

import unittest

from engine.dubflow.diarization import DiarizationBaseline, DiarizationMetrics, ResourceProfile, SpeakerObservation, SpeakerSegment


def obs(name: str, start: int, end: int, label: str | None, *, role: str = "speaker", vector: tuple[float, ...] | None = None, chunk: str = "chunk-0") -> SpeakerObservation:
    return SpeakerObservation(name, start, end, label, role, "0.9", vector, chunk)


class DiarizationBaselineTests(unittest.TestCase):
    def test_one_two_and_four_speaker_fixtures(self) -> None:
        baseline = DiarizationBaseline()
        for count in (1, 2, 4):
            observations = [obs(f"s{index}", index * 100, index * 100 + 50, f"local-{index}", vector=(1.0, float(index + 1))) for index in range(count)]
            result = baseline.merge_chunks("job-fixture", [observations])
            self.assertEqual(len({segment.speaker_cluster_ids[0] for segment in result.segments}), count)
            self.assertEqual(result.model_version, "baseline-1")

    def test_overlap_is_preserved_and_unresolved_roles_are_explicit(self) -> None:
        baseline = DiarizationBaseline()
        result = baseline.merge_chunks("job-overlap", [[
            obs("a", 0, 100, "a", vector=(1.0, 0.0)),
            obs("b", 50, 150, "b", vector=(0.0, 1.0)),
            obs("narrator", 20, 60, None, role="narrator"),
            obs("offscreen", 70, 90, None, role="offscreen"),
        ]])
        self.assertEqual(len(result.segments), 4)
        speaker_segments = [segment for segment in result.segments if segment.role == "speaker"]
        self.assertEqual(len({segment.overlap_group_id for segment in speaker_segments}), 1)
        self.assertIsNotNone(speaker_segments[0].overlap_group_id)
        self.assertEqual(next(segment for segment in result.segments if segment.role == "narrator").speaker_cluster_ids, ())
        self.assertEqual(next(segment for segment in result.segments if segment.role == "offscreen").speaker_cluster_ids, ())

    def test_cluster_ids_are_stable_when_chunk_order_changes(self) -> None:
        baseline = DiarizationBaseline()
        first = [obs("a0", 0, 50, "left", vector=(1.0, 0.0), chunk="a"), obs("b0", 50, 100, "right", vector=(0.0, 1.0), chunk="b")]
        second = [obs("a1", 100, 150, "left", vector=(1.0, 0.0), chunk="a"), obs("b1", 150, 200, "right", vector=(0.0, 1.0), chunk="b")]
        forward = baseline.merge_chunks("job-stable", [first, second])
        reverse = baseline.merge_chunks("job-stable", [second, first])
        self.assertEqual([segment.speaker_cluster_ids for segment in forward.segments], [segment.speaker_cluster_ids for segment in reverse.segments])

    def test_provenance_resource_profile_and_der_are_serializable(self) -> None:
        profile = ResourceProfile(200, 2, 15, 4096)
        result = DiarizationBaseline().merge_chunks("job-metrics", [[obs("one", 0, 100, "one", vector=(1.0, 0.0))]], resource_profile=profile)
        payload = result.to_dict()
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["resource_profile"]["peak_memory_bytes"], 4096)
        reference = result.segments
        wrong = (SpeakerSegment("wrong", 0, 100, ("spk-0000000000000000",), "speaker", "1"),)
        metrics = DiarizationMetrics.compute(reference, wrong)
        self.assertEqual(metrics.reference_ticks, 100)
        self.assertEqual(metrics.der, "2")
        self.assertEqual(metrics.to_dict()["confusion_ticks"], "100")

    def test_malformed_numeric_and_role_data_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            obs("bad", 0, 1, "speaker", vector=(float("nan"),))
        with self.assertRaises(ValueError):
            obs("bad", 0, 1, None, role="speaker")


if __name__ == "__main__":
    unittest.main()

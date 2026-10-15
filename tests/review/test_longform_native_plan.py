import hashlib
import json
from pathlib import Path
import unittest

from engine.dubflow.review.longform_plan import encode_plan, plan_chunks, plan_hash, read_plan


class NativeLongformPlanTests(unittest.TestCase):
    def test_native_fixture_matches_exact_python_source_clock_and_local_dependency_keys(self):
        fixture = json.loads(Path(__file__).with_name('native_longform_plan_v1.json').read_bytes())
        self.assertEqual(len(fixture['cases']), 2)
        for case in fixture['cases']:
            with self.subTest(name=case['name']):
                raw = case['plan_json'].encode()
                value = json.loads(raw)
                self.assertEqual(plan_hash(raw), case['plan_sha256'])
                self.assertEqual(read_plan(raw, expected_hash=case['plan_sha256'], **{
                    k:value[k] for k in ('context_sha256','source_sha256','processing_recipe_sha256')}), value)
                pts = [int(p) for p in case['source_pts']]
                expected = plan_chunks(iter(pts), **{k:value[k] for k in
                    ('context_sha256','source_sha256','processing_recipe_sha256')},
                    time_base=tuple(int(value['time_base'][k]) for k in ('numerator','denominator')),
                    start_tick=int(value['source_interval']['start_tick']), end_tick=int(value['source_interval']['end_tick']),
                    target_ticks=int(value['policy']['target_ticks']), max_frames=value['policy']['max_frames'],
                    visual_dependencies=lambda a,b: 'sha256:'+hashlib.sha256(b'native-fixture-visual').hexdigest())
                self.assertEqual(encode_plan(expected), raw)


if __name__ == '__main__':
    unittest.main()

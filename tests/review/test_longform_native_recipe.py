"""Independent Python oracle for native processing-release recipe derivation."""
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from engine.dubflow.review.longform_encode import recipe_identity
from engine.dubflow.review.longform_plan import encode_plan, plan_hash

SOURCES = ('longform_encode.py', 'longform_captions.py', 'longform_fragments.py',
           'longform_plan.py', 'longform_clock.py', 'longform_probe.py',
           'render_execution.py', 'synthesis_worker.py')
FIXTURE = Path(__file__).with_name('native_chunk_recipe_v1.json')


def digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def samples():
    result = []
    for burn, stream, clock, geometry in [(False, 0, (1, 1000), (96, 64)),
                                           (True, 3, (1001, 30000), (306, 544))]:
        sources = {name: digest(('independent source fixture:' + name).encode()) for name in SOURCES}
        ffmpeg = dict(sha256=digest(b'fixture ffmpeg, not executable'), size_bytes=4829696)
        ffprobe = dict(sha256=digest(b'fixture ffprobe, not executable'), size_bytes=4591232)
        environment = digest(b'fixture native codec/font/provider/OS closure; not an admission')
        case = dict(geometry=list(geometry), time_base=list(clock), video_stream_index=stream,
                    burn_in_subtitles=burn, runtime_environment_sha256=environment,
                    ffmpeg_inventory=ffmpeg, ffprobe_inventory=ffprobe, sources=sources)
        # Invoke the existing Python recipe function independently. Native must
        # reproduce these exact bytes, including decimal-string file sizes.
        encoded = []

        def capture(value):
            raw = encode_plan(value)
            encoded.append(raw)
            return raw

        def source_hash(path, deadline):
            if time.monotonic() >= deadline:
                raise AssertionError('test recipe deadline expired')
            return sources[Path(path).name], 123

        pins = [dict(sha256=p['sha256'], size_bytes=str(p['size_bytes'])) for p in (ffmpeg, ffprobe)]
        with patch('engine.dubflow.review.longform_encode._file_hash', side_effect=source_hash), \
             patch('engine.dubflow.review.longform_encode.encode_plan', side_effect=capture):
            identity = recipe_identity(ffmpeg_pin=pins[0], ffprobe_pin=pins[1], geometry=geometry,
                time_base=clock, burn_in_subtitles=burn, video_stream_index=stream,
                runtime_environment_sha256=environment, deadline=time.monotonic()+10)
        assert len(encoded) == 1 and plan_hash(encoded[0]) == identity
        result.append(dict(input=case, python_recipe_json=encoded[0].decode('utf-8'),
                           python_recipe_sha256=identity))
    return dict(schema_version=1, scope='cross-language recipe metadata only; no media/environment admission', cases=result)


class LongformNativeRecipeTests(unittest.TestCase):
    def test_native_oracle_matches_existing_python_recipe_for_burn_mode_and_source_clock(self):
        self.assertEqual(json.loads(FIXTURE.read_text(encoding='utf-8')), samples())

    def test_oracle_separates_inventory_integer_sizes_from_context_decimal_string_pins(self):
        for case in samples()['cases']:
            value = json.loads(case['python_recipe_json'])
            self.assertIs(type(case['input']['ffmpeg_inventory']['size_bytes']), int)
            self.assertIs(type(value['ffmpeg']['size_bytes']), str)
            self.assertEqual(set(value['sources']), set(SOURCES))
            changed = dict(value)
            changed['ffmpeg'] = case['input']['ffmpeg_inventory']
            self.assertNotEqual(plan_hash(encode_plan(changed)), case['python_recipe_sha256'])


if __name__ == '__main__':
    unittest.main()

"""Python source-stream oracle for native current chunk-plan binding."""
import json
from pathlib import Path
import unittest

from engine.dubflow.media.adapter import CANONICAL_TIME_BASE, MediaStream, Rational

FIXTURE = Path(__file__).with_name('native_chunk_source_v1.json')


def samples():
    result = []
    for start, duration, clock, stream, geometry in [
            (9007199254740993, 450001, (1, 90000), 2, (306, 544)),
            (-450000, 300, (1001, 30000), 7, (96, 64)),
            (0, 21600000, (1, 1000), 0, (1920, 1080))]:
        video = MediaStream(index=stream, codec_type='video', codec_name='h264',
            time_base=Rational(*clock), start_pts=start, duration_ts=duration,
            duration_ticks=duration, width=geometry[0], height=geometry[1],
            frame_rate=Rational(30000, 1001))
        audio = MediaStream(index=stream+1, codec_type='audio', codec_name='aac',
            time_base=Rational(1, 48000), start_pts=0, duration_ts=48000,
            duration_ticks=48000, sample_rate=48000, channels=2)
        exact = video.timeline()
        canonical = video.timeline(CANONICAL_TIME_BASE, start_rounding='floor', duration_rounding='ceil')
        result.append(dict(probe=dict(streams=[audio.to_dict(),video.to_dict()],
            canonical_time_base=CANONICAL_TIME_BASE.to_dict(),canonical_duration_ticks=canonical.duration_ticks,
            video_timeline=canonical.to_dict()),expected=dict(geometry=list(geometry),video_stream_index=stream,
            time_base={key:str(value) for key,value in exact.time_base.to_dict().items()},
            source_interval=dict(start_tick=str(exact.start_pts),end_tick=str(exact.end_pts)))))
    return dict(schema_version=1,scope='archived source-stream metadata oracle; not live source/decoded-frame admission',cases=result)


class LongformNativeSourceTests(unittest.TestCase):
    def test_native_source_oracle_uses_existing_python_exact_source_stream_timeline(self):
        self.assertEqual(json.loads(FIXTURE.read_text(encoding='utf-8')),samples())

    def test_source_ticks_stay_separate_from_rounded_ms_timeline_and_unrelated_audio(self):
        for case in samples()['cases'][:2]:
            expected, probe = case['expected'],case['probe']
            self.assertNotEqual(expected['time_base'],{key:str(value) for key,value in probe['canonical_time_base'].items()})
            self.assertNotEqual(expected['source_interval']['start_tick'],str(probe['video_timeline']['start_pts']))
            self.assertEqual(probe['streams'][1]['index'],expected['video_stream_index'])
            self.assertNotEqual(probe['streams'][0]['index'],expected['video_stream_index'])


if __name__ == '__main__':
    unittest.main()

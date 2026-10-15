import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from engine.dubflow.review.longform_clock import plan_decoded_clock
from engine.dubflow.review.longform_probe import _consume_process, probe_plan
from engine.dubflow.review.production import ReviewError


def pin(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


class LongformProbeTests(unittest.TestCase):
    def fixture(self, code, consume=None, *, timeout=3, max_bytes=16*1024*1024, cancelled=lambda: False):
        argv = (sys.executable, '-I', '-S', '-B', '-c', code)
        if consume is None:
            consume = lambda data: plan_decoded_clock(data, context_sha256=pin(b'context'),
                source_sha256=pin(b'source'), processing_recipe_sha256=pin(b'recipe'),
                time_base=(1, 1000), start_tick=-10, end_tick=30, target_ticks=10,
                visual_dependencies=lambda a, b: pin(b'local visual'))
        return _consume_process(argv, consume, deadline=time.monotonic()+timeout,
                                max_stdout_bytes=max_bytes, cancelled=cancelled)

    def test_actual_owned_python_fixture_streams_split_xml_and_reaps_readers(self):
        result = self.fixture("import sys;[sys.stdout.buffer.write(p) or None for p in []];"
            "s=b'<ffprobe><frames><frame pts=\"-10\"/><frame pts=\"0\"/><frame pts=\"20\"/></frames></ffprobe>';"
            "[sys.stdout.buffer.write(s[i:i+3]) for i in range(0,len(s),3)];sys.stdout.flush()")
        self.assertEqual(result['frame_count'], '3')
        self.assertEqual(result['chunks'][0]['start_tick'], '-10')
        self.assertFalse(any(t.name.startswith('review-clock-') for t in threading.enumerate()))

    def test_nonzero_exit_or_error_diagnostics_refuse_even_complete_xml(self):
        body = "import sys;sys.stdout.write('<ffprobe><frames><frame pts=\"-10\"/></frames></ffprobe>');"
        for tail in ("sys.exit(2)", "sys.stderr.write('decoder reported bad packet')"):
            with self.subTest(tail=tail), self.assertRaisesRegex(ReviewError, 'failed or emitted'):
                self.fixture(body+tail)

    def test_truncated_stdout_never_returns_a_plan(self):
        with self.assertRaises(ReviewError):
            self.fixture("print('<ffprobe><frames><frame pts=\"-10\"/>',flush=True)")

    def test_stdout_stderr_floods_refuse_and_reap_instead_of_deadlocking(self):
        for stream in ('stdout', 'stderr'):
            with self.subTest(stream=stream), self.assertRaises(ReviewError):
                self.fixture(f"import sys;sys.{stream}.buffer.write(b'x'*1000000);sys.{stream}.flush()",
                             consume=lambda data: sum(len(b) for b in data), max_bytes=4096)
        self.assertFalse(any(t.name.startswith('review-clock-') for t in threading.enumerate()))

    def test_timeout_and_cancellation_kill_reap_and_do_not_return_partial_data(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ReviewError, 'deadline'):
            self.fixture('import time;time.sleep(10)', timeout=0.15)
        self.assertLess(time.monotonic()-started, 3)
        cancel = threading.Event()
        timer = threading.Timer(0.15, cancel.set)
        timer.start()
        try:
            with self.assertRaisesRegex(ReviewError, 'cancelled'):
                self.fixture('import time;time.sleep(10)', cancelled=cancel.is_set)
        finally:
            timer.cancel()
            timer.join()
        self.assertFalse(any(t.name.startswith('review-clock-') for t in threading.enumerate()))

    def test_early_consumer_and_callback_failure_refuse_and_reap(self):
        with self.assertRaisesRegex(ReviewError, 'complete pipe'):
            self.fixture('import time;time.sleep(10)', consume=lambda blocks: 'untrusted early success')

        def refused(blocks):
            raise ReviewError('local input callback refused')

        with self.assertRaisesRegex(ReviewError, 'callback refused'):
            self.fixture('import time;time.sleep(10)', consume=refused)

    def test_source_and_executable_pins_are_checked_before_and_after_private_process(self):
        with tempfile.TemporaryDirectory(prefix='dubflow-clock-process-') as tmp:
            root = Path(tmp)
            source, exe = root/'source with spaces.mp4', root/'ffprobe.exe'
            source.write_bytes(b'fixture source; no actual media execution')
            exe.write_bytes(b'fixture executable; process boundary is mocked')
            args = dict(ffprobe_path=exe, media_root=root,
                executable_pin=dict(sha256=pin(exe.read_bytes()), size_bytes=str(exe.stat().st_size)),
                source_pin=dict(path=str(source), sha256=pin(source.read_bytes()), size_bytes=str(source.stat().st_size)),
                source_root=root, video_stream_index=2, context_sha256=pin(b'context'),
                processing_recipe_sha256=pin(b'recipe'), time_base=(1, 1000), start_tick=-10, end_tick=30,
                target_ticks=10, visual_dependencies=lambda a,b:pin(b'visual'))
            seen = []

            def process(argv, consume, **bounds):
                seen.append(argv)
                self.assertEqual(argv[argv.index('-select_streams')+1], '2')
                self.assertNotIn('-read_intervals', argv)
                return consume(iter((b'<ffprobe><frames><frame pts="-10"/><frame pts="0"/></frames></ffprobe>',)))

            with patch('engine.dubflow.review.longform_probe._consume_process', side_effect=process):
                self.assertEqual(probe_plan(**args)['frame_count'], '2')
            self.assertEqual(len(seen),1)

            def changed(argv, consume, **bounds):
                result = process(argv, consume, **bounds)
                source.write_bytes(b'changed')
                return result

            with patch('engine.dubflow.review.longform_probe._consume_process', side_effect=changed):
                with self.assertRaisesRegex(ReviewError, 'size or content changed'):
                    probe_plan(**args)
            with patch('engine.dubflow.review.longform_probe._consume_process') as not_started:
                with self.assertRaises(ReviewError):
                    probe_plan(**args)
                not_started.assert_not_called()

    @unittest.skipUnless(os.environ.get('DUBFLOW_REVIEW_TEST_MEDIA_ROOT'),
                         'explicit owned media QA fixture not selected')
    def test_actual_owned_ffprobe_preserves_vfr_source_pts_and_nonzero_origin(self):
        from engine.dubflow.media import MediaProbe
        media = Path(os.environ['DUBFLOW_REVIEW_TEST_MEDIA_ROOT'])
        ffmpeg, ffprobe = media/'ffmpeg.exe', media/'ffprobe.exe'
        self.assertTrue(media.is_absolute() and ffmpeg.is_file() and ffprobe.is_file())
        before = {p: (pin(p.read_bytes()), p.stat().st_mtime_ns) for p in (ffmpeg, ffprobe)}
        with tempfile.TemporaryDirectory(prefix='dubflow-decoded-vfr-clock-') as tmp:
            root = Path(tmp)
            source = root/'VFR source [spaces].mkv'
            generated = subprocess.run((str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=20:duration=2',
                '-vf', 'select=not(eq(mod(n\\,5)\\,1)),setpts=PTS+2/TB',
                '-fps_mode', 'passthrough', '-enc_time_base', '1:1000', '-c:v', 'ffv1',
                '-an', '-y', str(source)), capture_output=True, timeout=20)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertEqual(generated.stderr, b'')
            metadata = MediaProbe(ffprobe, trusted_root=media, timeout_seconds=20).probe(source)
            self.assertEqual(metadata.video.start_pts, 2000)
            self.assertEqual(metadata.video.time_base.to_dict(), dict(numerator=1, denominator=1000))
            source_before = (pin(source.read_bytes()), source.stat().st_mtime_ns)
            seen = []
            result = probe_plan(ffprobe_path=ffprobe, media_root=media,
                executable_pin=dict(sha256=before[ffprobe][0], size_bytes=str(ffprobe.stat().st_size)),
                source_pin=dict(path=str(source), sha256=source_before[0], size_bytes=str(source.stat().st_size)),
                source_root=root, video_stream_index=metadata.video.index,
                context_sha256=pin(b'new VFR fixture context'), processing_recipe_sha256=pin(b'exact fixture recipe'),
                time_base=(1, 1000), start_tick=2000, end_tick=4000, target_ticks=500,
                visual_dependencies=lambda a,b: seen.append((a,b)) or pin(b'fixture visual'), timeout_seconds=20)
            expected = [2000+n*50 for n in range(40) if n%5 != 1]
            self.assertEqual(result['frame_count'], str(len(expected)))
            self.assertEqual(result['frame_clock_sha256'], pin(''.join(f'{p}\n' for p in expected).encode()))
            self.assertEqual(seen, [(2000,2500), (2500,3000), (3000,3500), (3500,4000)])
            self.assertEqual((pin(source.read_bytes()), source.stat().st_mtime_ns), source_before)
        for p, expected in before.items():
            self.assertEqual((pin(p.read_bytes()), p.stat().st_mtime_ns), expected)


if __name__ == '__main__':
    unittest.main()

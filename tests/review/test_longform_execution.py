import copy
import io
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from engine.dubflow.review.longform_execution import MAX_CONTEXT, _metadata, admit_context, execute_context
from engine.dubflow.review.longform_plan import encode_plan, plan_chunks
from engine.dubflow.review.production import ReviewError
from engine.dubflow.review.synthesis_input import _digest, _encode


def file_pin(path):
    return dict(path=str(path), sha256=_digest(path.read_bytes()), size_bytes=str(path.stat().st_size))


def acknowledge(request):
    return dict(schema_version=1, kind='review_chunk_native_ack', binding=request['binding'],
        sequence=request['sequence'], request_sha256=_digest(_encode(request, 65536)), accepted=True,
        runtime_environment_sha256=request['binding']['runtime_environment_sha256'])


class ParentInput:
    def __init__(self, output, mutate=lambda value: value):
        self.output, self.mutate = output, mutate

    def readline(self, limit):
        request = json.loads(self.output.getvalue().splitlines()[-1])
        return _encode(self.mutate(acknowledge(request)), 65536) + b'\n'


class LongformExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dubflow-chunk-execution-context-')
        self.root = Path(self.tmp.name)
        self.output = self.root / 'private output'; self.output.mkdir()
        self.media = self.root / 'media'; self.media.mkdir()
        self.source = self.root / 'source with spaces.mp4'; self.source.write_bytes(b'not media; mocked encoder only')
        self.recipe = _digest(b'fixture recipe; not actual source/OS environment admission')
        self.origin = 9007199254740993
        plan = plan_chunks(iter((self.origin, self.origin + 50)), context_sha256=_digest(b'current context'),
            source_sha256=file_pin(self.source)['sha256'], processing_recipe_sha256=self.recipe,
            time_base=(1, 1000), start_tick=self.origin, end_tick=self.origin + 100, target_ticks=100,
            visual_dependencies=lambda left, right: _digest(b'no caption fixture'))
        self.plan = self.root / 'plan.json'; self.plan.write_bytes(encode_plan(plan)); self.plan.chmod(stat.S_IREAD)
        self.ffmpeg = self.media / 'ffmpeg.exe'; self.ffprobe = self.media / 'ffprobe.exe'
        self.ffmpeg.write_bytes(b'fixture, never executed'); self.ffprobe.write_bytes(b'fixture, never executed')
        self.context = dict(schema_version=1, kind='review_native_chunk_execution_context',
            binding=dict(context_sha256=plan['context_sha256'], plan_sha256=file_pin(self.plan)['sha256'],
                chunk_identity=plan['chunks'][0]['identity'], runtime_environment_sha256=_digest(b'native callback fixture')),
            processing_recipe_sha256=self.recipe, plan=file_pin(self.plan), plan_root=str(self.root),
            source=file_pin(self.source), source_root=str(self.root),
            executables=dict(ffmpeg=file_pin(self.ffmpeg), ffprobe=file_pin(self.ffprobe)),
            media_root=str(self.media), output_root=str(self.output), geometry=[96, 64], video_stream_index=0,
            burn_in_subtitles=False, captions=None, max_chunk_bytes='65536', timeout_seconds=5)

    def tearDown(self):
        for path in self.root.rglob('*'):
            if path.is_file():
                path.chmod(stat.S_IREAD | stat.S_IWRITE)
        self.tmp.cleanup()

    def run_context(self, context=None, encoder=lambda *args, **kwargs: {'fixture': 'not media/QC evidence'},
                    mutate=lambda value: value, context_fence=lambda: None):
        raw = _encode(self.context if context is None else context, MAX_CONTEXT)
        output = io.BytesIO()
        with patch('engine.dubflow.review.longform_encode.encode_chunk', side_effect=encoder) as called:
            result = execute_context(raw, expected_hash=_digest(raw), native_input=ParentInput(output, mutate),
                native_output=output, context_fence=context_fence)
        return result, [json.loads(line) for line in output.getvalue().splitlines()], called.call_count

    def test_closed_context_refuses_native_hash_type_mode_bounds_and_foreign_fields_before_encode(self):
        raw = _encode(self.context, MAX_CONTEXT)
        with self.assertRaises(ReviewError):
            admit_context(raw, _digest(b'other context'))
        cases = [('schema_version', True), ('kind', 'unknown'), ('timeout_seconds', 901),
                 ('timeout_seconds', True), ('max_chunk_bytes', '268435457'), ('max_chunk_bytes', '01'),
                 ('burn_in_subtitles', 1), ('captions', {}), ('geometry', [True, 64]),
                 ('video_stream_index', -1), ('extra', 'not admitted')]
        for key, value in cases:
            context = copy.deepcopy(self.context); context[key] = value
            with self.subTest(key=key), patch('engine.dubflow.review.longform_encode.encode_chunk') as encoder:
                with self.assertRaises(ReviewError):
                    candidate = _encode(context, MAX_CONTEXT)
                    admit_context(candidate, _digest(candidate))
                encoder.assert_not_called()
        padded = raw + b' '
        with self.assertRaises(ReviewError):
            admit_context(padded, _digest(padded))

    def test_worker_routes_actual_encoder_callbacks_and_keeps_native_plan_wide_integer_bytes(self):
        before = file_pin(self.plan), self.plan.stat().st_mtime_ns
        def encode(plan, **arguments):
            self.assertEqual(plan, self.plan.read_bytes())
            self.assertIn(str(self.origin).encode(), plan)
            self.assertEqual(arguments['geometry'], (96, 64))
            self.assertEqual(arguments['ffmpeg_path'], str(self.ffmpeg))
            self.assertEqual(arguments['runtime_environment_fence'](), self.context['binding']['runtime_environment_sha256'])
            arguments['capacity'](12345)
            self.assertFalse(arguments['cancelled']())
            return {'wide': self.origin, 'fixture': 'mock encoder, no media admission'}
        result, packets, calls = self.run_context(encoder=encode)
        self.assertEqual(calls, 1)
        self.assertEqual([value.get('operation') for value in packets], ['fence', 'capacity', 'fence', 'capacity', 'fence', 'capacity', None])
        self.assertEqual(json.loads(packets[-1]['result_json']), result)
        self.assertEqual([item['sequence'] for item in packets], list(range(1, 8)))
        self.assertEqual((file_pin(self.plan), self.plan.stat().st_mtime_ns), before)

    def test_foreign_plan_or_local_identity_and_mutable_or_multiply_linked_metadata_refuse_before_encoder(self):
        for mode in ('plan', 'chunk', 'source', 'writable', 'hardlink'):
            context = copy.deepcopy(self.context)
            link = self.root / 'linked plan'
            if mode == 'plan': context['binding']['plan_sha256'] = _digest(b'other plan')
            if mode == 'chunk': context['binding']['chunk_identity'] = _digest(b'other chunk')
            if mode == 'source': context['source']['sha256'] = _digest(b'other source')
            if mode == 'writable': self.plan.chmod(stat.S_IREAD | stat.S_IWRITE)
            if mode == 'hardlink': os.link(self.plan, link)
            try:
                raw = _encode(context, MAX_CONTEXT)
                with self.subTest(mode=mode), patch('engine.dubflow.review.longform_encode.encode_chunk') as encoder:
                    with self.assertRaises(Exception):
                        execute_context(raw, expected_hash=_digest(raw), native_input=io.BytesIO(),
                            native_output=io.BytesIO(), context_fence=lambda: None)
                    encoder.assert_not_called()
            finally:
                if link.exists():
                    # Both names belong to this newly allocated fixture. On
                    # Windows the shared read-only attribute prevents unlink.
                    link.chmod(stat.S_IREAD | stat.S_IWRITE)
                    link.unlink()
                self.plan.chmod(stat.S_IREAD)

    def test_parent_environment_or_capacity_refusal_never_reaches_encoder_or_returns_final(self):
        for operation in ('fence', 'capacity'):
            output = io.BytesIO(); raw = _encode(self.context, MAX_CONTEXT)
            def mutate(value):
                request = json.loads(output.getvalue().splitlines()[-1])
                if request['operation'] == operation: value['accepted'] = False
                return value
            with self.subTest(operation=operation), patch('engine.dubflow.review.longform_encode.encode_chunk') as encoder:
                with self.assertRaises(ReviewError):
                    execute_context(raw, expected_hash=_digest(raw), native_input=ParentInput(output, mutate),
                        native_output=output, context_fence=lambda: None)
                encoder.assert_not_called()
            self.assertTrue(all(json.loads(line)['kind'] != 'review_chunk_worker_result' for line in output.getvalue().splitlines()))

    def test_post_encode_plan_or_context_change_or_encoder_failure_prevents_any_final_result(self):
        for mode in ('plan', 'same-bytes-replacement', 'context', 'encoder'):
            output = io.BytesIO(); raw = _encode(self.context, MAX_CONTEXT); observed = 0
            original = self.plan.read_bytes()
            def fence():
                nonlocal observed
                observed += 1
                if mode == 'context' and observed == 2: raise ReviewError('sealed context changed')
            def encode(*args, **kwargs):
                if mode == 'encoder': raise ReviewError('encoder refused')
                if mode == 'plan':
                    self.plan.chmod(stat.S_IREAD | stat.S_IWRITE); self.plan.write_bytes(b'changed'); self.plan.chmod(stat.S_IREAD)
                if mode == 'same-bytes-replacement':
                    replacement = self.root / 'replacement plan'
                    replacement.write_bytes(original); replacement.chmod(stat.S_IREAD)
                    self.plan.chmod(stat.S_IREAD | stat.S_IWRITE); self.plan.unlink()
                    replacement.rename(self.plan)
                return {'fixture': 'not media'}
            try:
                with self.subTest(mode=mode), patch('engine.dubflow.review.longform_encode.encode_chunk', side_effect=encode):
                    with self.assertRaises(ReviewError):
                        execute_context(raw, expected_hash=_digest(raw), native_input=ParentInput(output),
                            native_output=output, context_fence=fence)
                self.assertTrue(all(json.loads(line)['kind'] != 'review_chunk_worker_result' for line in output.getvalue().splitlines()))
            finally:
                self.plan.chmod(stat.S_IREAD | stat.S_IWRITE); self.plan.write_bytes(original); self.plan.chmod(stat.S_IREAD)

    def test_caption_inputs_are_exact_sealed_native_pins_and_rechecked_after_encode(self):
        captions = self.root / 'captions.ass'; captions.write_bytes(b'explicit ASS mock fixture'); captions.chmod(stat.S_IREAD)
        context = copy.deepcopy(self.context); context.update(burn_in_subtitles=True,
            captions=dict(file=file_pin(captions), root=str(self.root)))
        def encode(*args, **kwargs):
            self.assertEqual(kwargs['captions_ass'], captions.read_bytes())
            self.assertEqual(kwargs['captions_sha256'], context['captions']['file']['sha256'])
            captions.chmod(stat.S_IREAD | stat.S_IWRITE); captions.write_bytes(b'changed'); captions.chmod(stat.S_IREAD)
            return {'fixture': 'no actual caption render'}
        with self.assertRaises(ReviewError): self.run_context(context=context, encoder=encode)

    def test_actual_isolated_entry_requires_native_gate_and_emits_no_output_on_bad_context(self):
        entry = Path(__file__).resolve().parents[2] / 'engine/dubflow/review/longform_execution.py'
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        for gate in (b'', b'\x00', b'\x01'):
            result = subprocess.run([sys.executable, '-I', '-S', '-B', str(entry), '--context',
                str(self.root / 'missing context.json'), '--context-sha256', _digest(b'missing')],
                input=gate, capture_output=True, timeout=5, creationflags=flags)
            self.assertEqual(result.returncode, 2); self.assertEqual(result.stdout, b'')
            self.assertLessEqual(len(result.stderr), 1300)
            self.assertEqual(json.loads(result.stderr)['code'], 'REVIEW_CHUNK_EXECUTION_INVALID')

    def test_actual_isolated_worker_entry_exchanges_live_acks_and_one_final_with_mock_encoder(self):
        # Actual entry/isolation/pipe fixture. Encoder is intentionally mocked;
        # no media, native WorkerTree, current graph or product crash claim.
        root = Path(__file__).resolve().parents[2]
        context = self.root / 'sealed context.json'
        raw = _encode(self.context, MAX_CONTEXT); context.write_bytes(raw); context.chmod(stat.S_IREAD)
        code = ('import sys;sys.path.insert(0,sys.argv[1]);'
                'from engine.dubflow.review import longform_encode as e,longform_execution as x;'
                'e.encode_chunk=lambda raw,**kw:{"fixture":"mocked encoder", "wide":9007199254740993};'
                'raise SystemExit(x.main(sys.argv[2:]))')
        child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', code, str(root),
            '--context', str(context), '--context-sha256', _digest(raw)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        events = queue.Queue(maxsize=8)
        def reader():
            try:
                while True:
                    line = child.stdout.readline(65537); events.put(line, timeout=2)
                    if not line: return
            except Exception as error: events.put(error, timeout=2)
        thread = threading.Thread(target=reader, daemon=True); thread.start()
        before = file_pin(context), context.stat().st_mtime_ns
        try:
            child.stdin.write(b'\x01'); child.stdin.flush(); packets = []
            while True:
                line = events.get(timeout=4)
                self.assertIsInstance(line, bytes)
                if not line: break
                self.assertLessEqual(len(line), 65536)
                packet = json.loads(line); packets.append(packet)
                if packet['kind'] == 'review_chunk_native_request':
                    child.stdin.write(_encode(acknowledge(packet), 65536) + b'\n'); child.stdin.flush()
            self.assertEqual(child.wait(timeout=2), 0)
            self.assertEqual(child.stderr.read(4097), b'')
            self.assertEqual([item.get('operation') for item in packets], ['fence', 'capacity', 'fence', 'capacity', None])
            self.assertEqual(json.loads(packets[-1]['result_json']), {'fixture': 'mocked encoder', 'wide': self.origin})
            self.assertEqual((file_pin(context), context.stat().st_mtime_ns), before)
        finally:
            if child.poll() is None: child.kill()
            child.wait(timeout=2); thread.join(timeout=2); self.assertFalse(thread.is_alive())
            for stream in (child.stdin, child.stdout, child.stderr): stream.close()


if __name__ == '__main__':
    unittest.main()

import copy
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from engine.dubflow.review.longform_bridge import (
    MAX_CAPACITY_BYTES, MAX_FRAME_BYTES, NativeChunkChannel, encode_to_supervisor)
from engine.dubflow.review.production import ReviewError
from engine.dubflow.review.synthesis_input import _digest, _encode


def binding():
    return {key: _digest(key.encode()) for key in
            ('context_sha256', 'plan_sha256', 'chunk_identity', 'runtime_environment_sha256')}


def ack(request):
    return dict(schema_version=1, kind='review_chunk_native_ack', binding=request['binding'],
                sequence=request['sequence'], request_sha256=_digest(_encode(request, MAX_FRAME_BYTES)),
                accepted=True, runtime_environment_sha256=request['binding']['runtime_environment_sha256'])


class NativeInput:
    def __init__(self, output, change=lambda value: value):
        self.output, self.change = output, change

    def readline(self, limit):
        request = json.loads(self.output.getvalue().splitlines()[-1])
        value = self.change(ack(request))
        return value if isinstance(value, bytes) else _encode(value, MAX_FRAME_BYTES) + b'\n'


class LongformBridgeTests(unittest.TestCase):
    def test_cross_language_golden_frames_match_exact_request_ack_and_wide_result_bytes(self):
        contract = Path(__file__).resolve().parents[2]/'contracts/review/native-chunk-worker-bridge-v1.json'
        fixture = json.loads(contract.read_bytes())['examples'][0]
        request = json.loads(fixture['request_json'])
        output = io.BytesIO()
        channel = NativeChunkChannel(io.BytesIO(fixture['ack_json'].encode()+b'\n'), output,
                                     binding=request['binding'], deadline=time.monotonic()+5)
        channel.capacity(0)
        channel.finish({'wide': 9007199254740993})
        self.assertEqual(output.getvalue(), (fixture['request_json']+'\n'+fixture['result_json']+'\n').encode())

    def channel(self, change=lambda value: value):
        output = io.BytesIO()
        channel = NativeChunkChannel(NativeInput(output, change), output,
                                     binding=binding(), deadline=time.monotonic()+20)
        return channel, output

    def test_exact_capacity_environment_and_final_result_preserve_binding_order_and_wide_integer(self):
        channel, output = self.channel()
        channel.capacity(0)
        channel.capacity(MAX_CAPACITY_BYTES)
        self.assertEqual(channel.runtime_environment_fence(), binding()['runtime_environment_sha256'])
        channel.finish({'wide': 9007199254740993, 'text': 'Một người tôi rất quý.'})
        packets = [json.loads(raw) for raw in output.getvalue().splitlines()]
        self.assertEqual([p['sequence'] for p in packets], [1, 2, 3, 4])
        self.assertEqual([p.get('additional_bytes') for p in packets[:3]], ['0', str(MAX_CAPACITY_BYTES), '0'])
        self.assertEqual(json.loads(packets[-1]['result_json'])['wide'], 9007199254740993)
        self.assertEqual(packets[-1]['binding'], binding())
        before = output.getvalue()
        with self.assertRaises(ReviewError):
            channel.capacity(1)
        self.assertEqual(output.getvalue(), before)

    def test_refused_foreign_replayed_or_noncanonical_ack_poison_channel_without_more_output(self):
        def change(key, value):
            def mutate(packet):
                packet[key] = value
                return packet
            return mutate
        cases = [change('schema_version', True), change('schema_version', 2), change('kind', 'foreign'),
                 change('sequence', True), change('sequence', 2), change('request_sha256', _digest(b'foreign')),
                 change('accepted', False), change('accepted', 1), change('binding', binding() | {'foreign': 'x'}),
                 change('runtime_environment_sha256', _digest(b'foreign')), change('extra', None),
                 lambda value: _encode(value, MAX_FRAME_BYTES)+b' \n', lambda value: b'',
                 lambda value: b'{}', lambda value: b'x'*(MAX_FRAME_BYTES+1)+b'\n',
                 lambda value: b'{"schema_version":1,"schema_version":1}\n', lambda value: b'\xff\n']
        for mutation in cases:
            with self.subTest(mutation=mutation):
                channel, output = self.channel(mutation)
                with self.assertRaises(Exception):
                    channel.capacity(16)
                before = output.getvalue()
                with self.assertRaises(ReviewError):
                    channel.runtime_environment_fence()
                self.assertEqual(output.getvalue(), before)

    def test_invalid_capacity_count_deadline_and_result_bounds_do_not_grant_partial_success(self):
        for amount in (-1, True, MAX_CAPACITY_BYTES+1):
            channel, output = self.channel()
            with self.assertRaises(ReviewError):
                channel.capacity(amount)
            self.assertEqual(output.getvalue(), b'')
        channel, output = self.channel()
        channel._sequence = 4096
        with self.assertRaises(ReviewError):
            channel.capacity(0)
        self.assertEqual(output.getvalue(), b'')
        channel, output = self.channel()
        channel._deadline = time.monotonic()-1
        with self.assertRaises(ReviewError):
            channel.runtime_environment_fence()
        self.assertEqual(output.getvalue(), b'')
        channel, output = self.channel()
        with self.assertRaises(ReviewError):
            channel.finish({'large': 'x'*MAX_FRAME_BYTES})
        self.assertEqual(output.getvalue(), b'')
        for deadline in (True, float('inf'), time.monotonic()+901, time.monotonic()-1):
            with self.assertRaises(ReviewError):
                NativeChunkChannel(io.BytesIO(), io.BytesIO(), binding=binding(), deadline=deadline)

    def arguments(self):
        b = binding()
        return dict(context_sha256=b['context_sha256'], expected_plan_sha256=b['plan_sha256'],
                    chunk_identity=b['chunk_identity'], runtime_environment_sha256=b['runtime_environment_sha256'])

    def test_encoder_wrapper_routes_live_callbacks_to_native_transport_before_single_final_result(self):
        channel, output = self.channel()
        def encode(raw, **kwargs):
            self.assertEqual(raw, b'exact plan fixture')
            self.assertEqual(kwargs['runtime_environment_fence'](), binding()['runtime_environment_sha256'])
            kwargs['capacity'](1024)
            self.assertFalse(kwargs['cancelled']())
            return {'kind': 'explicit encoder dispatch fixture, not media/QC proof'}
        with patch('engine.dubflow.review.longform_encode.encode_chunk', side_effect=encode) as encoder:
            result = encode_to_supervisor(b'exact plan fixture', channel=channel, **self.arguments())
        self.assertEqual(encoder.call_count, 1)
        packets = [json.loads(raw) for raw in output.getvalue().splitlines()]
        self.assertEqual([p['kind'] for p in packets], ['review_chunk_native_request']*2+['review_chunk_worker_result'])
        self.assertEqual(json.loads(packets[-1]['result_json']), result)

    def test_encoder_binding_or_callback_replacement_refuses_before_encoder_and_failed_encode_never_finishes(self):
        for extra in ({'context_sha256': _digest(b'foreign')}, {'capacity': lambda value: None}):
            channel, output = self.channel()
            with patch('engine.dubflow.review.longform_encode.encode_chunk') as encoder:
                with self.assertRaises(ReviewError):
                    encode_to_supervisor(b'plan', channel=channel, **(self.arguments() | extra))
                encoder.assert_not_called()
            self.assertEqual(output.getvalue(), b'')
        channel, output = self.channel()
        with patch('engine.dubflow.review.longform_encode.encode_chunk', side_effect=ReviewError('encode refused')):
            with self.assertRaises(ReviewError):
                encode_to_supervisor(b'plan', channel=channel, **self.arguments())
        with self.assertRaises(ReviewError):
            channel.finish({'false': 'success'})
        self.assertEqual(output.getvalue(), b'')

    def test_actual_isolated_owned_python_child_exchanges_live_capacity_fence_and_result_with_bounded_parent(self):
        # Actual pipe/isolation component, with no media/descendant or native
        # WorkerTree claim. Rust containment and full product crash remain gates.
        root = Path(__file__).resolve().parents[2]
        code = ('import sys,time,json;sys.path.insert(0,sys.argv[1]);'
                'assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode;'
                'assert sys.stdin.buffer.read(1)==b"\\x01";'
                'from engine.dubflow.review.longform_bridge import NativeChunkChannel;'
                'b=json.loads(sys.argv[2]);c=NativeChunkChannel(sys.stdin.buffer,sys.stdout.buffer,binding=b,deadline=time.monotonic()+5);'
                'c.capacity(123456);c.runtime_environment_fence();c.finish({"wide":9007199254740993})')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', code, str(root), json.dumps(binding())],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        events = queue.Queue(maxsize=4)
        def reader():
            try:
                while True:
                    raw = child.stdout.readline(MAX_FRAME_BYTES+1)
                    events.put(raw, timeout=2)
                    if not raw:
                        return
            except Exception as error:
                events.put(error, timeout=2)
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            child.stdin.write(b'\x01');child.stdin.flush()
            requests, final = [], None
            while True:
                raw = events.get(timeout=3)
                self.assertIsInstance(raw, bytes)
                if not raw:
                    break
                self.assertLessEqual(len(raw), MAX_FRAME_BYTES)
                value = json.loads(raw)
                if value['kind'] == 'review_chunk_native_request':
                    requests.append(value)
                    child.stdin.write(_encode(ack(value), MAX_FRAME_BYTES)+b'\n');child.stdin.flush()
                else:
                    self.assertIsNone(final)
                    final = value
            self.assertEqual(child.wait(timeout=2), 0)
            self.assertEqual(child.stderr.read(4097), b'')
            self.assertEqual([v['operation'] for v in requests], ['capacity', 'fence'])
            self.assertEqual(requests[0]['additional_bytes'], '123456')
            self.assertEqual(json.loads(final['result_json']), {'wide': 9007199254740993})
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()


if __name__ == '__main__':
    unittest.main()

"""Private chunk encoder/native callback channel; no runtime or graph admission.

The native parent must contain this worker before its byte gate, enforce its
absolute deadline and independently admit every requested fence/capacity check.
Packets, callback hashes and child exit status never authorize media reuse.
"""
import copy
import math
import time

from .longform_plan import _closed, _hash, _integer
from .production import ReviewError
from .synthesis_input import _digest, _encode
from .wire import decode_review_json

MAX_FRAME_BYTES = 65536
MAX_MESSAGES = 4096
MAX_WIRE_BYTES = 16 * 1024 * 1024
MAX_CAPACITY_BYTES = 512 * 1024 * 1024
BINDING_FIELDS = {'context_sha256', 'plan_sha256', 'chunk_identity', 'runtime_environment_sha256'}


class NativeChunkChannel:
    def __init__(self, native_input, native_output, *, binding, deadline):
        _closed(binding, BINDING_FIELDS)
        for value in binding.values():
            _hash(value)
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or not 0 < deadline-time.monotonic() <= 900:
            raise ReviewError('chunk bridge needs the native bounded absolute deadline')
        self._input, self._output = native_input, native_output
        self._binding = copy.deepcopy(binding)
        self._deadline, self._sequence, self._bytes = deadline, 0, 0
        self._failed, self._finished = False, False

    def _check(self):
        if self._failed or self._finished or time.monotonic() >= self._deadline:
            raise ReviewError('chunk bridge is closed, failed or expired')

    def _write(self, packet):
        raw = _encode(packet, MAX_FRAME_BYTES-1) + b'\n'
        self._bytes += len(raw)
        if self._bytes > MAX_WIRE_BYTES:
            raise ReviewError('chunk bridge aggregate output exceeds its bound')
        if self._output.write(raw) != len(raw):
            raise ReviewError('chunk bridge response pipe did not accept complete bytes')
        self._output.flush()
        return raw[:-1]

    def _call(self, operation, additional=0):
        self._check()
        try:
            _integer(additional, 'chunk capacity request', 0, MAX_CAPACITY_BYTES)
            self._sequence += 1
            if self._sequence > MAX_MESSAGES:
                raise ReviewError('chunk bridge request count exceeds its bound')
            request = dict(schema_version=1, kind='review_chunk_native_request', binding=self._binding,
                           sequence=self._sequence, operation=operation, additional_bytes=str(additional))
            raw = self._write(request)
            # A stalled/partial parent is bounded by its native contained-process
            # deadline. No independent timeout may extend that deadline here.
            response = self._input.readline(MAX_FRAME_BYTES+1)
            self._check()
            if not response.endswith(b'\n') or not 1 < len(response) <= MAX_FRAME_BYTES:
                raise ReviewError('chunk bridge acknowledgement is missing, truncated or oversized')
            value = decode_review_json(response[:-1].decode('utf-8'))
            _closed(value, {'schema_version', 'kind', 'binding', 'sequence', 'request_sha256',
                            'accepted', 'runtime_environment_sha256'})
            if (_encode(value, MAX_FRAME_BYTES-1) != response[:-1] or type(value['schema_version']) is not int or
                    value['schema_version'] != 1 or value['kind'] != 'review_chunk_native_ack' or
                    value['binding'] != self._binding or type(value['sequence']) is not int or
                    value['sequence'] != self._sequence or value['request_sha256'] != _digest(raw) or
                    value['accepted'] is not True or value['runtime_environment_sha256'] != self._binding['runtime_environment_sha256']):
                raise ReviewError('chunk bridge acknowledgement changed its exact native request/binding or refused admission')
            return value['runtime_environment_sha256']
        except Exception:
            self._failed = True
            raise

    def capacity(self, additional_bytes):
        self._call('capacity', additional_bytes)

    def runtime_environment_fence(self):
        return self._call('fence')

    def cancelled(self):
        self._check()
        return False  # Native observation/cancellation kills the contained worker.

    def finish(self, result):
        self._check()
        try:
            if not isinstance(result, dict):
                raise ReviewError('chunk bridge result must be a bounded JSON object')
            self._write(dict(schema_version=1, kind='review_chunk_worker_result', binding=self._binding,
                             sequence=self._sequence+1, result_json=_encode(result, MAX_FRAME_BYTES-1).decode('utf-8')))
            self._finished = True
        except Exception:
            self._failed = True
            raise


def encode_to_supervisor(raw_plan, *, channel, **arguments):
    """Connect the existing real encoder's live callbacks to its native parent.

    Caller still must derive arguments from native current/source/release/index/
    environment admission; this helper is not a public worker entry/capability.
    """
    from .longform_encode import encode_chunk
    if not isinstance(channel, NativeChunkChannel):
        raise ReviewError('chunk encoding requires the private native channel')
    channel._check()
    if set(arguments) & {'capacity', 'runtime_environment_fence', 'cancelled'}:
        raise ReviewError('chunk caller cannot replace native callback transport')
    expected = {'context_sha256': arguments.get('context_sha256'),
                'plan_sha256': arguments.get('expected_plan_sha256'),
                'chunk_identity': arguments.get('chunk_identity'),
                'runtime_environment_sha256': arguments.get('runtime_environment_sha256')}
    if expected != channel._binding:
        raise ReviewError('chunk encoder arguments differ from native channel binding')
    try:
        result = encode_chunk(raw_plan, **arguments, capacity=channel.capacity,
                              runtime_environment_fence=channel.runtime_environment_fence,
                              cancelled=channel.cancelled)
        channel.finish(result)
        return result
    except Exception:
        channel._failed = True
        raise

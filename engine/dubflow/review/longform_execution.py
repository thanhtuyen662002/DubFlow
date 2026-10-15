"""Isolated private chunk dispatch; the native parent owns every admission.

This entry consumes one sealed context and the existing native callback wire.
Its result is an unadmitted candidate, never a checkpoint, QC or graph grant.
"""
from pathlib import Path
import sys

MAX_CONTEXT = 128 * 1024


def _metadata(pin, root, limit, deadline, *, observed=None):
    """Read bounded immutable bytes; keep the exact supplied path spelling."""
    import hashlib
    import os
    import stat
    import time
    from .longform_plan import _hash
    from .production import ReviewError
    from .render_execution import _closed, _path, _size
    _closed(pin, {'path', 'sha256', 'size_bytes'})
    _hash(pin['sha256'])
    size = _size(pin['size_bytes'])
    if size > limit:
        raise ReviewError('chunk dispatch metadata exceeds its specific bound')
    path = _path(pin['path'], root)

    def identity(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o222 or
                getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ReviewError('chunk dispatch metadata must be sealed regular single-link bytes')
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode,
                getattr(info, 'st_file_attributes', 0))

    if time.monotonic() >= deadline:
        raise ReviewError('chunk dispatch metadata deadline expired')
    before = identity(path.stat())
    if before[2] != size:
        raise ReviewError('chunk dispatch metadata byte count changed')
    if observed is not None:
        key = str(path)
        if key in observed and observed[key] != before:
            raise ReviewError('chunk dispatch metadata changed its admitted physical identity')
        observed.setdefault(key, before)
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    flags |= getattr(os, 'O_NOINHERIT', 0)
    data = bytearray()
    digest = hashlib.sha256()
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        if identity(os.fstat(stream.fileno())) != before:
            raise ReviewError('chunk dispatch metadata handle changed its physical identity')
        while True:
            if time.monotonic() >= deadline:
                raise ReviewError('chunk dispatch metadata deadline expired')
            block = stream.read(min(32768, size + 1 - len(data)))
            if not block:
                break
            data.extend(block)
            if len(data) > size:
                raise ReviewError('chunk dispatch metadata grew during its bounded read')
            digest.update(block)
        if identity(os.fstat(stream.fileno())) != before:
            raise ReviewError('chunk dispatch metadata handle changed while reading')
    _path(str(path), root)
    if identity(path.stat()) != before or len(data) != size or 'sha256:' + digest.hexdigest() != pin['sha256']:
        raise ReviewError('chunk dispatch metadata bytes or physical identity changed')
    return bytes(data)


def admit_context(raw, expected_hash):
    from .longform_bridge import BINDING_FIELDS
    from .longform_plan import _closed, _hash, _integer
    from .production import ReviewError
    from .render_execution import _path, _size
    from .synthesis_input import _digest, _encode
    from .wire import decode_review_json
    _hash(expected_hash)
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_CONTEXT or _digest(raw) != expected_hash:
        raise ReviewError('chunk execution context differs from its native hash or bound')
    value = decode_review_json(raw.decode('utf-8'))
    _closed(value, {'schema_version', 'kind', 'binding', 'processing_recipe_sha256',
        'plan', 'plan_root', 'source', 'source_root', 'executables', 'media_root', 'output_root',
        'geometry', 'video_stream_index', 'burn_in_subtitles', 'captions', 'max_chunk_bytes', 'timeout_seconds'})
    if (type(value['schema_version']) is not int or value['schema_version'] != 1 or
            value['kind'] != 'review_native_chunk_execution_context' or _encode(value, MAX_CONTEXT) != raw):
        raise ReviewError('unknown or noncanonical chunk execution context requires compatibility')
    _closed(value['binding'], BINDING_FIELDS)
    for item in value['binding'].values():
        _hash(item)
    _hash(value['processing_recipe_sha256'])
    for name in ('plan_root', 'source_root', 'media_root', 'output_root'):
        if not isinstance(value[name], str) or not 1 <= len(value[name]) <= 4096 or not _path(value[name]).is_dir():
            raise ReviewError('chunk execution requires existing native root directories')
    if not isinstance(value['geometry'], list) or len(value['geometry']) != 2:
        raise ReviewError('chunk execution requires exact native source geometry')
    for item in value['geometry']:
        _integer(item, 'chunk source geometry', 1, 32768)
    _integer(value['video_stream_index'], 'chunk source video stream', 0, 1023)
    _integer(value['timeout_seconds'], 'chunk execution duration bound', 1, 900)
    maximum = _size(value['max_chunk_bytes'])
    if maximum > 256 * 1024 * 1024 or type(value['burn_in_subtitles']) is not bool:
        raise ReviewError('chunk execution exceeds media policy or changes caption mode')
    for name in ('plan', 'source'):
        _closed(value[name], {'path', 'sha256', 'size_bytes'})
        _hash(value[name]['sha256']); _size(value[name]['size_bytes'])
        if not isinstance(value[name]['path'], str) or not 1 <= len(value[name]['path']) <= 4096:
            raise ReviewError('chunk execution needs bounded native file locators')
        _path(value[name]['path'], value[name + '_root'])
    if value['plan']['sha256'] != value['binding']['plan_sha256']:
        raise ReviewError('chunk execution changes its separately bound plan')
    _closed(value['executables'], {'ffmpeg', 'ffprobe'})
    for pin in value['executables'].values():
        _closed(pin, {'path', 'sha256', 'size_bytes'})
        _hash(pin['sha256']); _size(pin['size_bytes'])
        # Complete executable/import inventory and path admission remains native.
        # The existing encoder independently verifies owned executable paths.
        if not isinstance(pin['path'], str) or not 1 <= len(pin['path']) <= 4096:
            raise ReviewError('chunk execution executable locator is missing or oversized')
    if value['burn_in_subtitles']:
        _closed(value['captions'], {'file', 'root'})
        _closed(value['captions']['file'], {'path', 'sha256', 'size_bytes'})
        _hash(value['captions']['file']['sha256']); _size(value['captions']['file']['size_bytes'])
        if not isinstance(value['captions']['file']['path'], str) or not 1 <= len(value['captions']['file']['path']) <= 4096:
            raise ReviewError('chunk caption locator is missing or oversized')
        if not isinstance(value['captions']['root'], str) or not 1 <= len(value['captions']['root']) <= 4096:
            raise ReviewError('chunk caption root is missing or oversized')
        _path(value['captions']['file']['path'], value['captions']['root'])
    elif value['captions'] is not None:
        raise ReviewError('non-burn chunk must not acquire undeclared caption inputs')
    return value


def execute_context(raw, *, expected_hash, native_input, native_output, context_fence):
    import time
    from .longform_bridge import NativeChunkChannel
    from .longform_captions import MAX_ASS_BYTES
    from .longform_encode import encode_chunk
    from .longform_plan import MAX_PLAN_BYTES, read_plan
    from .production import ReviewError
    context = admit_context(raw, expected_hash)
    if not callable(context_fence):
        raise ReviewError('chunk execution requires its native-context physical readback')
    deadline = time.monotonic() + context['timeout_seconds']
    binding = context['binding']
    observed = {}
    plan = _metadata(context['plan'], context['plan_root'], MAX_PLAN_BYTES, deadline, observed=observed)
    parsed = read_plan(plan, expected_hash=binding['plan_sha256'], context_sha256=binding['context_sha256'],
        source_sha256=context['source']['sha256'], processing_recipe_sha256=context['processing_recipe_sha256'])
    if sum(chunk['identity'] == binding['chunk_identity'] for chunk in parsed['chunks']) != 1:
        raise ReviewError('chunk execution identity is absent or ambiguous in its native plan')
    captions = None
    if context['captions'] is not None:
        captions = _metadata(context['captions']['file'], context['captions']['root'], MAX_ASS_BYTES, deadline, observed=observed)
    channel = NativeChunkChannel(native_input, native_output, binding=binding, deadline=deadline)
    try:
        context_fence()
        channel.runtime_environment_fence()
        channel.capacity(0)
        tools = context['executables']
        result = encode_chunk(plan, expected_plan_sha256=binding['plan_sha256'],
            context_sha256=binding['context_sha256'], source_pin=context['source'], source_root=context['source_root'],
            processing_recipe_sha256=context['processing_recipe_sha256'], chunk_identity=binding['chunk_identity'],
            ffmpeg_path=tools['ffmpeg']['path'], ffprobe_path=tools['ffprobe']['path'], media_root=context['media_root'],
            ffmpeg_pin={key: tools['ffmpeg'][key] for key in ('sha256', 'size_bytes')},
            ffprobe_pin={key: tools['ffprobe'][key] for key in ('sha256', 'size_bytes')},
            output_root=context['output_root'], geometry=tuple(context['geometry']),
            video_stream_index=context['video_stream_index'], burn_in_subtitles=context['burn_in_subtitles'],
            captions_ass=captions, captions_sha256=context['captions']['file']['sha256'] if captions is not None else None,
            runtime_environment_sha256=binding['runtime_environment_sha256'],
            runtime_environment_fence=channel.runtime_environment_fence, capacity=channel.capacity,
            max_chunk_bytes=int(context['max_chunk_bytes']),
            timeout_seconds=context['timeout_seconds'], cancelled=channel.cancelled)
        context_fence()
        if _metadata(context['plan'], context['plan_root'], MAX_PLAN_BYTES, deadline, observed=observed) != plan:
            raise ReviewError('chunk execution plan changed before final response')
        if captions is not None and _metadata(context['captions']['file'], context['captions']['root'], MAX_ASS_BYTES, deadline, observed=observed) != captions:
            raise ReviewError('chunk execution captions changed before final response')
        channel.runtime_environment_fence()
        channel.capacity(0)
        channel.finish(result)
        return result
    except Exception:
        channel._failed = True
        raise


def main(argv=None):
    import argparse
    import json
    import time
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--context-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.stdin.buffer.read(1) != b'\x01':
            raise ValueError('chunk dispatch requires owned Python -I -S -B and native byte gate')
        if __package__ in (None, ''):
            sys.path.append(str(Path(sys.executable).parent / 'Lib/site-packages'))
            sys.path.append(str(Path(__file__).resolve().parents[3]))
        from engine.dubflow.review import longform_execution as implementation
        # The context is immutable metadata, not an arbitrary filesystem root.
        pin = dict(path=str(args.context), sha256=args.context_sha256,
                   size_bytes=str(args.context.stat().st_size))
        observed = {}
        raw = implementation._metadata(pin, args.context.parent, MAX_CONTEXT, time.monotonic() + 30, observed=observed)

        def context_fence():
            from engine.dubflow.review.production import ReviewError
            if implementation._metadata(pin, args.context.parent, MAX_CONTEXT, deadline, observed=observed) != raw:
                raise ReviewError('chunk dispatch context changed before final response')

        deadline = time.monotonic() + implementation.admit_context(raw, args.context_sha256)['timeout_seconds']
        implementation.execute_context(raw, expected_hash=args.context_sha256,
            native_input=sys.stdin.buffer, native_output=sys.stdout.buffer, context_fence=context_fence)
        return 0
    except Exception as error:
        message = str(error).encode()[:1024].decode(errors='ignore')
        sys.stderr.write(json.dumps({'code': 'REVIEW_CHUNK_EXECUTION_INVALID', 'message': message}, ensure_ascii=True) + '\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

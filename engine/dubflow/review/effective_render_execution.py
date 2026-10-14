"""Private current B1/B2 render; native must admit current graph and all locators.

This distinct tag/recipe preserves current captions on a voice-only edit. It
never authorizes SQL/graph publication or repins an existing producer bundle.
"""
from pathlib import Path
import sys


def admit_context(raw):
    from .render_execution import MAX_PACKET,_closed,_admit_media_context,_path
    from .corrected_timeline import admit_correction
    from .effective_timeline_execution import prepare_timeline
    from .production import ReviewError
    from .synthesis_input import _digest,_encode
    from .wire import decode_review_json,read_review_snapshot
    if not isinstance(raw,bytes) or not 1<=len(raw)<=MAX_PACKET:raise ReviewError('current render context exceeds its bound')
    context=decode_review_json(raw.decode('utf-8'))
    _closed(context,('schema_version','kind','job_id','mode','current_token','origin_snapshot_json',
        'before_snapshot_json','after_snapshot_json','original_manifest_json','original_manifest_sha256',
        'original_timeline_json','current_timeline_json','current_timeline_sha256','corrected_timeline_json',
        'corrected_timeline_sha256','current_captions','original_output_root','source','retained_mix','burn_in_subtitles'))
    if (type(context['schema_version']) is not int or context['schema_version']!=1 or
        context['kind']!='review_native_effective_render_context' or context['mode'] not in ('b1','b2') or
        type(context['burn_in_subtitles']) is not bool):
        raise ReviewError('unknown current render context needs explicit compatibility')
    for key in ('original_manifest_json','corrected_timeline_json'):
        if not isinstance(context[key],str) or not 1<=len(context[key].encode())<=MAX_PACKET:
            raise ReviewError('current render needs exact bounded native JSON strings')
    timeline_context={key:context[key] for key in ('schema_version','mode','current_token','origin_snapshot_json',
        'before_snapshot_json','after_snapshot_json','original_timeline_json','current_timeline_json','current_timeline_sha256')}
    timeline_context.update(kind='review_native_effective_timeline_context',
        original_timeline_sha256=_digest(context['original_timeline_json'].encode()))
    prepared=prepare_timeline(_encode(timeline_context,MAX_PACKET))
    corrected=context['corrected_timeline_json'].encode()
    if (_encode(decode_review_json(context['corrected_timeline_json']),MAX_PACKET)!=
        _encode(decode_review_json(prepared['corrected_timeline_json']),MAX_PACKET) or
        _digest(corrected)!=context['corrected_timeline_sha256']):
        raise ReviewError('current render substitutes native cumulative timeline or source bits')
    before=read_review_snapshot(decode_review_json(context['before_snapshot_json']))
    after=read_review_snapshot(decode_review_json(context['after_snapshot_json']))
    old,new=admit_correction(before,after)
    if before.job_id!=context['job_id'] or context['current_token']['original_manifest_sha256']!=context['original_manifest_sha256']:
        raise ReviewError('current render substitutes its native job/archive token')
    if old.translated_text==new.translated_text and corrected!=context['current_timeline_json'].encode():
        raise ReviewError('voice-only current render must keep exact current timeline bytes')
    manifest_raw=context['original_manifest_json'].encode();manifest=decode_review_json(manifest_raw.decode())
    if (not isinstance(manifest,dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version']!=1 or
        manifest.get('kind')!='dubflow_localization_job' or manifest.get('job_id')!=before.job_id or
        _digest(manifest_raw)!=context['original_manifest_sha256'] or
        manifest['artifacts']['editable_timeline']['sha256']!=timeline_context['original_timeline_sha256']):
        raise ReviewError('current render changed the separately admitted original archive')
    captions=context['current_captions']
    _closed(captions,('captions_srt','captions_ass','artifact_root'))
    _path(captions['artifact_root'])
    for role in ('captions_srt','captions_ass'):
        _closed(captions[role],('path','sha256','size_bytes'))
        _path(captions[role]['path'],captions['artifact_root'])
    _admit_media_context(context,manifest,before,after)
    return context,manifest,before,after,old.translated_text!=new.translated_text


def _captions(context,deadline):
    from .render_execution import _verify,_read_packet
    from .production import ReviewError
    from .wire import decode_review_json
    from engine.dubflow.worker.production_job import _format_timestamp
    pins=context['current_captions'];raw={}
    for role in ('captions_srt','captions_ass'):
        raw[role]=_read_packet(_verify(pins[role],pins['artifact_root'],deadline))
    cues=decode_review_json(context['current_timeline_json'])['cues']
    expected='\n\n'.join(f"{i}\n{_format_timestamp(c['start_ms'])} --> {_format_timestamp(c['end_ms'])}\n{c['translated_text']}"
        for i,c in enumerate(cues,1))+'\n\n'
    # Recognize the two existing file framings while preserving every input byte.
    if raw['captions_srt'] not in (expected.encode(),expected.replace('\n','\r\n').encode()):
        raise ReviewError('current SRT loses a previous translation or source interval')
    ass=raw['captions_ass'].decode('utf-8').splitlines()
    if ass.count('[Events]')!=1:raise ReviewError('current ASS needs its admitted event section')
    events=ass[ass.index('[Events]')+1:]
    expected_format='Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text'
    expected_events=[f"Dialogue: 0,{_format_timestamp(c['start_ms'],ass=True)},{_format_timestamp(c['end_ms'],ass=True)},Default,,0,0,0,,"+
        c['translated_text'].replace(chr(10),r'\\N') for c in cues]
    if events!=[expected_format,*expected_events]:
        raise ReviewError('current ASS loses a previous translation or source interval')
    return raw


def render_candidate(raw,*,output_root,ffmpeg_path,ffprobe_path,media_root,timeout_seconds=900):
    from .render_execution import _render_admitted
    return _render_admitted(raw,output_root=output_root,ffmpeg_path=ffmpeg_path,ffprobe_path=ffprobe_path,
        media_root=media_root,timeout_seconds=timeout_seconds,admission=admit_context,
        recipe='review-private-effective-render-v1',extra_recipe_sources=(Path(__file__),
        Path(__file__).with_name('effective_timeline_execution.py')),caption_reader=_captions)


def main(argv=None):
    import argparse,json
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('context','output-root','ffmpeg','ffprobe','media-root'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--context-sha256',required=True);args=parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.stdin.buffer.read(1)!=b'\x01':
            raise ValueError('current render requires owned Python -I -S -B and native byte gate')
        if __package__ in (None,''):
            sys.path.append(str(Path(sys.executable).parent/'Lib/site-packages'))
            sys.path.append(str(Path(__file__).resolve().parents[3]))
        from engine.dubflow.review.render_execution import _read_packet,MAX_PACKET
        from engine.dubflow.review.effective_render_execution import render_candidate
        from engine.dubflow.review.synthesis_input import _digest,_encode
        from engine.dubflow.review.export import _hash
        _hash(args.context_sha256);raw=_read_packet(args.context)
        if _digest(raw)!=args.context_sha256:raise ValueError('current render context differs from native byte pin')
        result=render_candidate(raw,output_root=args.output_root,ffmpeg_path=args.ffmpeg,ffprobe_path=args.ffprobe,media_root=args.media_root)
        sys.stdout.buffer.write(_encode(result,MAX_PACKET));sys.stdout.buffer.flush();return 0
    except Exception as error:
        sys.stderr.write(json.dumps({'code':'REVIEW_EFFECTIVE_RENDER_FAILED','message':str(error)[:2048]},ensure_ascii=True)+'\n');return 2


if __name__=='__main__':raise SystemExit(main())

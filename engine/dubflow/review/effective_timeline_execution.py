"""Isolated current B1/B2 timeline derivation; no media/model/SQL execution."""
from pathlib import Path
import sys

LIMIT=64*1024*1024


def prepare_timeline(raw:bytes)->dict:
    from engine.dubflow.review.corrected_timeline import derive_effective_timeline,admit_correction
    from engine.dubflow.review.production import ReviewError,_tick
    from engine.dubflow.review.synthesis_input import _digest,_encode
    from engine.dubflow.review.export import _hash
    from engine.dubflow.review.wire import decode_review_json,read_review_snapshot
    if not isinstance(raw,bytes) or not 1<=len(raw)<=LIMIT:raise ReviewError('effective timeline context exceeds its bound')
    value=decode_review_json(raw.decode('utf-8'))
    fields={'schema_version','kind','mode','current_token','origin_snapshot_json','before_snapshot_json',
        'after_snapshot_json','original_timeline_json','original_timeline_sha256','current_timeline_json','current_timeline_sha256'}
    if (not isinstance(value,dict) or set(value)!=fields or type(value['schema_version']) is not int or
        value['schema_version']!=1 or value['kind']!='review_native_effective_timeline_context' or value['mode'] not in ('b1','b2')):
        raise ReviewError('unknown effective timeline context requires explicit compatibility')
    for key in ('origin_snapshot_json','before_snapshot_json','after_snapshot_json','original_timeline_json','current_timeline_json'):
        if not isinstance(value[key],str) or not 1<=len(value[key].encode())<=LIMIT:
            raise ReviewError('effective timeline requires bounded exact native JSON strings')
    token=value['current_token']
    if not isinstance(token,dict) or set(token)!={'job_id','revision','snapshot_sha256','graph_sha256','original_manifest_sha256'}:
        raise ReviewError('effective timeline requires a complete current token')
    if not isinstance(token['revision'],str):raise ReviewError('effective timeline public revision must be a canonical string')
    revision=int(_tick(token['revision'],'revision'))
    for key in ('snapshot_sha256','graph_sha256','original_manifest_sha256'):_hash(token[key])
    origin,before,after=(read_review_snapshot(decode_review_json(value[key])) for key in
        ('origin_snapshot_json','before_snapshot_json','after_snapshot_json'))
    old,_=admit_correction(before,after)
    if (before.job_id!=token['job_id'] or before.revision!=revision or
        _digest(_encode(before.to_dict(),LIMIT))!=token['snapshot_sha256'] or
        (old.voice is None)!=(value['mode']=='b1')):
        raise ReviewError('effective timeline substitutes current counter/snapshot or mode')
    original=value['original_timeline_json'].encode();current=value['current_timeline_json'].encode()
    if _digest(original)!=value['original_timeline_sha256'] or _digest(current)!=value['current_timeline_sha256']:
        raise ReviewError('effective timeline differs from native archived/current byte pins')
    corrected=derive_effective_timeline(original,origin,current,before,after)
    return {'schema_version':1,'kind':'review_prepared_effective_timeline','context_sha256':_digest(raw),
        'mode':value['mode'],'current_token':token,'after_snapshot_sha256':_digest(_encode(after.to_dict(),LIMIT)),
        'current_timeline_sha256':_digest(current),'corrected_timeline_json':corrected.decode(),
        'corrected_timeline_sha256':_digest(corrected),'model_weights_verified':False,
        'active_graph_published':False,'correction_ready':False}


def main(argv=None):
    import argparse,json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',type=Path,required=True);parser.add_argument('--context-sha256',required=True)
    args=parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.stdin.buffer.read(1)!=b'\x01':
            raise ValueError('effective timeline requires owned Python -I -S -B and native byte gate')
        if __package__ in (None,''):
            sys.path.append(str(Path(sys.executable).resolve().parent/'Lib/site-packages'))
            sys.path.append(str(Path(__file__).resolve().parents[3]))
        from engine.dubflow.review.export import _json_bytes
        from engine.dubflow.review.synthesis_input import _encode
        result=prepare_timeline(_json_bytes(args.context,args.context_sha256))
        sys.stdout.buffer.write(_encode(result,LIMIT)+b'\n');return 0
    except Exception as error:
        message=str(error).encode()[:1024].decode(errors='ignore')
        sys.stderr.write(json.dumps({'code':'REVIEW_EFFECTIVE_TIMELINE_INVALID','message':message},ensure_ascii=True)+'\n');return 2


if __name__=='__main__':raise SystemExit(main())

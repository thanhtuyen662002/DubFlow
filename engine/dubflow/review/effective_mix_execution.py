"""Owned current-assignment mix; no speech inference, SQL or graph publication.

All descriptors come from a native admitted current request and sealed cue.
The supervisor independently validates composition/metadata and retains PCM.
"""
from __future__ import annotations
from pathlib import Path
import sys

LIMIT=64*1024*1024


def _imports():
    if __package__ in (None,""):
        sys.path.append(str(Path(sys.executable).resolve().parent/'Lib/site-packages'))
        sys.path.append(str(Path(__file__).resolve().parents[3]))


def prepare_composition(raw: bytes, *, app_root: Path, profile_path: Path):
    from engine.dubflow.review.audio_composition import PinnedDocument, rebind_audio_composition, replace_audio_cue
    from engine.dubflow.review.cue_execution import accepted_packet
    from engine.dubflow.review.effective_cue_execution import prepare_bound_cue, read_bound_audio_pins
    from engine.dubflow.review.export import _json_bytes
    from engine.dubflow.review.production import ReviewError
    from engine.dubflow.review.synthesis_input import _digest, _encode, read_cue_synthesis_input
    from engine.dubflow.review.wire import decode_review_json, read_review_snapshot
    if not isinstance(raw,bytes) or not 1<=len(raw)<=LIMIT:
        raise ReviewError('current mix context exceeds its bound')
    value=decode_review_json(raw.decode('utf-8'))
    fields={'schema_version','kind','preparation_context_json','preparation_context_sha256',
        'bindings_json','bindings_sha256','request_json','request_sha256',
        'accepted_tts_document','accepted_spoken_text','accept_context'}
    if (not isinstance(value,dict) or set(value)!=fields or type(value['schema_version']) is not int or
        value['schema_version']!=1 or value['kind']!='review_native_effective_mix_context'):
        raise ReviewError('unknown current mix context requires explicit compatibility')
    strings={}
    for text,pin in (('preparation_context_json','preparation_context_sha256'),
                     ('bindings_json','bindings_sha256'),('request_json','request_sha256')):
        if not isinstance(value[text],str) or not 1<=len(value[text].encode())<=LIMIT:
            raise ReviewError('current mix context requires bounded exact native bytes')
        strings[text]=value[text].encode()
        if _digest(strings[text])!=value[pin]:
            raise ReviewError('current mix context substitutes native bytes')
    prepared=prepare_bound_cue(strings['preparation_context_json'],strings['bindings_json'],
        app_root=app_root,profile_path=profile_path)
    if prepared['request_json']!=value['request_json'] or prepared['request_sha256']!=value['request_sha256']:
        raise ReviewError('current mix request differs from admitted current assignments')
    request=read_cue_synthesis_input(strings['request_json'],expected_hash=value['request_sha256'])
    pins=read_bound_audio_pins(strings['bindings_json'])
    pins.pop('expected_token')
    context=decode_review_json(value['preparation_context_json'])
    current=rebind_audio_composition(pins.pop('composition_raw'),
        content_snapshot=pins.pop('composition_snapshot'),
        current_snapshot=read_review_snapshot(decode_review_json(context['before_snapshot_json'])),
        expected_sha256=context['current_composition_sha256'],manifest_sha256=request.to_dict()['manifest_sha256'],**pins)
    if _encode(current.to_dict(),LIMIT).decode()!=prepared['effective_composition_json']:
        raise ReviewError('current mix projection differs from independently prepared cue')
    def metadata(name):
        pin=value[name]
        if not isinstance(pin,dict) or set(pin)!={'path','sha256','size_bytes'}:
            raise ReviewError('accepted current mix metadata descriptor is malformed')
        return _json_bytes(Path(pin['path']),pin['sha256'],pin['size_bytes'])
    acceptance_raw=metadata('accept_context')
    acceptance=decode_review_json(acceptance_raw.decode())
    if (acceptance.get('request_json')!=value['request_json'] or
        acceptance.get('request_sha256')!=value['request_sha256']):
        raise ReviewError('accepted current speech belongs to another request')
    checked=accepted_packet(acceptance_raw)
    pin=value['accepted_tts_document']
    if not isinstance(pin,dict) or set(pin)!={'path','sha256','size_bytes','artifact_root','metadata_root'}:
        raise ReviewError('current accepted TTS descriptor is malformed')
    document=PinnedDocument.from_admitted_descriptor(pin)
    if (document.read()!=checked['tts_document_json'].encode() or
        metadata('accepted_spoken_text')!=checked['spoken_text_json'].encode()):
        raise ReviewError('current accepted TTS/spoken metadata differs from native retarget')
    after=read_review_snapshot(decode_review_json(context['after_snapshot_json']))
    return replace_audio_cue(current,after,request=request,replacement_document=document),prepared['before_token'],request.input_hash


def produce_mix(raw: bytes, *, output_root: Path, app_root: Path, profile_path: Path) -> dict:
    from engine.dubflow.review.audio_composition import mix_audio_candidate
    from engine.dubflow.review.production import ReviewError
    from engine.dubflow.review.synthesis_input import _digest,_encode
    composition,token,request_hash=prepare_composition(raw,app_root=app_root,profile_path=profile_path)
    output=Path(output_root)
    if not output.is_absolute() or '..' in output.parts:
        raise ReviewError('current mix output must be its absolute native private root')
    mixed=mix_audio_candidate(composition,output_root=output)
    composition_raw=_encode(composition.to_dict(),LIMIT).decode()
    mix_raw=mixed.to_json()+'\n'
    return {'schema_version':1,'kind':'review_effective_mix_candidate','context_sha256':_digest(raw),
        'before_token':token,'request_sha256':request_hash,'composition_json':composition_raw,
        'composition_sha256':composition.input_hash,'mix_document_json':mix_raw,
        'mix_document_sha256':_digest(mix_raw.encode()),'active_graph_published':False}


def main(argv=None):
    import argparse,json
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('context','output-root','app-root','profile'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--context-sha256',required=True)
    args=parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.stdin.buffer.read(1)!=b'\x01':
            raise ValueError('current mix requires owned Python -I -S -B and native byte gate')
        _imports()
        from engine.dubflow.review.export import _json_bytes
        from engine.dubflow.review.synthesis_input import _encode
        packet=produce_mix(_json_bytes(args.context,args.context_sha256),output_root=args.output_root,
            app_root=args.app_root,profile_path=args.profile)
        sys.stdout.buffer.write(_encode(packet,LIMIT)+b'\n')
        return 0
    except Exception as error:
        message=str(error).encode()[:1024].decode(errors='ignore')
        sys.stderr.write(json.dumps({'code':'REVIEW_EFFECTIVE_MIX_INVALID','message':message},ensure_ascii=True)+'\n')
        return 2


if __name__=='__main__':
    raise SystemExit(main())

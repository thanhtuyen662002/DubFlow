from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import os,subprocess,sys,time,unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from engine.dubflow.review.corrected_timeline import derive_effective_timeline
from engine.dubflow.review.effective_render_execution import admit_context,render_candidate
from engine.dubflow.review.render_execution import admit_context as admit_original
from engine.dubflow.review.production import ReviewError,RegenerationPlan,_correction_stages
from engine.dubflow.review.synthesis_input import _digest,_encode
from engine.dubflow.review.wire import decode_review_json,read_review_snapshot
import test_render_execution as fixtures

REPO=Path(__file__).resolve().parents[2]


def fixture(directory,*,b1=False,voice_only=False,source_probe=None,source_bytes=None):
    context,args=fixtures.render_fixture(directory,b1=b1,source_probe=source_probe,source_bytes=source_bytes)
    origin=read_review_snapshot(decode_review_json(context['before_snapshot_json']))
    current=context['corrected_timeline_json'].encode()
    before=replace(read_review_snapshot(decode_review_json(context['after_snapshot_json'])),revision=2)
    selected=before.findings[0 if voice_only else 2]
    updated=replace(selected,voice='voice-next') if voice_only else replace(selected,translated_text='Chúc bạn bình an')
    stages=_correction_stages(translation_changed=selected.translated_text!=updated.translated_text,
        voice_changed=selected.voice!=updated.voice,has_voice=selected.voice is not None)
    after=replace(before,revision=3,last_plan=RegenerationPlan(selected.finding_id,stages,('asr','ocr'),tuple('replacement:'+s for s in stages)),
        findings=tuple(replace(f,translated_text=updated.translated_text,voice=updated.voice) if f.cue_id==selected.cue_id else f for f in before.findings))
    corrected=derive_effective_timeline(context['original_timeline_json'].encode(),origin,current,before,after)
    caption_root=Path(directory)/'current';caption_root.mkdir()
    from engine.dubflow.worker.production_job import TextCue,_write_subtitles
    srt,ass=_write_subtitles(caption_root,tuple(TextCue(**c) for c in json.loads(current)['cues']))
    for path in (srt,ass):path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'))
    context.update(kind='review_native_effective_render_context',origin_snapshot_json=_encode(origin.to_dict(),65536).decode(),
        before_snapshot_json=_encode(before.to_dict(),65536).decode(),after_snapshot_json=_encode(after.to_dict(),65536).decode(),
        current_timeline_json=current.decode(),current_timeline_sha256=_digest(current),
        corrected_timeline_json=corrected.decode(),corrected_timeline_sha256=_digest(corrected),
        current_token={'job_id':before.job_id,'revision':'2','snapshot_sha256':_digest(_encode(before.to_dict(),65536)),
            'graph_sha256':'sha256:'+'a'*64,'original_manifest_sha256':context['original_manifest_sha256']},
        current_captions={'artifact_root':str(caption_root),'captions_srt':fixtures.pin(srt),'captions_ass':fixtures.pin(ass)})
    if not b1:
        retained=context['retained_mix'];composition=json.loads(retained['composition_json'])
        composition['review_snapshot_sha256']=_digest(_encode(after.to_dict(),65536));raw=_encode(composition,65536)
        retained.update(composition_json=raw.decode(),composition_sha256=_digest(raw))
        mixed=json.loads(retained['mix_document_json']);mixed['provenance']['input_hash']=_digest(raw);raw=_encode(mixed,65536)
        retained.update(mix_document_json=raw.decode(),mix_document_sha256=_digest(raw))
    return context,args


class EffectiveRenderTests(unittest.TestCase):
    def adapters(self,args):
        media=self.enterContext(patch('engine.dubflow.media.FfmpegMediaAdapter')).return_value
        probe=self.enterContext(patch('engine.dubflow.media.MediaProbe')).return_value
        media.ffmpeg_path=args['ffmpeg_path'];probe.ffprobe_path=args['ffprobe_path']
        media.render.side_effect=lambda source,target,**kw:target.write_bytes(b'private current-render fixture')
        media._run.return_value=subprocess.CompletedProcess([],0,'','')
        probe.probe.side_effect=fixtures.probe_result
        return media,probe

    def test_current_b1_b2_second_translation_keeps_first_caption_and_other_source_content(self):
        with TemporaryDirectory(prefix='dubflow-current-render-cumulative-') as directory:
            for b1 in (True,False):
                child=Path(directory)/str(b1);child.mkdir()
                context,args=fixture(child,b1=b1);media,_=self.adapters(args)
                before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in child.rglob('*') if p.is_file()}
                result=render_candidate(_encode(context,65536),**args)
                srt=Path(result['artifacts']['captions_srt']['path']).read_text(encoding='utf-8')
                self.assertIn('Chào bạn',srt);self.assertIn('Chúc bạn bình an',srt);self.assertIn('Yên lặng',srt)
                self.assertIn('00:00:03,000 --> 00:00:04,000',srt)
                self.assertEqual(result['after_snapshot_sha256'],_digest(context['after_snapshot_json'].encode()))
                self.assertFalse(result['active_graph_published']);self.assertFalse(result['correction_ready'])
                self.assertEqual(media.render.call_count,1)
                for p,old in before.items():self.assertEqual((p.read_bytes(),p.stat().st_mtime_ns),old)

    def test_voice_only_preserves_exact_current_captions_and_checkpoint_hash_mtime_without_rerender(self):
        with TemporaryDirectory(prefix='dubflow-current-render-voice-') as directory:
            context,args=fixture(directory,voice_only=True);media,_=self.adapters(args)
            raw=_encode(context,65536);first=render_candidate(raw,**args)
            manifest=json.loads(context['original_manifest_json'])
            for role in ('captions_srt','captions_ass'):
                output=Path(first['artifacts'][role]['path']).read_bytes()
                self.assertEqual(output,Path(context['current_captions'][role]['path']).read_bytes())
                self.assertNotEqual(output,Path(manifest['artifacts'][role]['path']).read_bytes())
            self.assertEqual(Path(first['artifacts']['corrected_timeline']['path']).read_bytes(),context['current_timeline_json'].encode())
            stamps={p:(_digest(p.read_bytes()),p.stat().st_mtime_ns) for p in args['output_root'].rglob('*') if p.is_file()}
            self.assertEqual(render_candidate(raw,**args),first);self.assertEqual(media.render.call_count,1)
            self.assertEqual(stamps,{p:(_digest(p.read_bytes()),p.stat().st_mtime_ns) for p in args['output_root'].rglob('*') if p.is_file()})

    def test_closed_context_and_checksummed_lost_current_caption_refuse_before_media(self):
        with TemporaryDirectory(prefix='dubflow-current-render-refuse-') as directory:
            context,args=fixture(directory,voice_only=True);media,_=self.adapters(args)
            with self.assertRaises(ReviewError):admit_original(_encode(context,65536))
            for mutation in ('counter','tag','prior','archive','authority'):
                bad=deepcopy(context)
                if mutation=='counter':bad['current_token']['revision']='0'
                elif mutation=='tag':bad['kind']='review_native_render_context'
                elif mutation=='prior':
                    timeline=json.loads(bad['current_timeline_json']);timeline['cues'][0]['translated_text']='Xin chào'
                    raw=_encode(timeline,65536);bad.update(current_timeline_json=raw.decode(),current_timeline_sha256=_digest(raw))
                elif mutation=='archive':bad['current_token']['original_manifest_sha256']='sha256:'+'b'*64
                else:bad['active_graph_published']=True
                with self.assertRaises(ReviewError):admit_context(_encode(bad,65536))
            manifest=json.loads(context['original_manifest_json'])
            for role in ('captions_srt','captions_ass'):
                bad=deepcopy(context);path=Path(context['current_captions'][role]['path'])
                original=path.read_bytes();path.write_bytes(Path(manifest['artifacts'][role]['path']).read_bytes())
                bad['current_captions'][role]=fixtures.pin(path)
                with self.assertRaises(ReviewError):render_candidate(_encode(bad,65536),**args)
                path.write_bytes(original)
            self.assertFalse(media.render.called);self.assertEqual(list(args['output_root'].iterdir()),[])

    @unittest.skipUnless(os.environ.get('DUBFLOW_REVIEW_TEST_MEDIA_ROOT'),'explicit owned media QA fixture not selected')
    def test_actual_tiny_current_b1_render_cli_uses_owned_media_and_full_av_decode(self):
        from engine.dubflow.media import MediaProbe
        from engine.dubflow.media.adapter import _WINDOWS_H264_ENCODER
        media_root=Path(os.environ['DUBFLOW_REVIEW_TEST_MEDIA_ROOT'])
        self.assertTrue(media_root.is_absolute(),'explicit owned fixture root must be absolute')
        ffmpeg=media_root/'ffmpeg.exe';ffprobe=media_root/'ffprobe.exe'
        self.assertTrue(ffmpeg.is_file() and ffprobe.is_file(),'owned media fixture runtime required')
        with TemporaryDirectory(prefix='dubflow-current-render-real-') as directory:
            root=Path(directory);source=root/'fixture.mp4'
            encoder=[_WINDOWS_H264_ENCODER,'-hw_encoding','0'] if os.name=='nt' else ['libx264']
            child=subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-f','lavfi','-i',
                'testsrc2=size=320x180:rate=25','-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
                '-t','10','-c:v',*encoder,'-pix_fmt','yuv420p','-c:a','aac','-y',str(source)],capture_output=True,timeout=40)
            self.assertEqual(child.returncode,0,child.stderr)
            probe=MediaProbe(ffprobe,trusted_root=media_root,timeout_seconds=30).probe(source)
            child_root=root/'case';child_root.mkdir()
            context,args=fixture(child_root,b1=True,source_probe=probe,source_bytes=source.read_bytes())
            path=root/'context.json';raw=_encode(context,65536);path.write_bytes(raw)
            inputs={p:(_digest(p.read_bytes()),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
            command=[sys.executable,'-I','-S','-B',str(REPO/'engine/dubflow/review/effective_render_execution.py'),
                '--context',str(path),'--context-sha256',_digest(raw),'--output-root',str(args['output_root']),
                '--ffmpeg',str(ffmpeg),'--ffprobe',str(ffprobe),'--media-root',str(media_root)]
            refused=subprocess.run(command,input=b'\x00',capture_output=True,timeout=20)
            self.assertEqual(refused.returncode,2);self.assertEqual(refused.stdout,b'')
            rendered=subprocess.run(command,input=b'\x01',capture_output=True,timeout=45)
            self.assertEqual(rendered.returncode,0,rendered.stderr);self.assertEqual(rendered.stderr,b'')
            result=json.loads(rendered.stdout)
            qc=json.loads(Path(result['artifacts']['qc_report']['path']).read_bytes())
            self.assertTrue(qc['full_av_decode']);self.assertFalse(qc['active_graph_published'])
            self.assertEqual(qc['output_probe']['streams'][0]['width'],320)
            self.assertEqual(qc['output_probe']['streams'][0]['height'],180)
            self.assertIn('Chào bạn',Path(result['artifacts']['captions_srt']['path']).read_text(encoding='utf-8'))
            self.assertIn('Chúc bạn bình an',Path(result['artifacts']['captions_srt']['path']).read_text(encoding='utf-8'))
            for p,old in inputs.items():self.assertEqual((_digest(p.read_bytes()),p.stat().st_mtime_ns),old)


if __name__=='__main__':unittest.main()

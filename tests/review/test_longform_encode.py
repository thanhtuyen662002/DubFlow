import hashlib
import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from engine.dubflow.review.longform_captions import AssTrack,NO_BURN_HASH
from engine.dubflow.review.longform_encode import encode_chunk,recipe_identity,_seek_seconds
from engine.dubflow.review.longform_plan import plan_chunks,encode_plan,plan_hash
from engine.dubflow.review.production import ReviewError
from test_longform_captions import HEADER,caption
from test_longform_fragments import init,fragment


def pin(raw):return 'sha256:'+hashlib.sha256(raw).hexdigest()
def file_pin(path):return dict(path=str(path),sha256=pin(path.read_bytes()),size_bytes=str(path.stat().st_size))


class LongformEncodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='dubflow-chunk-encode-component-');self.root=Path(self.tmp.name)
        self.source=self.root/'source with spaces.mp4';self.source.write_bytes(b'not actual media; mocked child')
        self.media=self.root/'media';self.media.mkdir()
        self.ffmpeg=self.media/'ffmpeg.exe';self.ffprobe=self.media/'ffprobe.exe'
        self.ffmpeg.write_bytes(b'owned executable fixture');self.ffprobe.write_bytes(b'owned probe fixture')
        self.output=self.root/'output';self.output.mkdir();self.capacities=[]
        self.environment=pin(b'explicit test-only native environment callback fixture')

    def tearDown(self):
        for p in self.root.rglob('*'):
            if p.is_file():p.chmod(stat.S_IREAD|stat.S_IWRITE)
        self.tmp.cleanup()

    def args(self,burn=False):
        ep=[{k:file_pin(p)[k]for k in ('sha256','size_bytes')}for p in (self.ffmpeg,self.ffprobe)]
        recipe=recipe_identity(ffmpeg_pin=ep[0],ffprobe_pin=ep[1],geometry=(96,64),time_base=(1,1000),
            burn_in_subtitles=burn,video_stream_index=0,runtime_environment_sha256=self.environment,deadline=time.monotonic()+20)
        ass=(HEADER+caption('0:00:00.00','0:00:01.00','first')).encode()if burn else None
        dependency=pin(ass)if burn else NO_BURN_HASH
        plan=plan_chunks(iter((3000,3050,3150)),context_sha256=pin(b'context'),source_sha256=file_pin(self.source)['sha256'],
            processing_recipe_sha256=recipe,time_base=(1,1000),start_tick=3000,end_tick=3250,target_ticks=1000,
            visual_dependencies=lambda a,b:dependency)
        raw=encode_plan(plan)
        return dict(raw_plan=raw,expected_plan_sha256=plan_hash(raw),context_sha256=plan['context_sha256'],
            source_pin=file_pin(self.source),source_root=self.root,processing_recipe_sha256=recipe,
            chunk_identity=plan['chunks'][0]['identity'],ffmpeg_path=self.ffmpeg,ffprobe_path=self.ffprobe,
            media_root=self.media,ffmpeg_pin=ep[0],ffprobe_pin=ep[1],output_root=self.output,geometry=(96,64),
            video_stream_index=0,burn_in_subtitles=burn,captions_ass=ass,captions_sha256=pin(ass)if ass else None,
            runtime_environment_sha256=self.environment,runtime_environment_fence=lambda:self.environment,
            capacity=lambda additional:self.capacities.append(additional),max_chunk_bytes=65536,timeout_seconds=20)

    def test_mocked_encoder_pipe_has_fixed_caption_alias_global_then_local_clock_and_exact_candidate(self):
        args=self.args(True);calls=[]
        def process(argv,consume,**bounds):
            calls.append(argv)
            if 'pipe:1'in argv:
                filters=argv[argv.index('-vf')+1]
                self.assertEqual(filters,'trim=start_pts=3000:end_pts=3250,setpts=PTS-3000,subtitles=captions.ass,setpts=PTS-STARTPTS')
                self.assertEqual(argv[argv.index('-ss')+1],'3.000000')
                self.assertIn('-noaccurate_seek',argv)
                self.assertEqual(argv[argv.index('-frames:v')+1],'3')
                self.assertEqual((Path(bounds['cwd'])/'captions.ass').read_bytes(),args['captions_ass'])
                return consume(iter((init()+fragment(),)))
            return consume(iter(()))
        before=file_pin(self.source),self.source.stat().st_mtime_ns
        with patch('engine.dubflow.review.longform_encode._consume_process',side_effect=process):result=encode_chunk(**args)
        self.assertTrue(result['full_video_decode']);self.assertFalse(result['semantic_qc_admitted']);self.assertFalse(result['active_graph_published'])
        self.assertEqual(result['frame_count'],'3');self.assertEqual(file_pin(Path(result['file']['path'])),result['file'])
        self.assertEqual(len(calls),2);self.assertEqual((file_pin(self.source),self.source.stat().st_mtime_ns),before)
        self.assertEqual(self.capacities[0],131072);self.assertEqual(self.capacities[-1],0)
        self.assertFalse(any(p.name.startswith('chunk-encode-')for p in self.output.iterdir()))

    def test_untrusted_recipe_local_caption_or_source_refuses_before_child(self):
        for name in ('recipe','caption','source','environment','stream'):
            args=self.args(True)
            if name=='recipe':args['geometry']=(98,64)
            elif name=='caption':args['captions_ass']=args['captions_ass'].replace(b'first',b'changed');args['captions_sha256']=pin(args['captions_ass'])
            elif name=='source':self.source.write_bytes(b'changed source')
            elif name=='environment':args['runtime_environment_fence']=lambda:pin(b'changed native closure')
            else:args['video_stream_index']=1
            with patch('engine.dubflow.review.longform_encode._consume_process')as child:
                with self.subTest(name=name),self.assertRaises(ReviewError):encode_chunk(**args)
                child.assert_not_called()

    def test_failed_child_output_budget_space_and_post_encode_pin_fences_preserve_source(self):
        for mode in ('exit','budget','source','capacity','environment'):
            args=self.args();before=self.source.read_bytes()
            def process(argv,consume,**bounds):
                if mode=='exit':raise ReviewError('actual child exit failed')
                if mode=='source':self.source.write_bytes(b'changed during encode')
                if mode=='environment':self.environment=pin(b'changed during encode')
                return consume(iter((b'x'*70000 if mode=='budget'else init()+fragment(),)))
            if mode=='capacity':args['capacity']=lambda additional:(_ for _ in ()).throw(ReviewError('native space refusal'))
            with patch('engine.dubflow.review.longform_encode._consume_process',side_effect=process):
                with self.subTest(mode=mode),self.assertRaises(ReviewError):encode_chunk(**args)
            self.assertFalse(any(p.name.startswith('chunk-encode-')for p in self.output.iterdir()))
            if mode!='source':self.assertEqual(self.source.read_bytes(),before)

    def test_seek_hint_is_earlier_rational_microsecond_conversion_and_not_identity(self):
        self.assertEqual(_seek_seconds(1,(1001,30000)),'0.033366')
        self.assertEqual(_seek_seconds(-1,(1001,30000)),'-0.033367')

    def test_scratch_cleanup_refuses_a_replaced_link_without_chmod_or_deleting_its_source(self):
        from engine.dubflow.mix.streaming import MixError
        args=self.args(True);self.source.chmod(stat.S_IREAD)
        before=file_pin(self.source),self.source.stat().st_mtime_ns,self.source.stat().st_mode
        def process(argv,consume,**bounds):
            caption_path=Path(bounds['cwd'])/'captions.ass';caption_path.unlink();os.link(self.source,caption_path)
            return consume(iter((init()+fragment(),)))
        with patch('engine.dubflow.review.longform_encode._consume_process',side_effect=process):
            with self.assertRaises(MixError):encode_chunk(**args)
        self.assertEqual((file_pin(self.source),self.source.stat().st_mtime_ns,self.source.stat().st_mode),before)
        self.assertTrue(any((p/'captions.ass').exists()for p in self.output.iterdir()if p.is_dir()))

    def test_environment_change_invalidates_recipe_and_cannot_be_implicitly_omitted(self):
        args=self.args(True)
        values=dict(ffmpeg_pin=args['ffmpeg_pin'],ffprobe_pin=args['ffprobe_pin'],geometry=(96,64),
            time_base=(1,1000),burn_in_subtitles=True,video_stream_index=0,deadline=time.monotonic()+20)
        changed=recipe_identity(**values,runtime_environment_sha256=pin(b'changed font/codec environment'))
        self.assertNotEqual(changed,args['processing_recipe_sha256'])
        with self.assertRaises(TypeError):recipe_identity(**values)

    @unittest.skipUnless(os.environ.get('DUBFLOW_REVIEW_TEST_MEDIA_ROOT'),'explicit owned media fixture not selected')
    def test_actual_seeked_h264_mf_chunk_keeps_vfr_pts_and_global_caption_visibility(self):
        media=Path(os.environ['DUBFLOW_REVIEW_TEST_MEDIA_ROOT']);ffmpeg=media/'ffmpeg.exe';ffprobe=media/'ffprobe.exe'
        def run(argv):
            p=subprocess.run(argv,capture_output=True,timeout=30);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stderr,b'');return p.stdout
        source=self.root/'new VFR.mkv'
        run([str(ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-f','lavfi','-i','color=c=black:size=320x180:rate=20:duration=3',
             '-vf','select=not(eq(mod(n\\,5)\\,4))+eq(n\\,59),setpts=PTS+3/TB','-fps_mode','passthrough',
             '-enc_time_base','1:1000','-c:v','ffv1','-an','-y',str(source)])
        before={p:(file_pin(p),p.stat().st_mtime_ns)for p in (source,ffmpeg,ffprobe)}
        ep=[{k:file_pin(p)[k]for k in ('sha256','size_bytes')}for p in (ffmpeg,ffprobe)]
        # This explicit Latin/Arial fixture pins the observed font file, not a
        # complete native DirectWrite/Media Foundation dependency closure.
        # Product closure admission and installed rendering remain unqualified.
        font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'arial.ttf'
        font_before=file_pin(font),font.stat().st_mtime_ns
        environment=pin(json.dumps(dict(scope='tiny component fixture, no native environment admission',
            font=font_before),sort_keys=True).encode())
        def environment_fence():
            self.assertEqual((file_pin(font),font.stat().st_mtime_ns),font_before)
            return environment
        ass=(HEADER+caption('0:00:01.25','0:00:01.75','VISIBLE')).encode();track=AssTrack.read(ass,pin(ass))
        recipe=recipe_identity(ffmpeg_pin=ep[0],ffprobe_pin=ep[1],geometry=(320,180),time_base=(1,1000),
            burn_in_subtitles=True,video_stream_index=0,runtime_environment_sha256=environment,deadline=time.monotonic()+30)
        pts=[3000+n*50 for n in range(60)if n%5!=4 or n==59]
        plan=plan_chunks(iter(pts),context_sha256=pin(b'new actual context'),source_sha256=file_pin(source)['sha256'],
             processing_recipe_sha256=recipe,time_base=(1,1000),start_tick=3000,end_tick=6000,target_ticks=1000,
             visual_dependencies=lambda a,b:track.dependency(time_base=(1,1000),source_origin=3000,start_tick=a,end_tick=b))
        raw=encode_plan(plan);chunk=plan['chunks'][1]
        result=encode_chunk(raw,expected_plan_sha256=plan_hash(raw),context_sha256=plan['context_sha256'],source_pin=file_pin(source),
             source_root=self.root,processing_recipe_sha256=recipe,chunk_identity=chunk['identity'],ffmpeg_path=ffmpeg,ffprobe_path=ffprobe,
             media_root=media,ffmpeg_pin=ep[0],ffprobe_pin=ep[1],output_root=self.output,geometry=(320,180),video_stream_index=0,
             runtime_environment_sha256=environment,runtime_environment_fence=environment_fence,
             burn_in_subtitles=True,captions_ass=ass,captions_sha256=pin(ass),capacity=lambda additional:self.capacities.append(additional),timeout_seconds=30)
        probe=json.loads(run([str(ffprobe),'-v','error','-show_frames','-show_entries','frame=pts','-of','json',result['file']['path']]))
        self.assertEqual([f['pts']for f in probe['frames']],[p-4000 for p in pts if 4000<=p<5000])
        # Decode distinct temporal samples: ASS1.25..1.75s must appear at local
        #0.25..0.75s in the second chunk, without editing its event times.
        frames=run([str(ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-i',result['file']['path'],
            '-vf','select=eq(n\\,0)+eq(n\\,8)','-fps_mode','passthrough','-pix_fmt','gray','-f','rawvideo','pipe:1'])
        size=320*180;self.assertEqual(len(frames),size*2)
        self.assertGreater(sum(frames[size:]),sum(frames[:size])+10000)
        for p,value in before.items():self.assertEqual((file_pin(p),p.stat().st_mtime_ns),value)
        evidence=os.environ.get('DUBFLOW_REVIEW_TEST_ENCODE_EVIDENCE')
        if evidence:
            saved=Path(evidence);saved.mkdir() # Explicit new QA directory, never overwrite a prior proof.
            for src,name in ((source,'source-nonzero-vfr.mkv'),(Path(result['file']['path']),'selected-chunk.mp4')):
                shutil.copy2(src,saved/name)
            (saved/'global-captions.ass').write_bytes(ass)
            (saved/'encoded-probe.json').write_text(json.dumps(probe,indent=2),encoding='utf-8')
            (saved/'current-plan.json').write_bytes(raw)
            (saved/'two-decoded-gray-frames.bin').write_bytes(frames)
            from PIL import Image
            for index in (0,1):Image.frombytes('L',(320,180),frames[index*size:(index+1)*size]).save(saved/f'frame-{index}.png')
            (saved/'component-result.json').write_text(json.dumps(dict(candidate=result,
                source_expected_pts=pts,selected_expected_pts=[p-4000 for p in pts if 4000<=p<5000],
                source_and_executable_before={str(p):v for p,v in before.items()},
                source_and_executable_after={str(p):(file_pin(p),p.stat().st_mtime_ns)for p in before},
                font_before=font_before,font_after=(file_pin(font),font.stat().st_mtime_ns),
                decoded_frame_sums=[sum(frames[:size]),sum(frames[size:])],
                native_environment_closure_admitted=False,product_qualified=False),indent=2),encoding='utf-8')


if __name__=='__main__':unittest.main()

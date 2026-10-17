import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import time
import unittest

from engine.dubflow.review.longform_fragments import assemble_fragments, _boxes
from engine.dubflow.review.longform_plan import encode_plan, plan_chunks, plan_hash
from engine.dubflow.review.production import ReviewError


def box(kind,payload):
    return struct.pack('>I4s',len(payload)+8,kind)+payload


def full(kind,payload,flags=0,version=0):
    return box(kind,bytes((version,))+flags.to_bytes(3,'big')+payload)


def pin_bytes(b):
    return 'sha256:'+hashlib.sha256(b).hexdigest()


def pin(path):
    return dict(path=str(path),sha256=pin_bytes(path.read_bytes()),size_bytes=str(path.stat().st_size))


def init(width=96,timescale=1000):
    entry=bytearray(78)
    struct.pack_into('>H',entry,6,1)
    struct.pack_into('>HH',entry,24,width,64)
    avc=box(b'avc1',entry+box(b'avcC',b'fixture decoder metadata; not actual AVC'))
    tables=full(b'stsd',struct.pack('>I',1)+avc)+b''.join(full(k,b'\0'*4)for k in (b'stts',b'stsc',b'stco'))+full(b'stsz',b'\0'*8)
    dinf=box(b'dinf',full(b'dref',struct.pack('>I',1)+full(b'url ',b'',flags=1)))
    mdia=box(b'mdia',full(b'mdhd',struct.pack('>IIII',0,0,timescale,0))+full(b'hdlr',b'\0'*4+b'vide')+
        box(b'minf',dinf+box(b'stbl',tables)))
    trak=box(b'trak',full(b'tkhd',struct.pack('>IIII',0,0,1,0))+mdia)
    return box(b'ftyp',b'isom\0\0\0\0isom')+box(b'moov',trak+box(b'mvex',full(b'trex',struct.pack('>IIIII',1,1,0,0,0))))


def fragment(pts=(0,50,150),*,bad_offset=False,cts=0,default_base=True):
    durations=[b-a for a,b in zip(pts,pts[1:])]+[50]
    mdat=box(b'mdat',b'abcd'*len(pts))
    tfhd=full(b'tfhd',struct.pack('>IIII',1,50,4,0x01010000),flags=0x020038 if default_base else 0x38)
    tfdt=full(b'tfdt',struct.pack('>Q',pts[0]),version=1)
    samples=b''.join(struct.pack('>II',duration,4)+(struct.pack('>I',cts)if cts else b'')for duration in durations)
    flags=0x305|(0x800 if cts else 0)
    def moof(offset):
        return box(b'moof',full(b'mfhd',struct.pack('>I',1))+box(b'traf',tfhd+tfdt+
            full(b'trun',struct.pack('>IiI',len(pts),offset,0x02000000)+samples,flags=flags)))
    raw=moof(0)
    return moof(999 if bad_offset else len(raw)+8)+mdat


class LongformFragmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='dubflow-fragment-component-')
        self.root=Path(self.tmp.name)
        self.inputs=self.root/'retained';self.inputs.mkdir()
        self.output=self.root/'candidate';self.output.mkdir()
        self.capacity=[]

    def tearDown(self):
        # Only fixture-owned new files below this TemporaryDirectory.
        for p in self.root.rglob('*'):
            if p.is_file():p.chmod(p.stat().st_mode|stat.S_IWUSR)
        self.tmp.cleanup()

    def args(self,*,origin=-100):
        pts=[origin+i for i in (0,50,150,250,300,400)]
        plan=plan_chunks(iter(pts),context_sha256=pin_bytes(b'context'),source_sha256=pin_bytes(b'source'),
            processing_recipe_sha256=pin_bytes(b'exact recipe'),time_base=(1,1000),start_tick=origin,end_tick=origin+500,
            target_ticks=250,visual_dependencies=lambda a,b:pin_bytes(b'local visual'))
        files=[]
        for i in range(2):
            path=self.inputs/f'chunk{i}.mp4';path.write_bytes(init()+fragment());path.chmod(stat.S_IREAD)
            files.append(pin(path))
        return dict(raw_plan=encode_plan(plan),expected_plan_sha256=plan_hash(encode_plan(plan)),
            context_sha256=plan['context_sha256'],source_sha256=plan['source_sha256'],
            processing_recipe_sha256=plan['processing_recipe_sha256'],inputs=tuple(files),input_root=self.inputs,
            output_root=self.output,geometry=(96,64),capacity=lambda remaining:self.capacity.append(remaining))

    def change_input(self,args,index,raw):
        p=Path(args['inputs'][index]['path']);p.chmod(stat.S_IREAD|stat.S_IWRITE);p.write_bytes(raw);p.chmod(stat.S_IREAD)
        inputs=list(args['inputs']);inputs[index]=pin(p);args['inputs']=tuple(inputs)

    def test_source_clock_fields_derive_from_signed_origin_and_exact_next_boundary_without_reencoding(self):
        args=self.args();before={p: (pin(p),p.stat().st_mtime_ns)for p in self.inputs.iterdir()}
        result=assemble_fragments(**args)
        data=Path(result['path']).read_bytes();self.assertEqual(result['sha256'],pin_bytes(data))
        self.assertFalse(result['semantic_qc_admitted']);self.assertFalse(result['active_graph_published'])
        self.assertEqual(result['source_origin_tick'],'-100');self.assertEqual(result['frame_count'],'6')
        bases=[];sequences=[];durations=[]
        for top in _boxes(data):
            if top.kind==b'moof':
                for child in _boxes(data,top.payload,top.end):
                    if child.kind==b'mfhd':sequences.append(struct.unpack_from('>I',data,child.payload+4)[0])
                    if child.kind==b'traf':
                        for item in _boxes(data,child.payload,child.end):
                            if item.kind==b'tfdt':bases.append(struct.unpack_from('>Q',data,item.payload+4)[0])
                            if item.kind==b'trun':durations.append(struct.unpack_from('>I',data,item.end-8)[0])
        self.assertEqual(bases,[0,250]);self.assertEqual(sequences,[1,2]);self.assertEqual(durations,[100,100])
        self.assertEqual(self.capacity[-1],0)
        for p,expected in before.items():self.assertEqual((pin(p),p.stat().st_mtime_ns),expected)

    def test_incompatible_initialization_geometry_or_track_clock_refuses_before_candidate(self):
        for raw in (init(width=98)+fragment(),init(timescale=90000)+fragment()):
            args=self.args();self.change_input(args,1,raw)
            with self.assertRaises(ReviewError):assemble_fragments(**args)
            self.assertEqual(list(self.output.iterdir()),[])
            for p in self.inputs.iterdir():p.chmod(stat.S_IREAD|stat.S_IWRITE)

    def test_encoded_frame_clock_payload_offsets_reordering_and_default_base_are_exact(self):
        for raw in (init()+fragment((0,60,150)),init()+fragment(bad_offset=True),init()+fragment(cts=1),
                    init()+fragment(default_base=False),init()+fragment()+b'foreign'):
            args=self.args();self.change_input(args,0,raw)
            with self.assertRaises(ReviewError):assemble_fragments(**args)
            self.assertEqual(list(self.output.iterdir()),[])
            for p in self.inputs.iterdir():p.chmod(stat.S_IREAD|stat.S_IWRITE)

    def test_writable_missing_changed_or_foreign_physical_inputs_refuse(self):
        args=self.args();p=Path(args['inputs'][0]['path']);p.chmod(stat.S_IREAD|stat.S_IWRITE)
        with self.assertRaises(ReviewError):assemble_fragments(**args)
        p.write_bytes(b'changed')
        with self.assertRaises(ReviewError):assemble_fragments(**args)
        p.unlink()
        with self.assertRaises((ReviewError,OSError)):assemble_fragments(**args)
        args['inputs']=()
        with self.assertRaises(ReviewError):assemble_fragments(**args)

    def test_space_refusal_after_copy_begins_preserves_inputs_and_removes_only_new_candidate(self):
        args=self.args();before={p: (pin(p),p.stat().st_mtime_ns)for p in self.inputs.iterdir()}
        calls=0
        def capacity(remaining):
            nonlocal calls
            calls+=1
            if calls>=2:raise ReviewError('native capacity refused')
        args['capacity']=capacity
        with self.assertRaisesRegex(ReviewError,'native capacity'):assemble_fragments(**args)
        self.assertEqual(list(self.output.iterdir()),[])
        for p,value in before.items():self.assertEqual((pin(p),p.stat().st_mtime_ns),value)

    def test_cancelled_and_unpinned_plan_never_create_output(self):
        args=self.args();args['cancelled']=lambda:True
        with self.assertRaises(ReviewError):assemble_fragments(**args)
        args['cancelled']=lambda:False;args['expected_plan_sha256']=pin_bytes(b'foreign')
        with self.assertRaises(ReviewError):assemble_fragments(**args)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_wide_source_origin_never_passes_through_float_and_extra_track_boxes_refuse(self):
        args=self.args(origin=2**53+17)
        result=assemble_fragments(**args);self.assertEqual(result['source_origin_tick'],str(2**53+17))
        self.change_input(args,0,init()+fragment()+box(b'unknown',b'data'))
        with self.assertRaises(ReviewError):assemble_fragments(**args)

    def test_later_fragment_metadata_and_sample_offsets_are_bounded_before_copy(self):
        from unittest.mock import patch
        args=self.args()
        with patch('engine.dubflow.review.longform_fragments.MAX_METADATA',1):
            with self.assertRaisesRegex(ReviewError,'metadata'):assemble_fragments(**args)
        self.assertEqual(list(self.output.iterdir()),[])
        raw=bytearray(init()+fragment())
        position=raw.index(b'trun')-4
        # Preserve the rest of the box structure but declare too many samples.
        struct.pack_into('>I',raw,position+12,100)
        self.change_input(args,0,raw)
        with self.assertRaisesRegex(ReviewError,'sample count'):assemble_fragments(**args)

    def test_nonunit_reduced_clock_rescales_source_ticks_with_integer_arithmetic(self):
        origin=2**53+17
        plan=plan_chunks(iter((origin,origin+1,origin+3)),context_sha256=pin_bytes(b'rational context'),
            source_sha256=pin_bytes(b'source'),processing_recipe_sha256=pin_bytes(b'recipe'),time_base=(1001,30000),
            start_tick=origin,end_tick=origin+5,target_ticks=5,visual_dependencies=lambda a,b:pin_bytes(b'visual'))
        path=self.inputs/'rational.mp4';path.write_bytes(init(timescale=30000)+fragment((0,1001,3003)));path.chmod(stat.S_IREAD)
        raw=encode_plan(plan)
        result=assemble_fragments(raw,expected_plan_sha256=plan_hash(raw),**{k:plan[k]for k in
            ('context_sha256','source_sha256','processing_recipe_sha256')},inputs=(pin(path),),input_root=self.inputs,
            output_root=self.output,geometry=(96,64),capacity=lambda remaining:None)
        self.assertEqual(result['output_time_base'],dict(numerator='1',denominator='30000'))
        self.assertEqual(result['source_origin_tick'],str(origin))

    def test_initialization_rejects_external_data_reference_and_nonempty_sample_table(self):
        for field in ('reference','samples'):
            args=self.args();raw=bytearray(init()+fragment())
            if field=='reference':
                p=raw.index(b'url ');raw[p+7]=0
            else:
                p=raw.index(b'stts');raw[p+11]=1
            self.change_input(args,0,raw)
            with self.subTest(field=field),self.assertRaises(ReviewError):assemble_fragments(**args)
            self.assertEqual(list(self.output.iterdir()),[])
            for p in self.inputs.iterdir():p.chmod(stat.S_IREAD|stat.S_IWRITE)

    @unittest.skipUnless(os.environ.get('DUBFLOW_REVIEW_TEST_MEDIA_ROOT'),'explicit owned media fixture not selected')
    def test_actual_three_h264_mf_fragments_keep_all_vfr_frame_pts_across_sparse_seams(self):
        media=Path(os.environ['DUBFLOW_REVIEW_TEST_MEDIA_ROOT']);ffmpeg=media/'ffmpeg.exe';ffprobe=media/'ffprobe.exe'
        exe_pins={p: (pin(p),p.stat().st_mtime_ns)for p in (ffmpeg,ffprobe)}
        source=self.root/'source VFR [spaces].mkv'
        def run(args):
            p=subprocess.run(args,capture_output=True,timeout=30)
            self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stderr,b'');return p.stdout
        run([str(ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-f','lavfi','-i',
            'testsrc2=size=320x180:rate=20:duration=3','-vf',
            'select=not(eq(mod(n\\,5)\\,4))+eq(n\\,59),setpts=PTS+3/TB','-fps_mode','passthrough',
            '-enc_time_base','1:1000','-c:v','ffv1','-an','-y',str(source)])
        source_pin=(pin(source),source.stat().st_mtime_ns)
        expected=[3000+n*50 for n in range(60)if n%5!=4 or n==59]
        plan=plan_chunks(iter(expected),context_sha256=pin_bytes(b'new fixture context'),source_sha256=pin(source)['sha256'],
            processing_recipe_sha256=pin_bytes(b'explicit fixture encoder recipe'),time_base=(1,1000),
            start_tick=3000,end_tick=6000,target_ticks=1000,visual_dependencies=lambda a,b:pin_bytes(b'no burn in yet'))
        files=[]
        for i,chunk in enumerate(plan['chunks']):
            path=self.inputs/f'new chunk {i}.mp4'
            run([str(ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-copyts','-i',str(source),
                '-map','0:v:0','-vf',f"trim=start_pts={chunk['start_tick']}:end_pts={chunk['end_tick']},setpts=PTS-3000",
                '-fps_mode','passthrough','-enc_time_base','1:1000','-c:v','h264_mf','-hw_encoding','0',
                '-quality','90','-bf','0','-pix_fmt','yuv420p','-an','-sn','-video_track_timescale','1000',
                '-avoid_negative_ts','disabled','-movflags','+empty_moov+frag_keyframe+default_base_moof','-y',str(path)])
            path.chmod(stat.S_IREAD);files.append(pin(path))
        before={Path(p['path']):(p,Path(p['path']).stat().st_mtime_ns)for p in files}
        raw=encode_plan(plan)
        result=assemble_fragments(raw,expected_plan_sha256=plan_hash(raw),**{k:plan[k]for k in
            ('context_sha256','source_sha256','processing_recipe_sha256')},inputs=tuple(files),input_root=self.inputs,
            output_root=self.output,geometry=(320,180),capacity=lambda remaining:self.capacity.append(remaining))
        probe=json.loads(run([str(ffprobe),'-v','error','-show_frames','-show_streams','-show_entries',
            'frame=pts:stream=codec_name,time_base,width,height','-of','json',result['path']]))
        self.assertEqual([f['pts']for f in probe['frames']],[p-3000 for p in expected])
        self.assertEqual(probe['streams'][0]['codec_name'],'h264');self.assertEqual(probe['streams'][0]['time_base'],'1/1000')
        self.assertEqual((probe['streams'][0]['width'],probe['streams'][0]['height']),(320,180))
        run([str(ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-xerror','-i',result['path'],'-map','0:v:0','-f','null','-'])
        self.assertEqual((pin(source),source.stat().st_mtime_ns),source_pin)
        for p,value in {**exe_pins,**before}.items():self.assertEqual((pin(p),p.stat().st_mtime_ns),value)
        self.assertEqual(result['frame_count'],'49')


if __name__=='__main__':unittest.main()

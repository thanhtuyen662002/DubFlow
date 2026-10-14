from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import struct
import sys
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.review.corrected_timeline import derive_corrected_timeline,derive_effective_timeline
from engine.dubflow.review.effective_timeline_execution import prepare_timeline
from engine.dubflow.review.production import ReviewError,RegenerationPlan,_correction_stages
from engine.dubflow.review.synthesis_input import _digest,_encode
import test_corrected_timeline as fixtures
from schema_assertions import assert_schema

REPO=Path(__file__).resolve().parents[2]
NATIVE_CASES=[]


class EffectiveTimelineTests(unittest.TestCase):
    def fixture(self,directory,*,b1=False,revision=2,voice_only=False):
        raw,origin,store,_=fixtures.CorrectedTimelineTests().fixture(directory,b1=b1,time_base=('1','48000'))
        original=json.loads(raw)
        original['cues'][0]['confidence']=0.9838564395904541
        original['cues'][2]['confidence']=-0.0
        raw=_encode(original,65536)
        first,_=store.commit_correction(origin,'risk-a',translation='Chào bạn')
        current=derive_corrected_timeline(raw,origin,first)
        before=replace(first,revision=revision)
        selected=before.findings[0 if voice_only else 2]
        updated=replace(selected,voice='voice-next') if voice_only else replace(selected,translated_text='Chúc bạn bình an')
        stages=_correction_stages(translation_changed=selected.translated_text!=updated.translated_text,
            voice_changed=selected.voice!=updated.voice,has_voice=selected.voice is not None)
        after=replace(before,revision=revision+1,findings=tuple(updated if f.finding_id==selected.finding_id else
            replace(f,translated_text=updated.translated_text,voice=updated.voice) if f.cue_id==selected.cue_id else f
            for f in before.findings),last_plan=RegenerationPlan(selected.finding_id,stages,('asr','ocr'),tuple('replacement:'+s for s in stages)))
        token={'job_id':before.job_id,'revision':str(revision),'snapshot_sha256':_digest(_encode(before.to_dict(),65536)),
            'graph_sha256':'sha256:'+'b'*64,'original_manifest_sha256':'sha256:'+'c'*64}
        context={'schema_version':1,'kind':'review_native_effective_timeline_context','mode':'b1' if b1 else 'b2',
            'current_token':token,'origin_snapshot_json':_encode(origin.to_dict(),65536).decode(),
            'before_snapshot_json':_encode(before.to_dict(),65536).decode(),'after_snapshot_json':_encode(after.to_dict(),65536).decode(),
            'original_timeline_json':raw.decode(),'original_timeline_sha256':_digest(raw),
            'current_timeline_json':current.decode(),'current_timeline_sha256':_digest(current)}
        return context,raw,origin,current,before,after

    def test_current_b1_b2_cumulative_timeline_preserves_prior_edit_unwarned_cues_clock_and_confidence(self):
        with TemporaryDirectory(prefix='dubflow-effective-timeline-') as directory:
            for b1 in (True,False):
                context,raw,origin,current,before,after=self.fixture(Path(directory)/str(b1),b1=b1)
                packet=prepare_timeline(_encode(context,65536))
                assert_schema(context,'native-effective-timeline-context-v1.json')
                assert_schema(packet,'prepared-effective-timeline-v1.json')
                result=json.loads(packet['corrected_timeline_json']);old=json.loads(raw)
                self.assertEqual(result['cues'][0]['translated_text'],'Chào bạn')
                self.assertEqual(result['cues'][1]['translated_text'],'Chúc bạn bình an')
                self.assertEqual(result['cues'][2],old['cues'][2]);self.assertEqual(result['source_clock_mapping'],old['source_clock_mapping'])
                self.assertEqual([c['confidence'] for c in result['cues']],[c['confidence'] for c in old['cues']])
                self.assertEqual([struct.pack('!d',c['confidence']) for c in result['cues']],
                    [struct.pack('!d',c['confidence']) for c in old['cues']])
                self.assertEqual(packet['current_token']['revision'],'2');self.assertEqual(after.revision,3)
                self.assertFalse(packet['active_graph_published']);self.assertFalse(packet['model_weights_verified'])
                self.assertEqual(json.loads(derive_effective_timeline(raw,origin,current,before,after)),result)
                NATIVE_CASES.append({'mode':context['mode'],'origin':origin.to_dict(),'before':before.to_dict(),
                    'after':after.to_dict(),'token':context['current_token'],'original_json':raw.decode(),
                    'current_json':current.decode(),'expected_json':packet['corrected_timeline_json']})

    def test_voice_only_keeps_current_caption_bytes_and_wide_public_counter_without_normalization(self):
        with TemporaryDirectory(prefix='dubflow-effective-timeline-voice-') as directory:
            context,raw,origin,current,before,after=self.fixture(directory,revision=2**53+19,voice_only=True)
            packet=prepare_timeline(_encode(context,65536))
            assert_schema(context,'native-effective-timeline-context-v1.json')
            assert_schema(packet,'prepared-effective-timeline-v1.json')
            self.assertEqual(packet['corrected_timeline_json'].encode(),current)
            self.assertNotEqual(current,raw)
            self.assertEqual(packet['current_token']['revision'],str(2**53+19))
            self.assertEqual(after.revision,2**53+20)
            NATIVE_CASES.append({'mode':'b2','origin':origin.to_dict(),'before':before.to_dict(),
                'after':after.to_dict(),'token':context['current_token'],'original_json':raw.decode(),
                'current_json':current.decode(),'expected_json':packet['corrected_timeline_json']})

    def test_checksums_do_not_allow_source_clock_confidence_unwarned_cue_or_prior_edit_substitution(self):
        with TemporaryDirectory(prefix='dubflow-effective-timeline-refuse-') as directory:
            context,raw,origin,current,before,after=self.fixture(directory)
            for field in ('confidence','signed_zero','source_clock','unwarned','prior'):
                changed=json.loads(current)
                if field=='confidence':changed['cues'][0]['confidence']=0.9
                elif field=='signed_zero':changed['cues'][2]['confidence']=0.0
                elif field=='source_clock':changed['source_clock_mapping']['start_pts']='9001'
                elif field=='unwarned':changed['cues'][2]['translated_text']='Lost unwarned cue'
                else:changed['cues'][0]['translated_text']='Xin chào'
                bad=_encode(changed,65536)
                with self.assertRaises(ReviewError):derive_effective_timeline(raw,origin,bad,before,after)
            for kind in ('counter','mode','origin','extra'):
                changed=deepcopy(context)
                if kind=='counter':changed['current_token']['revision']='0'
                elif kind=='mode':changed['mode']='b1'
                elif kind=='origin':
                    value=json.loads(changed['origin_snapshot_json']);value['revision']=2;changed['origin_snapshot_json']=_encode(value,65536).decode()
                else:changed['trusted_by_client']=True
                with self.assertRaises(ReviewError):prepare_timeline(_encode(changed,65536))

    def test_isolated_byte_gated_current_timeline_subprocess_without_touching_inputs(self):
        with TemporaryDirectory(prefix='dubflow-effective-timeline-cli-') as directory:
            context,_,_,_,_,_=self.fixture(directory,b1=True)
            raw=_encode(context,65536);path=Path(directory)/'context.json';path.write_bytes(raw)
            pins={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in Path(directory).rglob('*') if p.is_file()}
            command=[sys.executable,'-I','-S','-B',str(REPO/'engine/dubflow/review/effective_timeline_execution.py'),
                '--context',str(path),'--context-sha256',_digest(raw)]
            for gate,code in ((b'\x00',2),(b'\x01',0)):
                result=subprocess.run(command,input=gate,capture_output=True,timeout=15)
                self.assertEqual(result.returncode,code,result.stderr)
                if code==0:self.assertEqual(json.loads(result.stdout)['mode'],'b1')
                else:self.assertEqual(result.stdout,b'')
            for p,old in pins.items():self.assertEqual((p.read_bytes(),p.stat().st_mtime_ns),old)


if __name__=='__main__':unittest.main()

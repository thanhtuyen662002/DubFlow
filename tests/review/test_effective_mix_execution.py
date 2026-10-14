"""Tiny real streamed DSP with synthetic speech and simulated native pins.

No source-matched supervisor, real inference, SQL publication or GUI is tested.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.mix import FileSource, MixConfig, ResourceProfile, streaming
from engine.dubflow.review.audio_composition import PinnedDocument,compose_audio
from engine.dubflow.review.cue_execution import candidate_packet,accepted_packet
from engine.dubflow.review.effective_cue_execution import prepare_bound_cue
from engine.dubflow.review.effective_mix_execution import prepare_composition,produce_mix
from engine.dubflow.review.mix_io import ReviewStreamingAudioMixer
from engine.dubflow.review.synthesis_input import _encode,_digest,read_cue_synthesis_input
from engine.dubflow.review.synthesis_worker import synthesize_cue_candidate
from engine.dubflow.tts import LocalTtsAdapter,vieneu
from schema_assertions import assert_schema
import test_effective_cue_plan as helpers
import test_synthesis_worker as workers

NATIVE_FIXTURE=None
ENTRY=helpers.REPO/'engine/dubflow/review/effective_mix_execution.py'


class EffectiveMixTests(unittest.TestCase):
    def fixture(self,directory):
        root=Path(directory);helper=helpers.EffectiveCuePlanTests()
        original,content=helper.fixture(directory,first_voice=True)
        source_path=root/'source48-stereo.wav'
        source_path.write_bytes(helpers.inputs.helpers.make_wave([1234]*96000,sample_rate=48000,channels=2))
        source=FileSource(source_path,original.source.start,original.source.end,source_id='fixture-source',layout='stereo-source')
        config=MixConfig(requested_profile='cpu',resource=ResourceProfile(max_threads=1,max_memory_mb=512,max_batch_items=8))
        template_root=root/'stream-template'
        mixed=ReviewStreamingAudioMixer(config=config,output_dir=template_root,generation_root=template_root).mix(
            source,tuple(cue.segment for cue in original.cues),input_hash=original.manifest_sha256)
        template_path=root/'stream-template-document.json';template_path.write_text(mixed.to_json(),encoding='utf-8')
        template=PinnedDocument(template_path,_digest(template_path.read_bytes()),template_path.stat().st_size,root)
        original_inputs=tuple(c.canonical_input for c in original.cues)
        docs={c.canonical_input.segment_id:replace(c.document,metadata_root=c.document.path.parent) for c in content.cues}
        content=compose_audio(content.snapshot,manifest_sha256=content.manifest_sha256,source=source,
            source_sha256=_digest(source_path.read_bytes()),canonical_inputs=tuple(c.canonical_input for c in content.cues),
            assignments=docs,mix_template=template)
        current=replace(content,snapshot=replace(content.snapshot,revision=2))
        proposal=helper.proposal(current,index=1,text='Người tôi vô cùng trân trọng.')
        prep=helper.context(current,proposal)
        composition_raw=_encode(content.to_dict(),65536);snapshot_raw=_encode(content.snapshot.to_dict(),65536)
        prep['current_composition_sha256']=_digest(composition_raw)
        prep['composition_snapshot_sha256']=_digest(snapshot_raw)
        bindings={'schema_version':1,'kind':'review_native_effective_cue_bindings','current_token':prep['before_token'],
            'content_composition_json':composition_raw.decode(),'content_composition_sha256':_digest(composition_raw),
            'composition_snapshot_json':snapshot_raw.decode(),'composition_snapshot_sha256':_digest(snapshot_raw),
            'source_audio':{'path':str(source_path),'sha256':content.source_sha256,'size_bytes':source_path.stat().st_size,'artifact_root':str(root)},
            'canonical_inputs':[i.to_dict() for i in original_inputs],
            'documents':[pin.to_dict() for pin in sorted(docs.values(),key=lambda p:p.sha256)],'mix_template':template.to_dict()}
        prep_raw=_encode(prep,65536);bindings_raw=_encode(bindings,65536)
        prepared=prepare_bound_cue(prep_raw,bindings_raw,app_root=helpers.REPO,profile_path=helpers.PROFILE)
        request=read_cue_synthesis_input(prepared['request_json'].encode(),expected_hash=prepared['request_sha256'])
        candidate=synthesize_cue_candidate(request.encoded,expected_hash=request.input_hash,output_root=root/'private-speech',
            engine_factory=lambda _:workers.TrackingEngine(),recipe_identity='a'*64)
        packet=candidate_packet(request,candidate,'a'*64);artifact=candidate.document.artifacts[0]
        audio_root=root/'simulated-native-output';audio_root.mkdir()
        audio=audio_root/(artifact.content_hash[7:]+'.wav')
        audio.write_bytes(Path(artifact.path).read_bytes());audio.chmod(stat.S_IRUSR)
        accepted_wave={'path':str(audio),'root':str(audio_root),'sha256':artifact.content_hash,'size_bytes':audio.stat().st_size,
            'sample_rate':artifact.sample_rate,'channels':artifact.channels,'frame_count':artifact.frame_count}
        acceptance={'schema_version':1,'kind':'review_native_accepted_cue_context','request_json':request.encoded.decode(),
            'request_sha256':request.input_hash,'candidate_tts_json':packet['tts_document_json'],
            'candidate_spoken_text_json':packet['spoken_text_json'],'recipe_identity':'a'*64,'accepted_wave':accepted_wave}
        accepted=accepted_packet(_encode(acceptance,65536))
        metadata=root/'simulated-sealed-metadata';metadata.mkdir()
        def seal(raw):
            pin=_digest(raw);path=metadata/(pin[7:]+'.json');path.write_bytes(raw)
            return {'path':str(path),'sha256':pin,'size_bytes':len(raw)}
        tts=seal(accepted['tts_document_json'].encode());spoken=seal(accepted['spoken_text_json'].encode())
        tts_pin={**tts,'artifact_root':str(audio_root),'metadata_root':str(metadata)}
        context={'schema_version':1,'kind':'review_native_effective_mix_context',
            'preparation_context_json':prep_raw.decode(),'preparation_context_sha256':_digest(prep_raw),
            'bindings_json':bindings_raw.decode(),'bindings_sha256':_digest(bindings_raw),
            'request_json':request.encoded.decode(),'request_sha256':request.input_hash,
            'accepted_tts_document':tts_pin,'accepted_spoken_text':spoken,'accept_context':seal(_encode(acceptance,65536))}
        projection=json.loads(prepared['effective_composition_json'])
        document_values={pin.sha256:json.loads(pin.read()) for pin in docs.values()}
        document_values[tts['sha256']]=json.loads(accepted['tts_document_json'])
        before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
        return context,current,proposal,before,{'current':projection,'request':json.loads(request.encoded),
            'previous_provenance':content.cues[1].provenance.to_dict(),'before':current.snapshot.to_dict(),
            'after':proposal.to_dict(),'voice':request.voice.to_dict(),'config':request.config.to_dict(),
            'accepted_tts':tts_pin,'documents':document_values,'template':mixed.to_dict(),'token':prepared['before_token']}

    def preserve(self,pins):
        for path,pin in pins.items():self.assertEqual((path.read_bytes(),path.stat().st_mtime_ns),pin)

    def clean_permissions(self,root):
        for path in (Path(root)/'simulated-native-output').glob('*.wav'):path.chmod(stat.S_IRUSR|stat.S_IWUSR)

    def test_actual_stream_mix_retains_prior_voice_other_cue_and_all_source_pins(self):
        global NATIVE_FIXTURE
        with TemporaryDirectory(prefix='dubflow-effective-stream-') as directory:
            context,current,proposal,pins,native=self.fixture(directory)
            try:
                raw=_encode(context,65536)
                with (patch.object(LocalTtsAdapter,'synthesize',side_effect=AssertionError('mix reran TTS')),
                    patch.object(vieneu,'load_vieneu_voice',side_effect=AssertionError('mix loaded models'))):
                    composition,token,_=prepare_composition(raw,app_root=helpers.REPO,profile_path=helpers.PROFILE)
                    packet=produce_mix(raw,output_root=Path(directory)/'current-mix',app_root=helpers.REPO,profile_path=helpers.PROFILE)
                self.assertEqual(token['revision'],'2');self.assertEqual(proposal.revision,3)
                self.assertEqual(composition.cues[0].canonical_input,current.cues[0].canonical_input)
                self.assertEqual(composition.cues[0].provenance.voice_id,'vi-hai-dang-vieneu3-v1')
                self.assertEqual(composition.cues[0].document,current.cues[0].document)
                mixed=json.loads(packet['mix_document_json'])
                self.assertEqual((mixed['sample_rate'],mixed['channels']),(48000,2))
                self.assertEqual(mixed['original_audio']['content_hash'],current.source_sha256)
                self.assertEqual(mixed['original_audio']['frame_count'],96000)
                self.assertFalse(packet['active_graph_published']);self.preserve(pins)
                assert_schema(json.loads(packet['composition_json']),'audio-composition-v2.json')
                NATIVE_FIXTURE={**native,'packet':packet,'scope':'SYNTHETIC_PYTHON_DSP_METADATA_NOT_NATIVE_ADMISSION'}
            finally:self.clean_permissions(directory)

    def test_completed_stream_resume_preserves_three_pcm_files_without_recomputing(self):
        with TemporaryDirectory(prefix='dubflow-effective-stream-resume-') as directory:
            context,_,_,pins,_=self.fixture(directory)
            try:
                raw=_encode(context,65536);args={'output_root':Path(directory)/'current-mix','app_root':helpers.REPO,'profile_path':helpers.PROFILE}
                first=produce_mix(raw,**args);mixed=json.loads(first['mix_document_json'])
                media={Path(mixed[r]['path']):(Path(mixed[r]['path']).read_bytes(),Path(mixed[r]['path']).stat().st_mtime_ns)
                    for r in ('original_audio','dialogue_stem','final_mix')}
                with patch.object(streaming.StreamingAudioMixer,'_compute',side_effect=AssertionError('completed mix recomputed')) as compute:
                    second=produce_mix(raw,**args)
                self.assertEqual(compute.call_count,0);self.assertEqual(first,second)
                self.preserve(media);self.preserve(pins)
            finally:self.clean_permissions(directory)

    def test_current_token_request_and_accepted_speech_substitution_refuse_before_mix_output(self):
        with TemporaryDirectory(prefix='dubflow-effective-stream-refuse-') as directory:
            context,_,_,pins,_=self.fixture(directory)
            try:
                changes=[]
                changed=deepcopy(context);changed['request_sha256']='sha256:'+'b'*64;changes.append(changed)
                changed=deepcopy(context);value=json.loads(changed['bindings_json']);value['current_token']['revision']='0'
                changed['bindings_json']=_encode(value,65536).decode();changed['bindings_sha256']=_digest(changed['bindings_json'].encode());changes.append(changed)
                changed=deepcopy(context);changed['accepted_spoken_text']['sha256']='sha256:'+'c'*64;changes.append(changed)
                changed=deepcopy(context);changed['accepted_tts_document']['artifact_root']=str(Path(directory)/'other');changes.append(changed)
                for changed in changes:
                    with self.assertRaises(ValueError):produce_mix(_encode(changed,65536),output_root=Path(directory)/'refused',
                        app_root=helpers.REPO,profile_path=helpers.PROFILE)
                self.assertFalse((Path(directory)/'refused').exists());self.preserve(pins)
            finally:self.clean_permissions(directory)

    def test_owned_isolated_actual_mix_and_byte_gate_without_native_or_model_execution(self):
        with TemporaryDirectory(prefix='dubflow-effective-stream-cli-') as directory:
            context,_,_,pins,_=self.fixture(directory)
            try:
                path=Path(directory)/'context.json';raw=_encode(context,65536);path.write_bytes(raw)
                command=[sys.executable,'-I','-S','-B',str(ENTRY),'--context',str(path),'--context-sha256',_digest(raw),
                    '--output-root',str(Path(directory)/'cli-mix'),'--app-root',str(helpers.REPO),'--profile',str(helpers.PROFILE)]
                refused=subprocess.run(command,input=b'\x00',capture_output=True,timeout=20)
                self.assertEqual(refused.returncode,2);self.assertFalse((Path(directory)/'cli-mix').exists())
                result=subprocess.run(command,input=b'\x01',capture_output=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stderr,b'')
                packet=json.loads(result.stdout);self.assertEqual(packet['before_token']['revision'],'2')
                self.assertEqual(json.loads(packet['mix_document_json'])['original_audio']['frame_count'],96000)
                self.preserve(pins)
            finally:self.clean_permissions(directory)


if __name__=='__main__':unittest.main()

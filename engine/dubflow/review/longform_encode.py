"""Private bounded H264 software chunk encoder with global ASS window clocks.

Native must admit source/probe/processing release/plan/locators/quota before
dispatch and independently retain/validate the result. No worker/model/runtime
repin, current graph mutation or capability activation is performed here.
"""
import copy
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import time

from .longform_captions import AssTrack,NO_BURN_HASH
from .longform_plan import _integer,encode_plan,plan_hash,read_plan
from .longform_probe import _consume_process
from .production import ReviewError
from .render_execution import _closed,_file_hash,_path,_size,_verify

MAX_CHUNK_BYTES=256*1024*1024
RECIPE='review-h264-mf-software-global-ass-chunk-v1'


def recipe_identity(*,ffmpeg_pin,ffprobe_pin,geometry,time_base,burn_in_subtitles,
                    video_stream_index,runtime_environment_sha256,deadline):
    from .longform_plan import _clock
    from .longform_plan import _hash
    _hash(runtime_environment_sha256)
    _integer(video_stream_index,'chunk recipe video stream',0,1023)
    if type(burn_in_subtitles)is not bool or not isinstance(geometry,tuple)or len(geometry)!=2:
        raise ReviewError('chunk recipe needs native geometry and burn-in choice')
    for x in geometry:_integer(x,'chunk geometry',1,65535)
    _clock(time_base,0,1)
    for pin in (ffmpeg_pin,ffprobe_pin):
        _closed(pin,('sha256','size_bytes'));_size(pin['size_bytes'])
        _hash(pin['sha256'])
    sources={}
    for name in ('longform_encode.py','longform_captions.py','longform_fragments.py','longform_plan.py',
                 'longform_clock.py','longform_probe.py','render_execution.py','synthesis_worker.py'):
        sources[name]=_file_hash(Path(__file__).with_name(name),deadline)[0]
    value=dict(schema_version=1,kind='review_private_chunk_encoder_recipe',recipe=RECIPE,
        ffmpeg=ffmpeg_pin,ffprobe=ffprobe_pin,geometry=list(geometry),time_base=list(time_base),
        runtime_environment_sha256=runtime_environment_sha256,
        video_stream_index=video_stream_index,
        burn_in_subtitles=burn_in_subtitles,encoder='h264_mf',hw_encoding=0,quality=90,b_frames=0,
        pixel_format='yuv420p',timestamps='source_pts_trim_global_ass_then_local_origin',
        caption_window='review_ass_global_window_v1',fragment_layout='empty_moov_frag_keyframe_default_base_moof',sources=sources)
    return plan_hash(encode_plan(value))


def _seek_seconds(tick,time_base):
    # ffmpeg seeks in microseconds. Round toward earlier time; seek is only an
    # optimization. Exact integer trim + encoded frame-clock checks decide reuse.
    n,d=time_base
    value=tick*n*1000000//d
    sign='-'if value<0 else'';value=abs(value)
    return f'{sign}{value//1000000}.{value%1000000:06d}'


def encode_chunk(raw_plan,*,expected_plan_sha256,context_sha256,source_pin,source_root,
                 processing_recipe_sha256,chunk_identity,ffmpeg_path,ffprobe_path,media_root,
                 ffmpeg_pin,ffprobe_pin,output_root,geometry,video_stream_index,burn_in_subtitles,
                 runtime_environment_sha256,runtime_environment_fence,
                 captions_ass=None,captions_sha256=None,capacity,max_chunk_bytes=128*1024*1024,
                 timeout_seconds=900,cancelled=lambda:False):
    """Encode exactly one planned chunk, normalize timing and return no QC grant.

    runtime_environment_fence() must independently revalidate the native-admitted
    codec/font/provider/OS dependency closure and return its exact hash. An
    executable hash alone does not pin DirectWrite fonts or Media Foundation.
    This private callback interface is not itself native closure admission.
    capacity(additional_bytes) must throw on native volume/aggregate-quota/fresh
    space refusal. Source/executable hashes are rechecked before and after each
    bounded process. Native checkpoint scheduling/physical reuse is separate.
    """
    from engine.dubflow.media.adapter import _validate_executable
    from .longform_fragments import assemble_fragments
    _integer(timeout_seconds,'chunk execution deadline',1,900)
    _integer(max_chunk_bytes,'private chunk byte bound',1,MAX_CHUNK_BYTES)
    _integer(video_stream_index,'chunk video stream',0,1023)
    if not callable(capacity)or not callable(cancelled)or not callable(runtime_environment_fence):
        raise ReviewError('chunk needs native environment/capacity/cancel callbacks')
    deadline=time.monotonic()+timeout_seconds
    plan=read_plan(raw_plan,expected_hash=expected_plan_sha256,context_sha256=context_sha256,
        source_sha256=source_pin['sha256'],processing_recipe_sha256=processing_recipe_sha256)
    selected=[c for c in plan['chunks']if c['identity']==chunk_identity]
    if len(selected)!=1:raise ReviewError('chunk identity is absent or ambiguous in current native plan')
    chunk=selected[0];left=int(chunk['start_tick']);right=int(chunk['end_tick']);origin=int(plan['source_interval']['start_tick'])
    clock=tuple(int(plan['time_base'][k])for k in ('numerator','denominator'))
    # FFmpeg filter expressions use binary64. Refuse before execution when a
    # selected filter cannot represent this absolute PTS exactly; metadata and
    # fragment assembly still preserve signed64. Never silently round a source.
    if max(abs(left),abs(right),abs(origin))>2**53-1 or clock[1]>2**31-1:
        raise ReviewError('chunk FFmpeg filter clock requires an exact supported timestamp mapping')
    executables=[_validate_executable(path,name,trusted_root=_path(str(media_root)))for path,name in
                 ((ffmpeg_path,'ffmpeg_path'),(ffprobe_path,'ffprobe_path'))]
    root=_path(str(output_root))
    if not root.is_dir():raise ReviewError('chunk requires an existing native private root')
    expected_recipe=recipe_identity(ffmpeg_pin=ffmpeg_pin,ffprobe_pin=ffprobe_pin,geometry=geometry,
        time_base=clock,burn_in_subtitles=burn_in_subtitles,
        video_stream_index=video_stream_index,runtime_environment_sha256=runtime_environment_sha256,deadline=deadline)
    if expected_recipe!=processing_recipe_sha256:raise ReviewError('chunk encoder/helper/executable recipe differs from native plan')
    local=None
    if burn_in_subtitles:
        track=AssTrack.read(captions_ass,captions_sha256)
        local=track.window(time_base=clock,source_origin=origin,start_tick=left,end_tick=right)
        dependency=plan_hash(local)
    else:
        if captions_ass is not None or captions_sha256 is not None:raise ReviewError('non-burn chunk cannot accept undeclared caption bytes')
        dependency=NO_BURN_HASH
    if dependency!=chunk['visual_dependencies_sha256']:raise ReviewError('chunk local captions differ from native dependency key')

    def fence():
        if cancelled()or time.monotonic()>=deadline:raise ReviewError('chunk cancelled or execution deadline exceeded')
        if runtime_environment_fence()!=runtime_environment_sha256:
            raise ReviewError('chunk codec/font environment differs from native recipe admission')
        for path,pin in zip(executables,(ffmpeg_pin,ffprobe_pin)):
            if _file_hash(path,deadline,inventory_file=True)!=(pin['sha256'],_size(pin['size_bytes'])):
                raise ReviewError('chunk executable differs from admitted physical release')
        return _verify(source_pin,source_root,deadline)

    source=fence()
    if source.is_relative_to(root):raise ReviewError('chunk output must not own or overwrite its source')
    capacity(2*max_chunk_bytes)
    working=_path(tempfile.mkdtemp(prefix='chunk-encode-',dir=root),root)
    raw_path=working/'encoded.mp4';result_path=None;complete=False
    try:
        if local is not None:
            # A fixed safe basename avoids filter grammar injection from paths.
            with (working/'captions.ass').open('xb')as stream:stream.write(local);stream.flush();os.fsync(stream.fileno())
        filters=f'trim=start_pts={left}:end_pts={right},setpts=PTS-{origin}'
        if local is not None:filters+=',subtitles=captions.ass'
        filters+=',setpts=PTS-STARTPTS'
        args=(str(executables[0]),'-hide_banner','-loglevel','error','-nostdin','-xerror','-copyts',
            '-seek_timestamp','1','-ss',_seek_seconds(left,clock),'-noaccurate_seek','-i',str(source),'-map',f'0:{video_stream_index}',
            '-vf',filters,'-fps_mode','passthrough','-enc_time_base',f'{clock[0]}:{clock[1]}',
            '-frames:v',chunk['frame_count'],
            '-c:v','h264_mf','-hw_encoding','0','-quality','90','-bf','0','-pix_fmt','yuv420p','-an','-sn',
            '-video_track_timescale',str(clock[1]),'-avoid_negative_ts','disabled',
            '-movflags','+empty_moov+frag_keyframe+default_base_moof','-f','mp4','pipe:1')
        written=0;next_space=0
        with raw_path.open('xb')as target:
            def consume(blocks):
                nonlocal written,next_space
                for block in blocks:
                    if written>=next_space:
                        capacity(2*max_chunk_bytes-written);next_space=written+4*1024*1024
                    if written+len(block)>max_chunk_bytes:raise ReviewError('chunk encoded bytes exceed private candidate budget')
                    target.write(block);written+=len(block)
                target.flush();os.fsync(target.fileno())
            _consume_process(args,consume,deadline=deadline,max_stdout_bytes=max_chunk_bytes,
                              cancelled=cancelled,cwd=working)
        if written==0:raise ReviewError('chunk encoder produced no private media')
        fence();capacity(written);raw_path.chmod(stat.S_IREAD)
        raw_digest,raw_size=_file_hash(raw_path,deadline)
        # A one-chunk derivative validates exact encoded sample PTS and derives
        # its final hold. The unchanged current plan/identity remains provenance.
        sub=copy.deepcopy(plan);sub.update(source_interval=dict(start_tick=str(left),end_tick=str(right)),
            chunks=[chunk],frame_count=chunk['frame_count'],frame_clock_sha256=chunk['frame_clock_sha256'])
        subraw=encode_plan(sub)
        normalized=root/'normalized';_path(str(normalized),root);normalized.mkdir(exist_ok=True)
        normalized_result=assemble_fragments(subraw,expected_plan_sha256=plan_hash(subraw),context_sha256=context_sha256,
            source_sha256=source_pin['sha256'],processing_recipe_sha256=processing_recipe_sha256,
            inputs=(dict(path=str(raw_path),sha256=raw_digest,size_bytes=str(raw_size)),),input_root=working,
            output_root=normalized,geometry=geometry,capacity=capacity,timeout_seconds=max(1,min(720,int(deadline-time.monotonic()))),
            cancelled=cancelled)
        result_path=Path(normalized_result['path'])
        # Full video decode still has the same bounded native process fences.
        fence()
        decode=(str(executables[0]),'-hide_banner','-loglevel','error','-nostdin','-xerror','-i',str(result_path),
                '-map','0:v:0','-f','null','-')
        _consume_process(decode,lambda blocks:sum(len(b)for b in blocks),deadline=deadline,max_stdout_bytes=4096,cancelled=cancelled)
        fence()
        if local is not None and (working/'captions.ass').read_bytes()!=local:raise ReviewError('chunk private caption window changed')
        capacity(0)
        result=dict(schema_version=1,kind='review_private_encoded_chunk_candidate',context_sha256=context_sha256,
            plan_sha256=expected_plan_sha256,source_sha256=source_pin['sha256'],processing_recipe_sha256=processing_recipe_sha256,
            runtime_environment_sha256=runtime_environment_sha256,
            chunk_identity=chunk_identity,caption_window_sha256=dependency,source_frame_clock_sha256=chunk['frame_clock_sha256'],
            frame_count=chunk['frame_count'],file={k:normalized_result[k]for k in ('path','sha256','size_bytes')},
            full_video_decode=True,semantic_qc_admitted=False,active_graph_published=False)
        complete=True;return result
    finally:
        # Only newly allocated private scratch, never native-retained/checkpoint
        # inputs. A hard crash may leave quota-accounted unreferenced scratch.
        _path(str(working),root)
        _path(str(raw_path),working)
        if raw_path.exists():raw_path.chmod(stat.S_IREAD|stat.S_IWRITE);raw_path.unlink()
        caption=working/'captions.ass'
        _path(str(caption),working)
        if caption.exists():caption.unlink()
        working.rmdir()
        if not complete and result_path is not None:
            _path(str(result_path),normalized);result_path.unlink(missing_ok=True)

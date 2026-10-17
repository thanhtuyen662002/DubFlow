"""Private source-clock fragmented MP4 video assembly, never graph publication.

Only an explicit one-track AVC subset is supported. Native must admit the
processing/source/plan/physical locators and final decoded PTS, geometry and AV
QC. This module validates bounded box/sample inventories and exact local PTS
hashes, then derives tfdt and final sample hold from source clock intervals.
It does not concatenate guessed chunk durations, re-encode or alter input files.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import time

from .longform_plan import _decimal, _integer, read_plan
from .production import ReviewError
from .render_execution import _path, _verify

MAX_HEADER = 1024 * 1024
MAX_BOXES = 32768
MAX_INPUTS = 4096
MAX_METADATA = 16 * 1024 * 1024
MAX_VIDEO_BYTES = 128 * 1024**3
BLOCK = 32768


def _refuse(message):
    raise ReviewError("fragmented video " + message)


def _check(deadline, cancelled):
    if cancelled() or time.monotonic() >= deadline:
        _refuse("cancelled or exceeded its bounded deadline")


@dataclass(frozen=True)
class Box:
    kind: bytes
    start: int
    size: int
    header: int

    @property
    def payload(self):
        return self.start + self.header

    @property
    def end(self):
        return self.start + self.size


def _boxes(raw, start=0, end=None):
    end = len(raw) if end is None else end
    result = []
    while start < end:
        if end - start < 8 or len(result) >= MAX_BOXES:
            _refuse("box inventory is truncated or exceeds its bound")
        size, kind = struct.unpack_from(">I4s", raw, start)
        header = 8
        if size == 1:
            if end - start < 16:
                _refuse("extended box size is truncated")
            size, header = struct.unpack_from(">Q", raw, start + 8)[0], 16
        if size < header or start + size > end:
            _refuse("box escapes its exact byte inventory")
        result.append(Box(kind, start, size, header))
        start += size
    return result


def _one(boxes, kind):
    items = [b for b in boxes if b.kind == kind]
    if len(items) != 1:
        _refuse("requires one " + kind.decode("ascii") + " box")
    return items[0]


def _full(raw, box):
    if box.end - box.payload < 4:
        _refuse("full box header is truncated")
    return raw[box.payload], int.from_bytes(raw[box.payload+1:box.payload+4], "big"), box.payload+4


def _u32(raw, offset, end):
    if offset + 4 > end:
        _refuse("sample or track field is truncated")
    return struct.unpack_from(">I", raw, offset)[0]


def _initialization(raw, *, timescale, geometry):
    top = _boxes(raw)
    ftyp, moov = _one(top, b"ftyp"), _one(top, b"moov")
    if [b.kind for b in top] != [b"ftyp", b"moov"] or len(raw) > MAX_HEADER:
        _refuse("initialization inventory is unsupported")
    movie = _boxes(raw, moov.payload, moov.end)
    track, mvex = _one(movie, b"trak"), _one(movie, b"mvex")
    track_boxes = _boxes(raw, track.payload, track.end)
    if any(b.kind in (b"edts", b"tref", b"senc", b"sinf") for b in track_boxes):
        _refuse("edit lists, track references or encryption need explicit compatibility")
    tkhd, mdia = _one(track_boxes, b"tkhd"), _one(track_boxes, b"mdia")
    version, _, p = _full(raw, tkhd)
    if version not in (0, 1):
        _refuse("track version is unsupported")
    track_id = _u32(raw, p + (8 if version == 0 else 16), tkhd.end)
    mdias = _boxes(raw, mdia.payload, mdia.end)
    mdhd, hdlr, minf = (_one(mdias, k) for k in (b"mdhd", b"hdlr", b"minf"))
    version, _, p = _full(raw, mdhd)
    if version not in (0, 1) or _u32(raw, p+(8 if version == 0 else 16), mdhd.end) != timescale:
        _refuse("track time base differs from the exact source clock")
    _, _, p = _full(raw, hdlr)
    if raw[p+4:p+8] != b"vide":
        _refuse("initialization must contain video only")
    media_info = _boxes(raw, minf.payload, minf.end)
    dinf = _one(media_info,b"dinf")
    dref = _one(_boxes(raw,dinf.payload,dinf.end),b"dref")
    version, flags, p = _full(raw,dref)
    references = _boxes(raw,p+4,dref.end)
    if version or flags or _u32(raw,p,dref.end)!=1 or len(references)!=1 or references[0].kind!=b"url ":
        _refuse("requires one self-contained media data reference")
    version, flags, p = _full(raw,references[0])
    if version or flags!=1 or p!=references[0].end:
        _refuse("external media data references are unsupported")
    stbl = _one(media_info, b"stbl")
    tables = _boxes(raw,stbl.payload,stbl.end)
    if len(tables)!=5 or {b.kind for b in tables}!={b"stsd",b"stts",b"stsc",b"stsz",b"stco"}:
        _refuse("initialization needs explicit empty fragment-only sample tables")
    for name in (b"stts",b"stsc",b"stsz",b"stco"):
        table=_one(tables,name);version,flags,p=_full(raw,table)
        expected=8 if name==b"stsz" else 4
        if version or flags or table.end-p!=expected or any(raw[p:table.end]):
            _refuse("initialization contains nonempty or unsupported sample tables")
    stsd = _one(tables, b"stsd")
    version, flags, p = _full(raw, stsd)
    if version != 0 or flags != 0 or _u32(raw, p, stsd.end) != 1:
        _refuse("sample description inventory is unsupported")
    entries = _boxes(raw, p+4, stsd.end)
    if len(entries) != 1:
        _refuse("requires one unambiguous sample description")
    entry = _one(entries, b"avc1")
    if entry.size < 86:
        _refuse("AVC sample description is truncated")
    if struct.unpack_from('>H',raw,entry.start+14)[0]!=1:
        _refuse("AVC sample description substitutes its local data reference")
    width, height = struct.unpack_from(">HH", raw, entry.start+32)
    if (width, height) != geometry:
        _refuse("encoded geometry differs from admitted source geometry")
    configuration = _boxes(raw, entry.start+86, entry.end)
    if any(b.kind not in (b"avcC",b"pasp",b"btrt",b"colr") for b in configuration):
        _refuse("encrypted or unknown AVC configuration requires compatibility")
    avcc = _one(configuration, b"avcC")
    if not 1 <= avcc.size <= 8192:
        _refuse("AVC decoder configuration exceeds its bound")
    trex = _one(_boxes(raw, mvex.payload, mvex.end), b"trex")
    version, flags, p = _full(raw, trex)
    if version or flags or trex.end-p != 20 or _u32(raw,p,trex.end) != track_id or _u32(raw,p+4,trex.end) != 1:
        _refuse("default sample track identity is unsupported")
    return track_id


def _top_inventory(stream, size, deadline, cancelled):
    boxes, position = [], 0
    while position < size:
        _check(deadline, cancelled)
        stream.seek(position)
        raw = stream.read(16)
        if len(raw) < 8 or len(boxes) >= MAX_BOXES:
            _refuse("top-level inventory is truncated or too large")
        length, kind = struct.unpack_from(">I4s", raw)
        header = 8
        if length == 1:
            if len(raw) < 16:
                _refuse("extended top-level box is truncated")
            length, header = struct.unpack_from(">Q", raw, 8)[0], 16
        if length < header or position+length > size:
            _refuse("top-level box exceeds the pinned file")
        boxes.append(Box(kind, position, length, header))
        position += length
    return boxes


def _fragment(raw, mdat, *, track_id, expected_dts, chunk_start, chunk_end,
              numerator, frame_hash, remaining_frames, deadline, cancelled):
    """Validate one moof/mdat, returning exact timestamp-field patch offsets."""
    moof = _one(_boxes(raw), b"moof")
    parts = _boxes(raw, moof.payload, moof.end)
    if len(parts) != 2:
        _refuse("requires one sequence and one video fragment track")
    mfhd, traf = _one(parts,b"mfhd"), _one(parts,b"traf")
    version, flags, p = _full(raw,mfhd)
    if version or flags or mfhd.end-p != 4 or _u32(raw,p,mfhd.end) == 0:
        _refuse("fragment sequence is unsupported")
    sequence_offset = p
    children = _boxes(raw,traf.payload,traf.end)
    if len(children) != 3:
        _refuse("encrypted, multiple-run or unknown fragment data needs compatibility")
    tfhd, tfdt, trun = (_one(children,k) for k in (b"tfhd",b"tfdt",b"trun"))
    version, flags, p = _full(raw,tfhd)
    if version or flags != 0x020038 or tfhd.end-p != 16 or _u32(raw,p,tfhd.end) != track_id:
        _refuse("requires default-base-is-moof and explicit video sample defaults")
    default_duration, default_size, default_flags = struct.unpack_from(">III",raw,p+4)
    if not default_duration or not default_size:
        _refuse("sample defaults cannot be empty")
    version, flags, p = _full(raw,tfdt)
    if version != 1 or flags or tfdt.end-p != 8:
        _refuse("requires an explicit64bit decode clock")
    dts = struct.unpack_from(">Q",raw,p)[0]
    if dts != expected_dts:
        _refuse("local fragment decode clock has a gap or overlap")
    decode_offset = p
    version, flags, p = _full(raw,trun)
    if version not in (0,1) or flags & ~0xF05 or not flags & 1 or not flags & 0x100 or flags & 4 and flags & 0x400:
        _refuse("sample run needs explicit duration/data offset and supported flags")
    count = _u32(raw,p,trun.end)
    if not 1 <= count <= remaining_frames:
        _refuse("sample count differs from its bounded chunk inventory")
    _u32(raw,p+4,trun.end)
    data_offset = struct.unpack_from(">i",raw,p+4)[0]
    if data_offset != len(raw)+mdat.header:
        _refuse("fragment payload offset does not name its adjacent mdat")
    p += 8
    first_flags = _u32(raw,p,trun.end) if flags & 4 else None
    if flags & 4:
        p += 4
    payload_bytes, last_duration_offset = 0, None
    for index in range(count):
        if index % 4096 == 0:
            _check(deadline,cancelled)
        duration_offset = p
        duration = _u32(raw,p,trun.end)
        p += 4
        size = _u32(raw,p,trun.end) if flags & 0x200 else default_size
        if flags & 0x200:
            p += 4
        sample_flags = _u32(raw,p,trun.end) if flags & 0x400 else (first_flags if index==0 and first_flags is not None else default_flags)
        if flags & 0x400:
            p += 4
        cts = _u32(raw,p,trun.end) if flags & 0x800 else 0
        if flags & 0x800:
            p += 4
        if not duration or not size or cts:
            _refuse("zero samples or reordered decode/presentation clocks are unsupported")
        if expected_dts == 0 and index == 0 and (sample_flags & 0x10000 or sample_flags >> 24 & 3 != 2):
            _refuse("chunk must start with an independent sync sample")
        if dts % numerator:
            _refuse("sample cannot map exactly to a source tick")
        source_pts = chunk_start+dts//numerator
        if not chunk_start <= source_pts < chunk_end:
            _refuse("sample PTS escapes its admitted source interval")
        frame_hash.update((str(source_pts)+'\n').encode('ascii'))
        payload_bytes += size
        dts += duration
        last_duration_offset = duration_offset
    if p != trun.end or payload_bytes != mdat.size-mdat.header:
        _refuse("sample payload inventory differs from mdat bytes")
    return dict(sequence_offset=sequence_offset,decode_offset=decode_offset,base=expected_dts,
                end=dts,count=count,last_duration_offset=last_duration_offset,last_source_pts=source_pts)


def assemble_fragments(raw_plan, *, expected_plan_sha256, context_sha256, source_sha256,
                       processing_recipe_sha256, inputs, input_root, output_root,
                       geometry, capacity, timeout_seconds=180, cancelled=lambda:False):
    """Copy pinned native-retained fragments into one private video candidate.

    capacity(remaining_bytes) is a mandatory supervisor-derived output volume/
    aggregate quota/fresh free-space guard, called before writes and each4MiB.
    Python callback existence does not replace native admission. Result is not
    semantic QC, final mux, publication or a reusable physical checkpoint.
    """
    _integer(timeout_seconds,'fragment assembly deadline',1,720)
    if (not callable(capacity) or not callable(cancelled) or not isinstance(geometry,tuple) or
            len(geometry)!=2 or any(type(x)is not int or not 1<=x<=65535 for x in geometry)):
        _refuse("requires native capacity/cancel observations and exact coded geometry")
    deadline=time.monotonic()+timeout_seconds
    plan=read_plan(raw_plan,expected_hash=expected_plan_sha256,context_sha256=context_sha256,
                   source_sha256=source_sha256,processing_recipe_sha256=processing_recipe_sha256)
    if not isinstance(inputs,tuple) or len(inputs)!=len(plan['chunks']) or not 1<=len(inputs)<=MAX_INPUTS:
        _refuse("physical input inventory differs from planned chunks")
    root=_path(str(output_root));source_root=_path(str(input_root))
    if not root.is_dir() or not source_root.is_dir():
        _refuse("requires existing native-admitted input/output roots")
    n=int(plan['time_base']['numerator']);d=int(plan['time_base']['denominator'])
    if d>2**31-1:
        _refuse("source clock exceeds the supported MP4 timescale")
    origin=int(plan['source_interval']['start_tick'])
    prepared=[];initial=None;total=0;frame_total=0;metadata_bytes=0;fragment_count=0
    for pin,chunk in zip(inputs,plan['chunks']):
        _check(deadline,cancelled)
        path=_verify(pin,source_root,deadline)
        if not path.stat().st_mode & 0o444 or path.stat().st_mode & 0o222:
            _refuse("input fragments must be immutable native-retained files")
        if path.is_relative_to(root):
            _refuse("output generation must not contain its retained inputs")
        before=path.stat()
        with path.open('rb') as stream:
            boxes=_top_inventory(stream,before.st_size,deadline,cancelled)
            if len(boxes)<4 or [b.kind for b in boxes[:2]]!=[b'ftyp',b'moov']:
                _refuse("requires an empty initialization followed by media fragments")
            header_end=boxes[1].end
            if header_end>MAX_HEADER:
                _refuse("initialization exceeds its byte bound")
            stream.seek(0);header=stream.read(header_end)
            track=_initialization(header,timescale=d,geometry=geometry)
            if initial is None:
                initial=header
            if header!=initial:
                _refuse("codec/track initialization differs across chunks")
            inventory=boxes[2:]
            if inventory and inventory[-1].kind==b'mfra':
                inventory=inventory[:-1] # Old absolute random-access table is not copied.
            if not inventory or len(inventory)%2 or len(inventory)>MAX_BOXES-2:
                _refuse("media fragment pairs are incomplete")
            clock=hashlib.sha256();count=0;next_dts=0;fragments=[]
            left=int(chunk['start_tick']);right=int(chunk['end_tick'])
            for i in range(0,len(inventory),2):
                moof,mdat=inventory[i:i+2]
                if moof.kind!=b'moof' or mdat.kind!=b'mdat' or moof.end!=mdat.start or moof.size>MAX_HEADER:
                    _refuse("requires bounded adjacent moof/mdat pairs only")
                stream.seek(moof.start);raw=stream.read(moof.size)
                metadata_bytes+=len(raw);fragment_count+=1
                if metadata_bytes>MAX_METADATA or fragment_count>MAX_BOXES:
                    _refuse("aggregate fragment metadata exceeds its memory bound")
                f=_fragment(raw,mdat,track_id=track,expected_dts=next_dts,chunk_start=left,chunk_end=right,
                            numerator=n,frame_hash=clock,remaining_frames=int(chunk['frame_count'])-count,
                            deadline=deadline,cancelled=cancelled)
                f.update(raw=raw,moof=moof,mdat=mdat,source_path=path)
                fragments.append(f);next_dts=f['end'];count+=f['count']
                total+=moof.size+mdat.size
            if count!=int(chunk['frame_count']) or 'sha256:'+clock.hexdigest()!=chunk['frame_clock_sha256']:
                _refuse("encoded frame PTS/count differs from exact admitted source clock")
            final_hold=(right-fragments[-1]['last_source_pts'])*n
            offset=(left-origin)*n
            if not 1<=final_hold<=2**32-1 or not 0<=offset<=2**64-1:
                _refuse("source-derived decode offset/sample hold exceeds MP4 bounds")
            for f in fragments:
                if f['base']+offset>2**64-1:
                    _refuse("decode timestamp overflows64bits")
                f['global_base']=f['base']+offset
            fragments[-1]['final_hold']=final_hold
            prepared.append((pin,path,before,fragments));frame_total+=count
    total+=len(initial)
    if total>MAX_VIDEO_BYTES:
        _refuse("assembled candidate exceeds video byte bound")
    _check(deadline,cancelled);capacity(total)
    descriptor,name=tempfile.mkstemp(prefix='.review-fragments-',suffix='.partial.mp4',dir=root)
    output=_path(name,root);complete=False;written=0;digest=hashlib.sha256();next_space_check=0
    try:
        with os.fdopen(descriptor,'wb') as target:
            def write(raw):
                nonlocal written,next_space_check
                _check(deadline,cancelled)
                if written>=next_space_check:
                    capacity(total-written);next_space_check=written+4*1024*1024
                target.write(raw);digest.update(raw);written+=len(raw)
            write(initial);sequence=0
            for pin,path,before,fragments in prepared:
                if _verify(pin,source_root,deadline)!=path or path.stat().st_mtime_ns!=before.st_mtime_ns:
                    _refuse("retained fragment changed before copy")
                with path.open('rb') as source:
                    for f in fragments:
                        sequence+=1
                        if sequence>2**32-1:
                            _refuse("fragment sequence overflows32bits")
                        raw=bytearray(f['raw'])
                        struct.pack_into('>I',raw,f['sequence_offset'],sequence)
                        struct.pack_into('>Q',raw,f['decode_offset'],f['global_base'])
                        if 'final_hold'in f:
                            struct.pack_into('>I',raw,f['last_duration_offset'],f['final_hold'])
                        write(raw)
                        source.seek(f['mdat'].start);remaining=f['mdat'].size
                        while remaining:
                            data=source.read(min(BLOCK,remaining))
                            if not data:
                                _refuse("retained fragment truncated during copy")
                            write(data);remaining-=len(data)
                _verify(pin,source_root,deadline)
                if path.stat().st_mtime_ns!=before.st_mtime_ns or path.stat().st_mode & 0o222:
                    _refuse("retained fragment lost immutability during copy")
            if written!=total:
                _refuse("assembled byte total changed")
            for pin,path,before,_ in prepared:
                _verify(pin,source_root,deadline)
                if path.stat().st_mtime_ns!=before.st_mtime_ns or path.stat().st_mode & 0o222:
                    _refuse("retained fragment changed before final candidate seal")
            _check(deadline,cancelled);capacity(0);target.flush();os.fsync(target.fileno())
        complete=True
        return dict(path=str(output),sha256='sha256:'+digest.hexdigest(),size_bytes=str(written),
                    frame_count=str(frame_total),initialization_sha256='sha256:'+hashlib.sha256(initial).hexdigest(),
                    source_origin_tick=str(origin),output_time_base=dict(numerator='1',denominator=str(d)),
                    semantic_qc_admitted=False,active_graph_published=False)
    finally:
        if not complete:
            output.unlink(missing_ok=True) # Only the newly created private candidate.

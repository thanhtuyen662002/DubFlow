"""Pinned ASS interval views with original global event bytes and local keys.

Native separately admits the current canonical timeline/captions/provenance.
This bounded private view neither changes SRT/ticks nor grants media authority.
"""
from dataclasses import dataclass
import re

from .longform_plan import _clock, _hash, _integer, plan_hash
from .production import ReviewError

MAX_ASS_BYTES=64*1024*1024
MAX_HEADER_BYTES=65536
MAX_EVENTS=10000
EVENT_FORMAT='Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text'
NO_BURN_HASH=plan_hash(b'review-chunk-no-burn-in-v1')
_TIME=re.compile(r'([0-6]):([0-5][0-9]):([0-5][0-9])\.([0-9]{2})\Z')


def _milliseconds(value):
    match=_TIME.fullmatch(value)
    if match is None:
        raise ReviewError('chunk ASS clock must preserve canonical global centiseconds')
    h,m,s,cs=map(int,match.groups())
    result=((h*60+m)*60+s)*1000+cs*10
    if result>21600000:
        raise ReviewError('chunk ASS event exceeds six-hour source policy')
    return result


@dataclass(frozen=True)
class AssTrack:
    header: bytes
    events: tuple
    sha256: str

    @classmethod
    def read(cls,raw,expected_hash):
        _hash(expected_hash)
        if type(raw)is not bytes or not 1<=len(raw)<=MAX_ASS_BYTES or plan_hash(raw)!=expected_hash:
            raise ReviewError('chunk ASS bytes differ from admitted current pin or bound')
        try:
            lines=raw.decode('utf-8').splitlines(keepends=True)
        except UnicodeError as error:
            raise ReviewError('chunk ASS requires UTF8 source bytes')from error
        if any('\x00'in l or not l.endswith('\n') for l in lines):
            raise ReviewError('chunk ASS requires complete bounded native lines')
        sections=[i for i,l in enumerate(lines)if l.rstrip('\r\n')=='[Events]']
        if len(sections)!=1 or sections[0]+1>=len(lines) or lines[sections[0]+1].rstrip('\r\n')!=EVENT_FORMAT:
            raise ReviewError('chunk ASS needs its exact native event section/format')
        cut=sections[0]+2
        header=''.join(lines[:cut]).encode('utf-8')
        names=[l.rstrip('\r\n')for l in lines[:cut]if l.startswith('[')]
        if names!=['[Script Info]','[V4+ Styles]','[Events]'] or len(header)>MAX_HEADER_BYTES:
            raise ReviewError('unknown ASS attachments/sections need explicit compatibility')
        if len(lines)-cut>MAX_EVENTS:
            raise ReviewError('chunk ASS event inventory exceeds its bound')
        events=[]
        for line in lines[cut:]:
            if len(line.encode('utf-8'))>65536 or not line.startswith('Dialogue: '):
                raise ReviewError('chunk ASS needs bounded native dialogue records')
            parts=line.rstrip('\r\n')[10:].split(',',9)
            if len(parts)!=10 or (parts[0],*parts[3:9])!=('0','Default','','0','0','0',''):
                raise ReviewError('chunk ASS dialogue fields differ from native format')
            start,end=_milliseconds(parts[1]),_milliseconds(parts[2])
            if end<=start or not parts[9].strip():
                raise ReviewError('chunk ASS event has no positive visible interval/text')
            events.append((start,end,line.encode('utf-8')))
        return cls(header,tuple(events),expected_hash)

    def window(self,*,time_base,source_origin,start_tick,end_tick):
        _clock(time_base,start_tick,end_tick)
        _integer(source_origin,'caption source origin')
        if source_origin>start_tick:
            raise ReviewError('chunk caption interval precedes source origin')
        n,d=time_base
        left=(start_tick-source_origin)*n*1000
        right=(end_tick-source_origin)*n*1000
        selected=[raw for start,end,raw in self.events if start*d<right and end*d>left]
        raw=self.header+b''.join(selected)
        if len(raw)>MAX_ASS_BYTES:
            raise ReviewError('chunk ASS window exceeds its byte bound')
        return raw

    def dependency(self,**clock):
        return plan_hash(self.window(**clock))

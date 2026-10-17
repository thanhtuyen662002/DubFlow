import hashlib
import unittest

from engine.dubflow.review.longform_captions import AssTrack,EVENT_FORMAT,NO_BURN_HASH
from engine.dubflow.review.production import ReviewError

HEADER=('[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 180\n[V4+ Styles]\n'
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
        'Style: Default,Arial,24,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,0,2,10,10,12,1\n[Events]\n'+EVENT_FORMAT+'\n')


def caption(start,end,text):
    return f'Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n'


def pin(raw):return 'sha256:'+hashlib.sha256(raw).hexdigest()


class LongformCaptionTests(unittest.TestCase):
    def test_global_event_bytes_cross_chunk_boundaries_without_clipping_or_rebasing(self):
        first=caption('0:00:00.50','0:00:01.50','Một người rất thân yêu.')
        second=caption('0:00:02.00','0:00:02.50','Another cue, with comma')
        raw=(HEADER+first+second).encode()
        track=AssTrack.read(raw,pin(raw))
        for left,right in ((3500,4000),(4000,4500)):
            window=track.window(time_base=(1,1000),source_origin=3000,start_tick=left,end_tick=right)
            self.assertEqual(window,(HEADER+first).encode())
            self.assertEqual(track.dependency(time_base=(1,1000),source_origin=3000,start_tick=left,end_tick=right),pin(window))
        self.assertEqual(track.window(time_base=(1,1000),source_origin=3000,start_tick=4500,end_tick=5000),HEADER.encode())

    def test_unrelated_caption_change_keeps_local_keys_and_style_change_invalidates_them(self):
        a=caption('0:00:00.00','0:00:01.00','first')
        b=caption('0:00:02.00','0:00:03.00','second')
        def track(raw):return AssTrack.read(raw,pin(raw))
        before=track((HEADER+a+b).encode());after=track((HEADER+a+b.replace('second','changed')).encode())
        local=dict(time_base=(1,1000),source_origin=3000,start_tick=3000,end_tick=4000)
        self.assertNotEqual(before.sha256,after.sha256)
        self.assertEqual(before.dependency(**local),after.dependency(**local))
        style=track((HEADER.replace('Arial,24','Arial,25')+a+b).encode())
        self.assertNotEqual(style.dependency(**local),before.dependency(**local))
        self.assertNotEqual(NO_BURN_HASH,before.dependency(**local))

    def test_crlf_and_unicode_bytes_are_preserved(self):
        raw=(HEADER+caption('0:00:00.00','0:00:01.00','Chúc bạn bình an.')).replace('\n','\r\n').encode()
        track=AssTrack.read(raw,pin(raw))
        self.assertEqual(track.window(time_base=(1,1000),source_origin=-100,start_tick=-100,end_tick=900),raw)

    def test_exact_rational_interval_selection_has_no_float_or_millisecond_rounding(self):
        raw=(HEADER+caption('0:00:00.03','0:00:00.07','near rational boundary')).encode();track=AssTrack.read(raw,pin(raw))
        origin=2**53+17
        self.assertIn(b'near rational',track.window(time_base=(1001,30000),source_origin=origin,start_tick=origin+1,end_tick=origin+2))
        self.assertEqual(track.window(time_base=(1001,30000),source_origin=origin,start_tick=origin+3,end_tick=origin+4),HEADER.encode())

    def test_foreign_hash_fields_sections_times_and_partial_lines_refuse(self):
        good=(HEADER+caption('0:00:00.00','0:00:01.00','visible')).encode()
        with self.assertRaises(ReviewError):AssTrack.read(good,pin(b'foreign'))
        samples=[good[:-1],b'\xff',good.replace(b'[V4+ Styles]',b'[Fonts]'),
            good.replace(b'0:00:00.00',b'00:00:00.00'),good.replace(b'0:00:01.00',b'0:00:00.00'),
            good.replace(b',Default,,0,0,0,,',b',Foreign,,0,0,0,,'),good+b'Comment: ignored\n',good+b'\0\n']
        for raw in samples:
            with self.subTest(raw=raw[:50]),self.assertRaises(ReviewError):AssTrack.read(raw,pin(raw))


if __name__=='__main__':unittest.main()

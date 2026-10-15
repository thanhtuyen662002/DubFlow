import hashlib
import unittest
from unittest.mock import patch

from engine.dubflow.review.longform_clock import DecodedFrameClock, iter_decoded_pts, plan_decoded_clock
from engine.dubflow.review.production import ReviewError


def xml(points):
    return ('<?xml version="1.0" encoding="UTF-8"?><ffprobe><frames>' +
            ''.join(f'<frame pts="{p}"/>' for p in points) + '</frames></ffprobe>').encode()


def blocks(raw, size=4096):
    return (raw[i:i + size] for i in range(0, len(raw), size))


def pin(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


class LongformClockTests(unittest.TestCase):
    def test_arbitrary_byte_splits_keep_exact_negative_nonzero_and_wide_source_pts(self):
        pts = (-100, -99, 0, 2**53 + 19, 2**63 - 1)
        for size in (1, 2, 7, 4096):
            with self.subTest(size=size):
                self.assertEqual(tuple(iter_decoded_pts(blocks(xml(pts), size))), pts)
        reader = DecodedFrameClock()
        self.assertEqual(reader.feed(xml(pts)), pts)
        with self.assertRaises(ReviewError):
            reader.summary
        self.assertEqual(reader.finish(), ())
        self.assertEqual(reader.summary, dict(frame_count='5', first_pts='-100', last_pts=str(2**63 - 1),
                         frame_clock_sha256=pin(''.join(f'{p}\n' for p in pts).encode())))
        with self.assertRaises(ReviewError):
            reader.feed(b'')

    def test_bounded_codec_side_data_does_not_supply_or_replace_pts(self):
        raw = b'<ffprobe><frames><frame pts="-9"><side_data_list><side_data side_data_type="SEI"/></side_data_list></frame><frame pts="1"/></frames></ffprobe>'
        self.assertEqual(tuple(iter_decoded_pts(blocks(raw, 3))), (-9, 1))
        for frame in (b'<frame/>', b'<frame best_effort_timestamp="9"/>',
                      b'<frame pts="N/A"/>', b'<frame pts="1.0"/>', b'<frame pts="+1"/>',
                      b'<frame pts="-0"/>', b'<frame pts="01"/>', b'<frame pts="9223372036854775808"/>'):
            with self.subTest(frame=frame), self.assertRaises(ReviewError):
                tuple(iter_decoded_pts((b'<ffprobe><frames>' + frame + b'</frames></ffprobe>',)))

    def test_duplicate_reversed_or_foreign_sections_refuse_without_clock_normalization(self):
        samples = [xml((1, 1)), xml((2, 1)), b'<ffprobe><packets/></ffprobe>',
                   b'<ffprobe><frames><frame pts="1" extra="2"/></frames></ffprobe>',
                   b'<ffprobe><frames/><frames/></ffprobe>', b'<ffprobe><frames/></ffprobe>',
                   b'<ffprobe><frames><frame pts="1">unexpected</frame></frames></ffprobe>']
        for raw in samples:
            with self.subTest(raw=raw), self.assertRaises(ReviewError):
                tuple(iter_decoded_pts(blocks(raw)))

    def test_partial_tail_never_returns_a_complete_plan_or_summary(self):
        reader = DecodedFrameClock()
        self.assertEqual(reader.feed(b'<ffprobe><frames><frame pts="0"/>'), (0,))
        with self.assertRaises(ReviewError):
            reader.finish()
        with self.assertRaises(ReviewError):
            reader.summary
        with self.assertRaises(ReviewError):
            reader.feed(b'</frames></ffprobe>')

    def test_external_entities_dtd_processing_instructions_and_encoding_changes_refuse(self):
        samples = [b'<!DOCTYPE ffprobe SYSTEM "file:///must-not-be-read"><ffprobe/>',
                   b'<!DOCTYPE ffprobe [<!ENTITY x "123">]><ffprobe>&x;</ffprobe>',
                   b'<?unexpected ignored?><ffprobe/>', b'<!--not-owned-writer--><ffprobe/>',
                   b'<?xml version="1.0" encoding="UTF-16"?><ffprobe/>',
                   b'<ffprobe><frames><![CDATA[ ]]> </frames></ffprobe>']
        for raw in samples:
            with self.subTest(raw=raw), self.assertRaises(ReviewError):
                tuple(iter_decoded_pts((raw,)))

    def test_byte_frame_token_and_deadline_bounds_are_enforced(self):
        with self.assertRaises(ReviewError):
            tuple(iter_decoded_pts((xml((0, 1)),), max_frames=1))
        with self.assertRaises(ReviewError):
            tuple(iter_decoded_pts((xml((0,)),), max_bytes=8))
        with self.assertRaises(ReviewError):
            tuple(iter_decoded_pts((b'x' * 4097,)))
        with self.assertRaisesRegex(ReviewError, 'token'):
            tuple(iter_decoded_pts(blocks(b'<ffprobe><frames><frame pts="' + b'1' * 20_000)))
        with patch('engine.dubflow.review.longform_clock.time.monotonic', side_effect=(10.0, 41.0)):
            with self.assertRaisesRegex(ReviewError, 'deadline'):
                tuple(iter_decoded_pts((xml((0,)),)))

    def test_stream_to_planner_is_incremental_and_has_no_whole_frame_array(self):
        consumed, callback_at = 0, []

        def source():
            nonlocal consumed
            yield b'<ffprobe><frames>'
            for p in range(100_000):
                consumed += 1
                yield f'<frame pts="{p}"/>'.encode()
            yield b'</frames></ffprobe>'

        def local_inputs(left, right):
            if not callback_at:
                callback_at.append(consumed)
            return pin(b'local source-derived visual dependencies')

        plan = plan_decoded_clock(source(), context_sha256=pin(b'current context'), source_sha256=pin(b'source'),
            processing_recipe_sha256=pin(b'processing recipe'), time_base=(1, 1000), start_tick=0,
            end_tick=100_000, target_ticks=1_000_000, max_frames=1000, visual_dependencies=local_inputs)
        self.assertEqual(callback_at, [1001])
        self.assertEqual(plan['frame_count'], '100000')
        self.assertEqual(len(plan['chunks']), 100)
        self.assertEqual(plan['frame_clock_sha256'], pin(''.join(f'{p}\n' for p in range(100_000)).encode()))

    def test_planner_rejects_a_valid_prefix_when_probe_document_is_truncated(self):
        with self.assertRaises(ReviewError):
            plan_decoded_clock((b'<ffprobe><frames><frame pts="0"/>',), context_sha256=pin(b'context'),
                source_sha256=pin(b'source'), processing_recipe_sha256=pin(b'recipe'),
                time_base=(1, 1000), start_tick=0, end_tick=10, target_ticks=5,
                visual_dependencies=lambda left, right: pin(b'visual'))


if __name__ == '__main__':
    unittest.main()

"""Proper names across languages (``dubflow.mastering.entities``) and their use by the aligner."""
from __future__ import annotations

import random
import unittest

from engine.dubflow.mastering import align, candidates as shots, entities as E, evidence as ev, policy as rules

PACING = rules.STYLE_PACING["natural"]


def found(text: str, table: E.Table = E.BUILTIN) -> list[tuple[str, str, int]]:
    return [(m.entity_id, m.written, m.tier) for m in table.mentions(text)]


class Recognition(unittest.TestCase):
    def test_the_same_place_in_vietnamese_english_russian_and_chinese(self):
        for text in ("Dãy núi Kavkaz", "the Caucasus mountains", "Горы Кавказ", "高加索山脉"):
            self.assertEqual([e for e, _, _ in found(text)], ["caucasus"], text)
        for text in ("Tại Mátxcơva", "In Moscow", "Москва", "莫斯科的冬天", "tai Matxcova"):
            self.assertIn("moscow", [e for e, _, _ in found(text)], text)
        self.assertEqual([e for e, _, _ in found("Điện Kremli về đêm")], ["kremlin"])
        self.assertEqual([e for e, _, _ in found("Кремль")], ["kremlin"])
        self.assertEqual([e for e, _, _ in found("Bắc Kinh")], [e for e, _, _ in found("北京烤鸭")])

    def test_marks_are_part_of_a_marked_name(self):
        # "thủy điện" (hydropower) is not "Thụy Điển" (Sweden): with marks the words differ
        self.assertEqual(found("Đập thủy điện lớn"), [])
        self.assertEqual([e for e, _, _ in found("Thụy Điển mùa đông")], ["sweden"])

    def test_names_that_are_also_ordinary_words_need_context(self):
        self.assertEqual(found("Áo khoác da màu đen"), [])           # a coat
        self.assertEqual(found("Uống một viên thuốc"), [])          # a pill
        self.assertEqual(found("Mặc áo ấm ra đường"), [])
        self.assertEqual(found("Ở Áo, cung điện Schönbrunn"), [("austria", "Áo", E.CONTEXTUAL)])
        self.assertEqual(found("Phố cổ Viên có quán cà phê"), [("vienna", "Viên", E.CONTEXTUAL)])
        self.assertEqual(found("O Ao, cung dien"), [])              # an ambiguous name without its marks is anything

    def test_similar_names_are_not_one_entity(self):
        self.assertEqual([e for e, _, _ in found("Schönbrunn Palace in Vienna, Austria")], ["austria", "vienna"])
        self.assertNotIn("austria", [e for e, _, _ in found("Kangaroos in the Australian outback")])
        self.assertNotIn("australia", [e for e, _, _ in found("Austrian Alps")])
        self.assertNotIn("caucasus", [e for e, _, _ in found("Kazan on the Volga")])
        self.assertEqual([e for e, _, _ in found("Kazan")], [])

    def test_the_other_reading_of_an_ambiguous_name(self):
        self.assertEqual(found("Downtown Atlanta, Georgia"), [])    # the US state
        self.assertIn(("georgia_country", "Georgia", E.CONTEXTUAL), found("Tbilisi, the capital of Georgia"))
        self.assertEqual(found("He used polish on china plates"), [])

    def test_tiers(self):
        kavkaz, caucasus = E.BUILTIN.mentions("Kavkaz")[0], E.BUILTIN.mentions("Caucasus")[0]
        self.assertEqual(E.tier(kavkaz, kavkaz, E.BUILTIN), E.EXACT)
        self.assertEqual(E.tier(kavkaz, caucasus, E.BUILTIN), E.VERIFIED)
        moscow, plain = E.BUILTIN.mentions("Mátxcơva")[0], E.BUILTIN.mentions("tai matxcova")[0]
        self.assertEqual(E.tier(moscow, plain, E.BUILTIN), E.NORMALIZED)
        austria = E.BUILTIN.mentions("Ở Áo")[0]
        self.assertEqual(E.tier(austria, E.BUILTIN.mentions("Austria")[0], E.BUILTIN), E.CONTEXTUAL)
        self.assertEqual(E.tier(austria, E.BUILTIN.mentions("Australia")[0], E.BUILTIN), 0)
        self.assertLess(E.CONTEXTUAL, E.VERIFIED)
        self.assertLess(E.VERIFIED, E.NORMALIZED)
        self.assertLess(E.NORMALIZED, E.EXACT)

    def test_a_users_note_adds_a_verified_alias(self):
        self.assertEqual(E.aliases_in_notes(["Quay ở Ninh Bình = Hoa Lư", "nồi = màu đen", "Viên còn gọi là Wien"]),
                         [("Ninh Bình", "Hoa Lư"), ("Viên", "Wien")])
        table = E.BUILTIN.with_notes(["Tràng An tức là Trang An Landscape"])
        self.assertEqual(found("Tràng An", table)[0][0], found("Trang An Landscape", table)[0][0])
        self.assertEqual(found("Tràng An"), [])  # not without the note


def candidate(cid, source, start, end):
    return shots.Candidate(cid, source, start, end, 0, 50, shots.Quality(120, 50, 300, ()), random.getrandbits(64))


class Aligner(unittest.TestCase):
    def plan(self, items, sentences, *, forward_only=False):
        pool, found_ = [], {}
        for cid, source, start, end, vision, said in items:
            pool.append(candidate(cid, source, start, end))
            found_[cid] = ev.Evidence(cid)
            found_[cid].add(ev.Item("vision", 800, vision))
            if said:
                found_[cid].add(ev.Item("transcript", 850, said))
        spoken = [(text, ms) for text, ms in sentences]
        units = align.units(spoken, sum(ms for _, ms in spoken), lead_ms=0, gap_ms=0)
        choices, decisions, _ = align.assign(units, pool, found_, PACING, forward_only=forward_only)
        return {u.unit_id: [c.candidate.candidate_id for c in choices if c.unit_id == u.unit_id] for u in units}, decisions

    def test_a_vietnamese_name_finds_the_shot_whose_subtitles_name_it_in_english(self):
        chosen, decisions = self.plan([
            ("a", "v1", 0, 5000, "Đoàn xe tăng trên đồng cỏ", "German tanks cross the steppe"),
            ("b", "v1", 5000, 10000, "Dãy núi phủ tuyết", "The Caucasus mountains guard the oil fields"),
            ("c", "v2", 0, 5000, "Thành phố đổ nát", "Stalingrad lies in ruins"),
        ], [("Dãy Kavkaz che chắn các mỏ dầu.", 4000), ("Stalingrad gần như bị san phẳng.", 4000)])
        self.assertEqual(chosen["u1"], ["b"])
        self.assertEqual(chosen["u2"], ["c"])

    def test_austria_is_not_australia(self):
        chosen, _ = self.plan([
            ("a", "t1", 0, 5000, "Cung điện màu vàng với vườn hoa", "Schönbrunn Palace in Vienna, Austria"),
            ("b", "t1", 5000, 10000, "Chuột túi trên đồng cỏ", "Kangaroos in the Australian outback"),
        ], [("Ở Áo có những cung điện cổ.", 4000), ("Còn ở Úc có chuột túi.", 4000)])
        self.assertEqual(chosen["u1"], ["a"])
        self.assertEqual(chosen["u2"], ["b"])

    def test_without_names_nothing_changes(self):
        items = [("a", "v1", 0, 5000, "Đổ nước vào bình", ""), ("b", "v1", 5000, 10000, "Đèn ngủ bảy màu", "")]
        chosen, _ = self.plan(items, [("Đèn ngủ bảy màu cực xinh.", 3000), ("Đổ nước vào bình rất dễ.", 3000)])
        self.assertEqual(chosen, {"u1": ["b"], "u2": ["a"]})


if __name__ == "__main__":
    unittest.main()

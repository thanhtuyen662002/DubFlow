"""#802: a wrong shot never becomes READY because the vision's wording changed.

The cases are the W3 long-form units that went READY on the wrong shot (rc13 / rc15 Windows E2E), reduced to the
shots that decided them. The texts are the vision answers those runs saved (the model's name left out). Nothing here
reads the ground truth of W3: each test states what a reviewer can see in the two answers.

* A1 i1.u2 "Dòng chữ lớn The Battle of Russia…": the Stimson quote card (no title) beat the title card 625 to 621
  once the vision wrote "huy hiệu" instead of "phù hiệu chim".
* A2 i16.u1 "Bản đồ … một nghìn năm trăm dặm": a map of Yugoslavia (rc13, 640) and a map of "500,000 SQUARE MILES"
  (rc15, 638) went READY on "bản đồ", "đánh dấu", "dặm"; the globe with "1500 MILES" was held back as a text card in
  rc15 because its description said "dòng chữ" and the number is spoken in words.
* A3 i1.u1 "Một tấm thẻ chữ … cho biết bộ phim có giá trị lịch sử": a dark card about "historic traditions" READY
  (rc15, 624) while the card that says it was never looked at.
"""
from __future__ import annotations

import random
import unittest

from engine.dubflow.mastering import align
from engine.dubflow.mastering import candidates as shots
from engine.dubflow.mastering import claims as said
from engine.dubflow.mastering import evidence as ev
from engine.dubflow.mastering import policy as rules

PACING = rules.STYLE_PACING["natural"]


def shot(cid: str, start: int, end: int) -> shots.Candidate:
    return shots.Candidate(cid, "s1", start, end, 0, 30, shots.Quality(120, 50, 300), random.getrandbits(64))


def seen(cid: str, description: str, objects: tuple[str, ...], visible: str, labels: tuple[str, ...], confidence: int = 900) -> ev.Evidence:
    """A shot as the vision cache answer makes it (description + objects + visible text), with its file name."""
    found = ev.Evidence(cid)
    found.add(ev.Item("metadata", 700, "wwf 01", (), "tên file"))
    found.add(ev.Item("vision", confidence, " ".join(p for p in (description, ", ".join(objects), visible) if p), labels, "AI claude"))
    return found


def plan(text: str, pool: list[shots.Candidate], found: dict[str, ev.Evidence], length: int = 3700):
    unit = align.Unit("u", text, 0, length, "narration")
    choices, decisions, mode = align.assign([unit], pool, found, PACING, forward_only=True)
    decision = decisions[0]
    ready = not decision.coverage.get("needs_review") and not any(c.needs_review for c in choices)
    return [c.candidate.candidate_id for c in choices], ready, decision


# --- the shots of the three cases -----------------------------------------------------------------------------------

TITLE = "Dòng chữ lớn The Battle of Russia hiện lên trên nền huy hiệu đại bàng."
MAP = "Bản đồ có đường thẳng đánh dấu khoảng cách một nghìn năm trăm dặm."
CARD = "Một tấm thẻ chữ trắng trên nền xám cho biết bộ phim có giá trị lịch sử."


def title_shots(with_title: bool = True):
    pool = [shot("c11", 58_520, 62_480), shot("c13", 70_920, 72_280), shot("c17", 81_520, 87_520)]
    found = {
        "c11": seen("c11", "Màn hình tiêu đề phim đen trắng với chữ lớn chồng lên nhau trên nền biểu tượng chim đại bàng.",
                    ("chữ tiêu đề", "biểu tượng đại bàng"),
                    "THE BATTLE OF RUSSIA; WHY WE FIGHT; A SERIES OF SEVEN INFORMATION FILMS; PART 1", ("text_on_screen", "establishing")),
        "c13": seen("c13", "Màn hình chữ đen trắng ghi đơn vị sản xuất, nền là hình huy hiệu đại bàng.",
                    ("chữ tiêu đề", "huy hiệu đại bàng"), "Produced by the WAR DEPARTMENT SPECIAL SERVICE DIVISION",
                    ("text_on_screen",), 920),
        "c17": seen("c17", "Màn hình chữ trắng trên nền tối, có huy hiệu đại bàng mờ phía sau, trích dẫn lời Henry L. Stimson, "
                    "Bộ trưởng Chiến tranh.", ("huy hiệu đại bàng", "dòng chữ trích dẫn"),
                    "Henry L. Stimson Secretary of War \"We and our allies owe and acknowledge an everlasting debt of gratitude "
                    "to the armies and people of the Soviet Union.\"", ("text_on_screen", "explanation"), 930),
    }
    if not with_title:
        pool = [c for c in pool if c.candidate_id != "c11"]
        del found["c11"]
    return pool, found


def map_shots(*keep: str):
    pool = [shot("c505", 1_575_000, 1_578_587), shot("c509", 1_585_292, 1_589_080), shot("c514", 1_607_920, 1_613_920),
            shot("c518", 1_630_295, 1_634_434), shot("c519", 1_634_674, 1_637_479)]
    found = {
        # rc13's targeted look at the Balkan map
        "c505": seen("c505", "Bản đồ hoạt hình vùng Balkan, mũi tên đánh dấu đường tiến quân, tên YUGOSLAVIA ở giữa.",
                     ("bản đồ", "mũi tên", "đường biên giới"), "YUGOSLAVIA; GREECE; BULGARIA", ("explanation", "text_on_screen"), 850),
        # rc15's answer: "dòng chữ" in the description made it a text card
        "c509": seen("c509", "Bản đồ địa cầu đen trắng vùng châu Âu, có mũi tên hai chiều kèm dòng chữ '1500 MILES' chỉ khoảng cách.",
                     ("bản đồ", "quả địa cầu", "mũi tên"), "1500 MILES", ("explanation", "text_on_screen"), 850),
        "c514": seen("c514", "Cảnh quay từ trên không, nhìn qua cánh máy bay xuống đồng ruộng và địa hình đồi núi.",
                     ("cánh máy bay", "đồng ruộng", "đồi núi"), "", ("establishing", "object"), 700),
        "c518": seen("c518", "Bản đồ hoạt hình đen trắng phần lãnh thổ phía tây Liên Xô được viền sáng, chữ lớn ghi diện tích 500.000 "
                     "dặm vuông, các thành phố được đánh dấu.", ("bản đồ", "đường viền lãnh thổ", "tên thành phố"),
                     "500,000 SQUARE MILES; FINLAND; Baltic Sea; MOSCOW; SMOLENSK; KURSK; KIEV", ("explanation", "text_on_screen", "establishing"), 850),
        "c519": seen("c519", "Cảnh tư liệu đen trắng: đoàn binh sĩ đội mũ sắt, vác súng hành quân trên con đường đất giữa cánh đồng.",
                     ("binh sĩ", "mũ sắt", "con đường đất"), "", ("establishing", "people", "action"), 880),
    }
    if keep:
        pool = [c for c in pool if c.candidate_id in keep]
        found = {k: v for k, v in found.items() if k in keep}
    return pool, found


def card_shots(with_card: bool = True):
    pool = [shot("c02", 1_166, 10_143), shot("c04", 16_383, 22_383), shot("c06", 28_383, 34_383)]
    found = {
        "c02": seen("c02", "Màn hình nền xám với dòng chữ trắng tiếng Anh, thông báo phim có giá trị lịch sử và không nhất thiết phản "
                    "ánh chính sách hiện tại của cơ quan tài trợ.", (), "This film is considered of historical value and does not "
                    "necessarily reflect current policy", ("text_on_screen",), 950),
        "c04": seen("c04", "Màn hình chữ trắng trên nền tối, đoạn văn tiếng Anh nói về truyền thống lịch sử của đồng minh.",
                    ("chữ trên màn hình",), "power that animates our enemies springs from their historic past",
                    ("text_on_screen", "explanation")),
        "c06": seen("c06", "Màn hình chữ trắng trên nền tối, đoạn giới thiệu bằng tiếng Anh về nguồn tư liệu của phim.",
                    ("chữ trên màn hình",), "Therefore, in the film you are about to see, free use has been made of motion pictures "
                    "that illustrate this historical background.", ("text_on_screen", "explanation"), 950),
    }
    if not with_card:
        pool = [c for c in pool if c.candidate_id != "c02"]
        del found["c02"]
    return pool, found


class ReadingClaimsTests(unittest.TestCase):
    def test_spelled_and_written_numbers_read_the_same(self):
        for text in ("một nghìn năm trăm dặm", "1500 dặm", "1.500 dặm", "1,500 miles", "1500 MILES", "một ngàn năm trăm dặm"):
            self.assertEqual(said.quantities(text), [said.Quantity(1500, "mile", "mile")], text)
        self.assertEqual(said.quantities("500.000 dặm vuông")[0], said.Quantity(500_000, "sq_mile", "mile"))
        self.assertEqual(said.quantities("500,000 SQUARE MILES")[0], said.Quantity(500_000, "sq_mile", "mile"))

    def test_vietnamese_number_words(self):
        cases = {"hai mươi lăm": 25, "hai mươi mốt": 21, "mười lăm": 15, "một trăm linh năm": 105, "năm trăm": 500,
                 "ba triệu": 3_000_000, "một triệu hai trăm nghìn": 1_200_000, "bốn mươi nghìn": 40_000}
        for text, value in cases.items():
            self.assertEqual([q.value for q in said.quantities(text)], [value], text)

    def test_words_that_only_look_like_numbers(self):
        self.assertEqual([q.value for q in said.quantities("hai mươi năm sau")], [20])  # twenty years, not 25
        self.assertEqual(said.quantities("hai người lính và năm chiếc xe"), [])  # no multiplier: no amount to show
        self.assertEqual(said.quantities("tư liệu năm xưa"), [])
        self.assertEqual(said.quantities("Năm 1941 quân Đức tấn công"), [said.Quantity(1941, "year", "year")])
        self.assertEqual(said.quantities("giảm hai mươi phần trăm"), [said.Quantity(20, "%", "%")])

    def test_claims_of_the_three_sentences(self):
        self.assertEqual(said.of(MAP).quantities, (said.Quantity(1500, "mile", "mile"),))
        self.assertTrue(said.of(MAP).display)
        self.assertEqual(said.of(TITLE).phrases, ("The Battle of Russia",))
        self.assertEqual(set(said.of(CARD).written), {"phim", "giá", "trị", "lịch", "sử"})

    def test_a_plain_scene_states_nothing_displayed(self):
        for text in ("Những người lính đội mũ sắt tròn nhìn thẳng vào ống kính trong lúc hành quân.",
                     "Các câu trích dẫn ký tên Henry Stimson và Frank Knox ca ngợi nhân dân Liên Xô.",
                     "Lá cờ chữ thập ngoặc tung bay trên quảng trường."):
            self.assertFalse(said.of(text).display, text)
            self.assertFalse(said.of(text).phrases or said.of(text).written, text)

    def test_check_verifies_contradicts_or_leaves_unverified(self):
        wanted = said.of(MAP)
        _, found = map_shots()
        self.assertEqual(said.check(wanted, said.content_text(found["c509"])).verified, ["1.500 dặm"])
        self.assertEqual(said.check(wanted, said.content_text(found["c518"])).conflicts, ["1.500 dặm"])
        self.assertEqual(said.check(wanted, said.content_text(found["c505"])).unverified, ["1.500 dặm"])
        close = seen("x", "Bản đồ có mũi tên", (), "1550 MILES", ("explanation",))
        self.assertEqual(said.check(wanted, said.content_text(close)).conflicts, ["1.500 dặm"])  # A4 close number

    def test_a_file_name_verifies_nothing(self):
        named = ev.Evidence("n")
        named.add(ev.Item("metadata", 700, "map 1500 miles", (), "tên file"))
        self.assertEqual(said.check(said.of(MAP), said.content_text(named)).verified, [])


class ReviewFindingsTests(unittest.TestCase):
    """A4: what an independent review of the first fix found (#808): claims that are no displayed fact."""

    def test_square_kilometres_written_km2(self):
        for text in ("500 km2", "500 km²", "500 km vuông"):
            self.assertEqual(said.quantities(text), [said.Quantity(500, "sq_km", "km")], text)
        result = said.check(said.of("Bản đồ diện tích 500 km vuông"), "a map with 500 km2")
        self.assertEqual((result.verified, result.conflicts), (["500 km²"], []))

    def test_a_head_count_in_a_picture_is_no_displayed_figure(self):
        result = said.check(said.of("Hai mươi nghìn lính tiến về Moskva"), "3 người lính đứng bên đường")
        self.assertEqual(result.conflicts, [])
        # nor is a number spoken over a scene a figure the shot must show: never a reason to review
        pool, found = map_shots("c514", "c519")
        found["c519"] = seen("c519", "Đoàn binh sĩ đội mũ sắt hành quân trên đường đất, 3 người lính đi đầu.", ("binh sĩ",), "",
                             ("establishing", "people"), 880)
        chosen, ready, decision = plan("Hai mươi nghìn binh sĩ hành quân trên con đường đất.", pool, found, length=2800)
        self.assertEqual(chosen, ["c519"])
        self.assertTrue(ready, decision.to_dict())
        # ... but the same count verifies
        self.assertEqual(said.check(said.of("Biểu đồ: 20.000 lính"), "chart: 20,000 soldiers").verified, ["20.000 người"])

    def test_quoted_speech_is_not_written(self):
        stated = said.of('Stalin nói: "Không lùi một bước"')
        self.assertEqual((stated.phrases, stated.display), ((), False))
        self.assertEqual(said.of('Khẩu hiệu "Victory or death" kẻ trên tường').phrases, ("Victory or death",))

    def test_a_small_bare_number_is_no_fact_to_show(self):
        self.assertEqual(said.of("Bản đồ cho thấy 3 mũi tên").quantities, ())
        self.assertEqual(said.of("mười năm sau bản đồ thay đổi").quantities, ())
        self.assertFalse(align.about_text(align.Unit("u", "mười năm sau bản đồ thay đổi", 0, 2000, "narration")))
        # a bare "3" in the evidence never stands for "3 dặm"
        self.assertEqual(said.check(said.of("Bản đồ đánh dấu 3 dặm"), "a map, 3 soldiers").verified, [])

    def test_a_date_keeps_only_its_year(self):
        stated = said.of("Bản đồ ngày 22/6/1941")
        self.assertEqual([q.value for q in stated.quantities], [1941])
        self.assertEqual(said.check(stated, "map captioned June 1941").verified, ["1.941"])

    def test_the_sea_is_not_a_sign(self):
        stated = said.of("Trên biển North Sea hạm đội Anh thông báo chiến thắng")
        self.assertEqual((stated.phrases, stated.written, stated.display), ((), (), False))
        self.assertEqual(said.of("Biển báo Danger Zone Ahead dựng ven đường").phrases, ("Danger Zone Ahead",))

    def test_a_digit_with_a_scale_word(self):
        self.assertEqual(said.quantities("2 tỷ người"), [said.Quantity(2_000_000_000, "people", "people")])
        self.assertEqual(said.quantities("2 tỷ người"), said.quantities("hai tỷ người"))

    def test_labels_fit_the_contract(self):
        long = "Tấm thẻ chữ cho biết " + " ".join(f"từ{i} điều{i}" for i in range(40))
        stated = said.of(long)
        label = said.check(stated, "").unverified[0]
        self.assertLessEqual(len(label), 200)


class LeadFindingsTests(unittest.TestCase):
    """A4: what the Lead's review of #808 found."""

    def test_speech_is_no_proof_of_what_the_frame_shows(self):
        spoken = ev.Evidence("t")
        spoken.add(ev.Item("transcript", 900, "Quân Đức tiến một nghìn năm trăm dặm, 1500 miles", (), "phụ đề nguồn"))
        spoken.add(ev.Item("vision", 850, "Bản đồ châu Âu có mũi tên", (), "AI test"))
        self.assertEqual(said.check(said.of(MAP), said.content_text(spoken)).verified, [])
        read = ev.Evidence("o")
        read.add(ev.Item("transcript", 900, "1500 MILES", (), "OCR chữ trên hình"))
        self.assertEqual(said.check(said.of(MAP), said.content_text(read)).verified, ["1.500 dặm"])

    def test_a_bare_number_does_not_prove_an_amount_with_a_unit(self):
        self.assertEqual(said.check(said.of(MAP), "Bản đồ có số 1500 ở góc").verified, [])
        self.assertEqual(said.check(said.of("Biểu đồ ghi năm 1941"), "chart 1941").verified, ["năm 1941"])

    def test_a_title_is_its_words_in_order(self):
        stated = said.of(TITLE)
        self.assertEqual(said.check(stated, "THE BATTLE OF RUSSIA; WHY WE FIGHT").verified, ["The Battle of Russia"])
        self.assertEqual(said.check(stated, "THE BATTLE\nOF RUSSIA").verified, ["The Battle of Russia"])
        self.assertEqual(said.check(stated, "Russia map; the battle lines of the front").verified, [])
        self.assertEqual(said.check(stated, "battle scene, soldiers, snow; flag of Russia").verified, [])


class NoteWordingTests(unittest.TestCase):  # rc16 W1: the note read "(giá lịch phim sử trị)"
    def test_a_cards_words_are_quoted_in_the_sentences_order(self):
        words = said.of(CARD).written
        self.assertEqual(" ".join(words), "phim giá trị lịch sử")
        pool, found = card_shots(with_card=False)
        _, _, decision = plan(CARD, pool, found, length=3000)
        self.assertTrue(any("(phim giá trị lịch sử)" in n for n in decision.notes), decision.notes)

    def test_the_order_does_not_change_what_verifies(self):
        claims = said.of(CARD)
        flipped = said.Claims(claims.quantities, claims.phrases, tuple(reversed(claims.written)), claims.display)
        text = "Dòng chữ: bộ phim này có giá trị lịch sử"
        self.assertEqual(said.check(claims, text).verified != [], said.check(flipped, text).verified != [])


class TitleTests(unittest.TestCase):
    """A1 i1.u2."""

    def test_the_card_that_shows_the_title_wins(self):
        pool, found = title_shots()
        chosen, ready, decision = plan(TITLE, pool, found)
        self.assertEqual(chosen[0], "c11", decision.to_dict())
        self.assertEqual(decision.signals["claims"]["verified"], ["The Battle of Russia"])

    def test_without_the_title_another_eagle_card_is_never_ready(self):
        pool, found = title_shots(with_title=False)
        chosen, ready, decision = plan(TITLE, pool, found)
        self.assertFalse(ready, decision.to_dict())
        self.assertTrue(any("The Battle of Russia" in n for n in decision.notes), decision.notes)

    def test_a_similar_title_is_not_the_title(self):
        pool, found = title_shots()
        found["c11"] = seen("c11", "Màn hình tiêu đề phim đen trắng với chữ lớn trên nền biểu tượng chim đại bàng.",
                            ("chữ tiêu đề", "biểu tượng đại bàng"), "THE BATTLE OF BRITAIN; WHY WE FIGHT", ("text_on_screen",))
        _, ready, decision = plan(TITLE, pool, found)
        self.assertFalse(ready, decision.to_dict())


class MapTests(unittest.TestCase):
    """A2 i16.u1."""

    def test_the_map_with_the_distance_wins_even_when_described_as_text(self):
        pool, found = map_shots()
        self.assertTrue(align._text_card(found["c509"]))  # rc15's wording: a card by its description
        chosen, ready, decision = plan(MAP, pool, found, length=2800)
        self.assertEqual(chosen, ["c509"], decision.to_dict())
        self.assertTrue(ready, decision.to_dict())

    def test_a_map_of_another_amount_is_never_ready(self):
        pool, found = map_shots("c514", "c518", "c519")
        chosen, ready, decision = plan(MAP, pool, found, length=2800)
        self.assertFalse(ready, decision.to_dict())
        self.assertNotIn("c518", decision.signals.get("claims", {}).get("verified", []))
        self.assertTrue(any("con số khác" in n or "chưa thấy" in n for n in decision.notes), decision.notes)

    def test_a_map_without_the_number_is_never_ready(self):
        pool, found = map_shots("c505", "c514", "c519")
        chosen, ready, decision = plan(MAP, pool, found, length=2800)
        self.assertEqual(chosen, ["c505"])
        self.assertFalse(ready, decision.to_dict())  # A4: large margin, evidence short of the fact

    def test_two_maps_the_number_decides(self):
        pool, found = map_shots("c509", "c518")
        chosen, ready, _ = plan(MAP, pool, found, length=2800)
        self.assertEqual(chosen, ["c509"])
        self.assertTrue(ready)

    def test_a_long_generic_description_is_no_proof(self):
        pool, found = map_shots("c505", "c514")
        found["c505"] = seen("c505", "Bản đồ lớn có đường thẳng đánh dấu khoảng cách, mũi tên chỉ đường, các dặm đường được đánh dấu "
                             "trên bản đồ chiến dịch, đường kẻ dài nối hai thành phố.", ("bản đồ", "đường thẳng", "mũi tên"),
                             "", ("explanation",), 900)
        _, ready, decision = plan(MAP, pool, found, length=2800)
        self.assertFalse(ready, decision.to_dict())

    def test_small_margin_between_two_maps_without_the_number(self):
        pool, found = map_shots("c505", "c518")
        found["c518"] = seen("c518", "Bản đồ hoạt hình vùng Ba Lan, mũi tên đánh dấu đường tiến quân.", ("bản đồ", "mũi tên"),
                             "POLAND; WARSAW", ("explanation", "text_on_screen"), 850)
        _, ready, _ = plan(MAP, pool, found, length=2800)
        self.assertFalse(ready)

    def test_rewordings_of_the_number_all_verify(self):
        for visible in ("1,500 MILES", "1.500 MILES", "1500 miles"):
            pool, found = map_shots("c509", "c514")
            found["c509"] = seen("c509", "Quả địa cầu với mũi tên hai chiều chỉ khoảng cách.", ("quả địa cầu", "mũi tên"), visible,
                                 ("explanation", "text_on_screen"), 850)
            chosen, ready, decision = plan(MAP, pool, found, length=2800)
            self.assertEqual(chosen, ["c509"], visible)
            self.assertTrue(ready, (visible, decision.to_dict()))


class StatedCardTests(unittest.TestCase):
    """A3 i1.u1."""

    def test_the_card_that_says_it_is_ready(self):
        pool, found = card_shots()
        chosen, ready, decision = plan(CARD, pool, found, length=4100)
        self.assertEqual(chosen, ["c02"])
        self.assertTrue(ready, decision.to_dict())

    def test_a_card_about_something_else_is_never_ready(self):
        pool, found = card_shots(with_card=False)
        _, ready, decision = plan(CARD, pool, found, length=4100)
        self.assertFalse(ready, decision.to_dict())
        self.assertTrue(any("chưa thấy" in n for n in decision.notes), decision.notes)

    def test_a_scene_is_not_the_card(self):
        pool = [shot("s1", 0, 6000), shot("s2", 6000, 12_000)]
        found = {"s1": seen("s1", "Đoàn xe tăng chạy trên cánh đồng, khói bụi mù mịt.", ("xe tăng",), "", ("action",)),
                 "s2": seen("s2", "Một người đàn ông đọc báo trong phòng, có tấm thẻ trên bàn.", ("tấm thẻ",), "", ("people",))}
        _, ready, _ = plan(CARD, pool, found, length=4100)
        self.assertFalse(ready)


class CacheProvenanceTests(unittest.TestCase):
    def test_a_cached_answer_records_what_made_it_and_is_read_back_the_same(self):
        import json
        from pathlib import Path
        import tempfile
        from engine.dubflow.mastering import vision as seeing

        answer = {"frames": [{"frame": 1, "description": "Quả địa cầu với mũi tên", "objects": [], "actions": [],
                              "visible_text": "1500 MILES", "labels": ["explanation"], "confidence": 850, "clarity": "clear", "hook": 0}]}
        calls = []

        def ask(system, schema, parts):
            calls.append(1)
            return answer, "model-b"
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "k.jpg"
            frame.write_bytes(b"\xff\xd8 frame")
            first = seeing.Describer(ask, provider="p", model="model-a", cache=Path(tmp) / "cache").describe([("c1", frame)])
            record = json.loads(next((Path(tmp) / "cache").glob("*.json")).read_text(encoding="utf-8"))
            again = seeing.Describer(ask, provider="p", model="model-a", cache=Path(tmp) / "cache").describe([("c1", frame)])
        self.assertEqual(record["made_by"], {"prompt": seeing.PROMPT_VERSION, "provider": "p", "model": "model-a",
                                             "answered_by": "model-b"})
        self.assertEqual(len(calls), 1)  # the second run reads the cache
        self.assertEqual(first, again)


class NotEverythingNeedsReviewTests(unittest.TestCase):
    """A5: what is verified, and what states nothing displayed, can still be READY."""

    def test_a_plain_scene_is_ready_as_before(self):
        pool, found = map_shots("c514", "c519")
        chosen, ready, decision = plan("Đoàn binh sĩ đội mũ sắt hành quân trên con đường đất giữa cánh đồng.", pool, found, length=2800)
        self.assertEqual(chosen, ["c519"])
        self.assertTrue(ready, decision.to_dict())
        self.assertNotIn("claims", decision.signals)

    def test_an_unshown_number_in_a_plain_sentence_is_not_a_reason_to_review(self):
        pool, found = map_shots("c514", "c519")
        chosen, ready, decision = plan("Hơn ba triệu lính hành quân trên con đường đất giữa cánh đồng.", pool, found, length=2800)
        self.assertEqual(chosen, ["c519"])
        self.assertTrue(ready, decision.to_dict())

    def test_a_named_place_on_a_map_is_ready(self):
        pool, found = map_shots("c505", "c514", "c519")
        chosen, ready, decision = plan("Bản đồ vùng Balkan với tên YUGOSLAVIA ở giữa.", pool, found, length=2800)
        self.assertEqual(chosen, ["c505"])
        self.assertTrue(ready, decision.to_dict())

    def test_one_dominant_verified_candidate(self):
        pool, found = map_shots("c509", "c514", "c519")
        chosen, ready, _ = plan(MAP, pool, found, length=2800)
        self.assertEqual(chosen, ["c509"])
        self.assertTrue(ready)

    def test_the_contract_lists_the_claims(self):
        import json
        from pathlib import Path
        schema = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "mastering" / "editorial-v1.json")
                            .read_text(encoding="utf-8"))["$defs"]["unit"]["properties"]["signals"]
        pool, found = map_shots("c505", "c514")
        _, _, decision = plan(MAP, pool, found, length=2800)
        claims = decision.to_dict()["signals"]["claims"]
        self.assertNotIn("claims", schema["required"])  # optional: only sentences that state something displayed
        self.assertEqual(set(schema["properties"]["claims"]["required"]), set(claims))
        self.assertEqual(set(decision.to_dict()["signals"]) - {"claims"}, set(schema["required"]))

    def test_a_pinned_shot_is_the_users_choice(self):
        pool, found = map_shots("c505", "c514")
        unit = align.Unit("u", MAP, 0, 2800, "narration")
        _, decisions, _ = align.assign([unit], pool, found, PACING, forward_only=True, edits=align.Edits(pin={"u": ["c505"]}))
        self.assertNotIn("claims", decisions[0].signals)


class PinLeftOutTests(unittest.TestCase):  # #805 x #802: an independent review of the merged branch
    def test_a_pin_an_exclusion_left_out_is_no_users_choice(self):
        # u is pinned to the "1500 MILES" globe, and another exclusion recorded that footage (under another id, as a
        # new split numbers it): the pin cannot play, the AI picks a map without the number, and the claim is checked
        pool, found = map_shots("c505", "c509", "c514")
        unit = align.Unit("u", MAP, 0, 2800, "narration")
        edits = align.Edits(pin={"u": ["c509"]}, exclude={"x"}, exclude_ranges={"x": (("s1", 1_585_000, 1_590_000),)})
        choices, decisions, _ = align.assign([unit], pool, found, PACING, forward_only=True, edits=edits)
        decision = decisions[0]
        self.assertNotIn("c509", [c.candidate.candidate_id for c in choices])
        ready = not decision.coverage.get("needs_review") and not any(c.needs_review for c in choices)
        self.assertFalse(ready, decision.to_dict())
        self.assertIn("claims", decision.signals)
        self.assertTrue(any("ghim" in n for n in decision.notes), decision.notes)



# --- #817: a number is tied to what it counts ---------------------------------------------------------------------
# Synthetic cases written from what a chart or a caption shows, never from the W3 labels (FROZEN): a count of people
# is shown only by a figure the same evidence line ties to people.

POPULATION = "Biểu đồ cho thấy dân số Liên Xô là 193.000.000 người."
HEADS = "Biểu đồ ghi 1.500 người."


def verdict(sentence: str, evidence: str) -> tuple[list[str], list[str], list[str], list[str]]:
    result = said.check(said.of(sentence), evidence)
    return result.verified, result.unverified, result.conflicts, result.disputed


def chart_shots(visible: str, description: str = "Biểu đồ đen trắng với dòng chữ lớn về dân số Liên Xô.") -> tuple[list, dict]:
    pool = [shot("c1", 10_000, 14_000), shot("c2", 20_000, 24_000)]
    found = {"c1": seen("c1", description, ("biểu đồ", "dòng chữ"), visible, ("explanation", "text_on_screen"), 800),
             "c2": seen("c2", "Cánh đồng lúa mì rộng, nông dân gặt lúa.", ("cánh đồng", "nông dân"), "", ("establishing",), 850)}
    return pool, found


class CountContextTests(unittest.TestCase):
    def test_people_count_in_the_right_context_is_confirmed(self):
        for evidence in ("POPULATION 193,000,000", "population of about 193.000.000", "193,000,000 INHABITANTS",
                         "dân số 193 triệu", "Dân số: 193.000.000", "193,000,000 PEOPLE", "dân số năm 1941 là 193 triệu"):
            self.assertEqual(verdict(POPULATION, evidence)[0], ["193.000.000 người"], evidence)
        self.assertEqual(verdict(HEADS, "1,500 PEOPLE")[0], ["1.500 người"])  # #802: the unit after the figure, as before

    def test_people_count_in_the_wrong_context_stays_unverified(self):
        for evidence in ("193,000,000", "AREA 193,000,000", "193,000,000 MILES", "NO. 193,000,000",
                         "POPULATION\n193,000,000", "POPULATION; 193,000,000"):  # another line: the word is not on it
            verified, unverified, conflicts, disputed = verdict(POPULATION, evidence)
            self.assertEqual((verified, unverified, conflicts, disputed), ([], ["193.000.000 người"], [], []), evidence)
        # one vision item says "population", another (OCR) item has the figure: a word never crosses evidence items
        found = ev.Evidence("x")
        found.add(ev.Item("vision", 800, "Biểu đồ về dân số", (), "AI test"))
        found.add(ev.Item("transcript", 900, "193,000,000", (), "OCR chữ trên hình"))
        self.assertEqual(said.check(said.of(POPULATION), said.content_text(found)).verified, [])

    def test_the_same_figure_with_another_unit_never_confirms_a_count(self):
        for evidence in ("1500 MILES", "POPULATION 1500 MILES", "POPULATION\n 1500 MILES", "dân số 1500 km", "1500 tấn"):
            verified, _, conflicts, disputed = verdict(HEADS, evidence)
            self.assertEqual((verified, conflicts, disputed), ([], [], []), evidence)

    def test_vietnamese_number_words(self):
        self.assertEqual(verdict("Biểu đồ ghi một trăm chín mươi ba triệu người.", "POPULATION 193,000,000")[0],
                         ["193.000.000 người"])
        self.assertEqual(verdict("Biểu đồ ghi một nghìn năm trăm người.", "dân số một nghìn năm trăm")[0], ["1.500 người"])
        self.assertEqual(said.quantities("dân số một nghìn năm trăm"), [said.Quantity(1500, None, None, "people")])

    def test_separators(self):
        for evidence in ("POPULATION 1.500", "POPULATION 1,500", "POPULATION 1500", "POPULATION 1 500", "POPULATION 1\u00a0500"):
            self.assertEqual(verdict(HEADS, evidence)[0], ["1.500 người"], evidence)
        for sentence in ("Biểu đồ ghi 1 500 người.", "Biểu đồ ghi 1,500 người.", "Biểu đồ ghi 1500 người."):
            self.assertEqual(verdict(sentence, "1,500 PEOPLE")[0], ["1.500 người"], sentence)

    def test_english_scale_words(self):
        self.assertEqual(said.quantities("193 MILLION"), [said.Quantity(193_000_000)])
        self.assertEqual(said.quantities("20 THOUSAND soldiers"), [said.Quantity(20_000, "people", "people")])
        self.assertEqual(said.quantities("2 billion people"), [said.Quantity(2_000_000_000, "people", "people")])
        self.assertEqual(said.quantities("1.5 million"), [said.Quantity(1_500_000)])
        self.assertEqual(said.quantities("1,5 tỷ người"), [said.Quantity(1_500_000_000, "people", "people")])
        self.assertEqual(verdict(POPULATION, "POPULATION 193 MILLION")[0], ["193.000.000 người"])
        self.assertEqual(verdict(POPULATION, "POPULATION 193")[3], ["193.000.000 người"])  # 193 is not 193 million

    def test_dates_and_years_never_confirm_a_count(self):
        for evidence in ("năm 1500", "year 1500", "in 1500", "22/6/1500", "June 1500", "population in 1500"):
            verified, _, conflicts, disputed = verdict(HEADS, evidence)
            self.assertEqual((verified, conflicts, disputed), ([], [], []), evidence)
        # a year next to a population word is the year; the figure after it is the population
        self.assertEqual(said.quantities("dân số năm 1941 là 193 triệu"),
                         [said.Quantity(1941, "year", "year"), said.Quantity(193_000_000, None, None, "people")])

    def test_prices_never_confirm_a_count(self):
        for evidence in ("mỗi người 1.500 đồng", "người 1500đ", "population $1500", "dân số 1500 USD", "giá 1.500 đồng"):
            verified, _, conflicts, disputed = verdict(HEADS, evidence)
            self.assertEqual((verified, conflicts, disputed), ([], [], []), evidence)

    def test_a_distance_never_confirms_a_count_and_the_reverse_hole_is_closed(self):
        for evidence in ("1500 MILES", "1500 km", "1.500 dặm"):
            self.assertEqual(verdict(HEADS, evidence)[0], [], evidence)
        # a unit-less figure after "dân số" counts people: a distance does not show it (it did before #817)
        stated = said.of("Biểu đồ dân số 1500")
        self.assertEqual(stated.quantities, (said.Quantity(1500, None, None, "people"),))
        for evidence in ("1500 miles", "POPULATION\n 1500 MILES", "1500"):
            self.assertEqual(said.check(stated, evidence).verified, [], evidence)
        self.assertEqual(said.check(stated, "POPULATION 1500").verified, ["1.500"])
        self.assertEqual(said.check(said.of("Biểu đồ dân số 1.500"), "1,500 PEOPLE").verified, ["1.500"])

    def test_an_area_never_confirms_a_count(self):
        for evidence in ("1500 km2", "1,500 SQUARE MILES", "dân số 1500 km vuông"):
            verified, _, conflicts, disputed = verdict(HEADS, evidence)
            self.assertEqual((verified, conflicts, disputed), ([], [], []), evidence)

    def test_an_ordinal_or_an_age_never_confirms_a_count(self):
        for evidence in ("No. 1500", "#1500", "the 1500th citizen", "người 1500 tuổi"):
            verified, _, conflicts, disputed = verdict(HEADS, evidence)
            self.assertEqual((verified, conflicts, disputed), ([], [], []), evidence)

    def test_a_vague_vision_description_is_no_proof(self):
        verified, unverified, conflicts, disputed = verdict(POPULATION, "Biểu đồ với dòng chữ lớn về dân số Liên Xô")
        self.assertEqual((verified, unverified, conflicts, disputed), ([], ["193.000.000 người"], [], []))

    def test_ocr_read_right_but_not_proving_the_claim(self):
        read = ev.Evidence("o")
        read.add(ev.Item("transcript", 900, "193,000,000", (), "OCR chữ trên hình"))  # the right digits, tied to nothing
        self.assertEqual(said.check(said.of(POPULATION), said.content_text(read)).verified, [])
        read.add(ev.Item("transcript", 900, "POPULATION 193,000,000", (), "OCR chữ trên hình"))
        self.assertEqual(said.check(said.of(POPULATION), said.content_text(read)).verified, ["193.000.000 người"])

    def test_another_figure_for_the_population_is_disputed_never_a_conflict(self):
        verified, unverified, conflicts, disputed = verdict(POPULATION, "POPULATION 93,000,000")
        self.assertEqual((verified, unverified, conflicts, disputed), ([], ["193.000.000 người"], [], ["193.000.000 người"]))
        # a head count in a description is no population figure (#808)
        self.assertEqual(verdict("Biểu đồ: 20.000 lính", "3 người lính đứng bên đường")[3], [])
        self.assertEqual(verdict("Biểu đồ: 20.000 lính", "3 persons standing")[3], [])

    def test_a_misread_population_does_not_move_the_choice(self):
        pool, found = chart_shots("POPULATION 93,000,000")
        chosen, ready, decision = plan(POPULATION, pool, found, length=3000)
        self.assertEqual(chosen, ["c1"], decision.to_dict())  # still the chart: no penalty, not dropped from accepts
        self.assertFalse(ready, decision.to_dict())
        claims = decision.signals["claims"]
        self.assertEqual((claims["conflicts"], claims["disputed"]), ([], ["193.000.000 người"]))
        self.assertEqual(said.statuses(claims), [("193.000.000 người", said.CONTRADICTED)])
        self.assertTrue(any("con số khác" in n for n in decision.notes), decision.notes)

    def test_the_chart_with_the_population_is_ready(self):
        pool, found = chart_shots("POPULATION 193,000,000")
        chosen, ready, decision = plan(POPULATION, pool, found, length=3000)
        self.assertEqual(chosen, ["c1"], decision.to_dict())
        self.assertTrue(ready, decision.to_dict())
        self.assertEqual(said.statuses(decision.signals["claims"]), [("193.000.000 người", said.CONFIRMED)])
        self.assertNotIn("disputed", decision.signals["claims"])  # absent when empty: logs read as before

    def test_a_claim_split_over_pieces_is_checked_on_every_piece(self):
        # the sentence outlasts the chart: two pieces, neither shows the count; the misread one makes it disputed
        pool, found = chart_shots("POPULATION 93,000,000")
        pool.append(shot("c3", 30_000, 34_000))
        found["c3"] = seen("c3", "Biểu đồ khác về Liên Xô.", ("biểu đồ",), "193,000,000", ("explanation",), 800)
        chosen, ready, decision = plan(POPULATION, pool, found, length=7000)
        self.assertEqual(sorted(chosen), ["c1", "c3"], decision.to_dict())
        self.assertFalse(ready)
        self.assertEqual(said.statuses(decision.signals["claims"]), [("193.000.000 người", said.CONTRADICTED)])
        # ... and a piece that shows it, tied to people, confirms it for the sentence
        found["c3"] = seen("c3", "Biểu đồ khác về Liên Xô.", ("biểu đồ",), "POPULATION 193,000,000", ("explanation",), 800)
        _, _, decision = plan(POPULATION, pool, found, length=7000)
        self.assertEqual(said.statuses(decision.signals["claims"]), [("193.000.000 người", said.CONFIRMED)])

    def test_a_disputed_count_fits_the_contract(self):
        import json
        from pathlib import Path
        schema = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "mastering" / "editorial-v1.json")
                            .read_text(encoding="utf-8"))["$defs"]["unit"]["properties"]["signals"]["properties"]["claims"]
        pool, found = chart_shots("POPULATION 93,000,000")
        _, _, decision = plan(POPULATION, pool, found, length=3000)
        claims = decision.to_dict()["signals"]["claims"]
        self.assertIn("disputed", claims)
        self.assertLessEqual(set(claims), set(schema["properties"]))
        self.assertLessEqual(set(schema["required"]), set(claims))
        self.assertNotIn("disputed", schema["required"])
        self.assertLessEqual(set(claims["disputed"]), set(claims["unverified"]))


class StatusTests(unittest.TestCase):
    def test_statuses_map_from_signals(self):
        claims = {"display": True, "verified": ["The Battle of Russia", "1.500 dặm"], "unverified": ["phim giá trị"],
                  "conflicts": ["1.500 dặm"]}  # rc15 i16.u1: verified by one piece, contradicted by another
        self.assertEqual(said.statuses(claims), [("1.500 dặm", said.CONTRADICTED), ("phim giá trị", said.UNVERIFIED),
                                                 ("The Battle of Russia", said.CONFIRMED)])
        self.assertEqual(said.unresolved(claims), ["1.500 dặm", "phim giá trị"])
        disputed = {"display": True, "verified": [], "unverified": ["193.000.000 người"], "conflicts": [],
                    "disputed": ["193.000.000 người"]}
        self.assertEqual(said.statuses(disputed), [("193.000.000 người", said.CONTRADICTED)])
        plain = dict(claims, display=False)
        self.assertEqual({s for _, s in said.statuses(plain)}, {said.NOT_APPLICABLE})
        self.assertEqual(said.unresolved(plain), [])
        self.assertEqual((said.statuses(None), said.unresolved({})), ([], []))

    def test_merged_pieces(self):
        right = said.check(said.of(POPULATION), "POPULATION 193,000,000")
        misread = said.check(said.of(POPULATION), "POPULATION 93,000,000")
        bare = said.check(said.of(POPULATION), "a field")
        self.assertEqual(said.merged([misread, right]).to_dict(),
                         {"verified": ["193.000.000 người"], "unverified": [], "conflicts": []})
        self.assertEqual(said.merged([bare, misread]).disputed, ["193.000.000 người"])

    def test_a_user_note_never_proves_what_is_displayed(self):
        noted = ev.Evidence("n")
        noted.add(ev.Item("user", 1000, "POPULATION 193,000,000", (), "ghi chú"))
        self.assertEqual(said.content_text(noted), "")
        self.assertEqual(said.check(said.of(POPULATION), said.content_text(noted)).verified, [])


if __name__ == "__main__":
    unittest.main()

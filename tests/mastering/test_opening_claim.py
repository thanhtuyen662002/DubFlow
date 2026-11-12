"""P1-N1 (#764): a product's opening is READY only on footage that shows the product.

rc7 Windows check D3b: the first sentence of a Bán hàng script ("Chiếc nồi chiên không dầu này có lòng nồi chống
dính phủ ceramic.") was planned as a hook, evidence NOT_NEEDED, state READY, over a 3D domino shot whose vision
said "Không thấy sản phẩm". The first sentence of every sales script is a hook, and a hook without a match needed no
footage of its own, so nothing looked at what it showed.

The fix keeps generic openings generic (an opening with no match of its own is still NOT_NEEDED: a hook on any shot
of the product is fine, REAL-DEV v1-s1), but such an opening is READY only when a shot under it shows the product;
when the vision looked and saw no product under it, it is NEEDS_REVIEW with the reason in its notes. An opening
with a real match is unchanged and can be READY.
"""
from __future__ import annotations

from pathlib import Path
import random
import unittest

from engine.dubflow.mastering import candidates as shots
from engine.dubflow.mastering import evidence as ev
from engine.dubflow.mastering import plan as m
from engine.dubflow.mastering import review
from engine.dubflow.mastering.planners import product as planner

HOOK = "Chiếc nồi chiên không dầu này có lòng nồi chống dính phủ ceramic."
CTA = "Đặt mua ngay hôm nay để nhận ưu đãi."
SPOKEN = [(HOOK, 3000), ("Giỏ chiên rút ra dễ dàng.", 2400), ("Khoai tây chiên vàng giòn.", 2400), (CTA, 2400)]
TOTAL = 300 + 3000 + 2400 * 3 + 3 * 250 + 700


def candidate(cid: str, source: str, start: int, end: int) -> shots.Candidate:
    return shots.Candidate(cid, source, start, end, 0, 50, shots.Quality(120, 50, 300), random.getrandbits(64))


def seen(text: str, labels: tuple[str, ...], product: bool) -> ev.Item:
    return ev.Item("vision", 900, text, labels, "AI claude")


def plan(descriptions: list[tuple[str, tuple[str, ...], bool]], name: str = "noi chien khong dau chong dinh.mp4"):
    """One clip cut in four shots, each with what the vision said and whether it saw a product."""
    visuals = [planner.Visual(Path(name), 16_000, 1920, 1080)]
    pool = {0: [candidate(f"v1c{i + 1}", "v1", i * 4000, (i + 1) * 4000) for i in range(len(descriptions))]}
    found = planner.gather_evidence([v.path for v in visuals], pool)
    for (text, labels, product), c in zip(descriptions, pool[0]):
        found[c.candidate_id].add(seen(text, labels, product))
        found[c.candidate_id].product_visible = product
    made, log = planner.plan_editorial(visuals, SPOKEN, TOTAL, m.Options("balanced"), lead_ms=300, gap_ms=250,
                                       candidates=pool, evidence=found, facts="", inputs={})
    review.annotate(log, made.to_dict())  # as the jobs do
    return {u["unit_id"]: u for u in log["units"]}, log


DOMINO = [("Mô phỏng 3D hàng domino đổ dây chuyền trên nền xám. Không thấy sản phẩm.", ("action",), False)] * 4


class OpeningClaimTests(unittest.TestCase):
    def test_product_opening_over_footage_without_the_product_is_not_ready(self):
        units, log = plan(DOMINO)
        self.assertEqual(log["mode"], "content")
        hook = units["u1"]
        self.assertEqual(hook["function"], "hook")
        self.assertNotEqual(hook["review"]["state"], review.READY)  # rc7: READY
        self.assertEqual(hook["review"]["state"], review.NEEDS_REVIEW)
        self.assertTrue(any("không thấy sản phẩm" in n for n in hook["notes"]), hook["notes"])
        # the reason reaches the desktop: the view carries the notes of the sentence
        shown = {u["unit_id"]: u for u in review.view(log, None, review.Review())["units"]}
        self.assertIn("u1", review.unreviewed(log, review.Review()))
        self.assertTrue(any("không thấy sản phẩm" in n for n in shown["u1"]["notes"]))

    def test_opening_on_footage_that_shows_the_product_stays_ready(self):
        # REAL-DEV v1-s1: a hook with no words of its own on any shot of the machine is fine
        footage = [("Nồi chiên không dầu màu đen đặt trên bàn bếp, nhìn toàn thân.", ("product_overview",), True),
                   ("Tay rút giỏ chiên ra khỏi nồi.", ("product_demonstration",), True),
                   ("Khoai tây chiên vàng giòn trong giỏ.", ("product_result",), True),
                   ("Hộp nồi chiên và nồi đặt cạnh nhau.", ("product_packaging",), True)]
        units, _ = plan(footage)
        self.assertEqual(units["u1"]["review"]["state"], review.READY, units["u1"])

    def test_a_generic_opening_that_shows_the_product_is_still_not_needed(self):
        footage = [("Một căn bếp sáng sủa, có nồi chiên màu đen trên kệ.", ("establishing",), True),
                   ("Tay rút giỏ chiên ra khỏi nồi.", ("product_demonstration",), True),
                   ("Khoai tây chiên vàng giòn trong giỏ.", ("product_result",), True),
                   ("Hộp nồi chiên và nồi đặt cạnh nhau.", ("product_packaging",), True)]
        units, _ = plan(footage)
        self.assertEqual(units["u1"]["review"]["evidence"], review.NOT_NEEDED)
        self.assertFalse(any("không thấy sản phẩm" in n for n in units["u1"]["notes"]))

    def test_the_file_name_does_not_count_as_seeing_the_product(self):
        # P2-14 holds: the misleading name is the air fryer's, the vision saw dominoes
        units, _ = plan(DOMINO, name="noi chien khong dau long noi chong dinh phu ceramic.mp4")
        self.assertNotEqual(units["u1"]["review"]["state"], review.READY)

    def test_unknown_product_visibility_keeps_the_old_verdict(self):
        # evidence written without the product flag (a user's note, an old log): nothing says the product is absent
        visuals = [planner.Visual(Path("clip.mp4"), 16_000, 1920, 1080)]
        pool = {0: [candidate(f"v1c{i + 1}", "v1", i * 4000, (i + 1) * 4000) for i in range(4)]}
        found = planner.gather_evidence([v.path for v in visuals], pool)
        for c in pool[0]:
            found[c.candidate_id].add(ev.Item("user", 900, "căn bếp sáng, người nấu ăn", (), "ghi chú"))
        made, log = planner.plan_editorial(visuals, SPOKEN, TOTAL, m.Options("balanced"), lead_ms=300, gap_ms=250,
                                           candidates=pool, evidence=found, facts="", inputs={})
        review.annotate(log, made.to_dict())
        u1 = log["units"][0]
        self.assertFalse(any("không thấy sản phẩm" in n for n in u1.get("notes") or []))


if __name__ == "__main__":
    unittest.main()

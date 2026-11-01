from __future__ import annotations

import unittest

from engine.dubflow.subtitle.style import (
    FONTS,
    SubtitleStyle,
    SubtitleStyleError,
    ass_event_prefix,
    ass_style_line,
    escape_ass_text,
)

# Today's look, as production_job.ASS_STYLE_DEFAULT writes it.
TODAY = "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,0,2,40,40,48,1"


def style(**changes):
    value = {"font": "Arial", "size": 1, "primary": "#FFFFFF", "outline": "#000000", "box": None, "box_opacity": 0.6,
             "outline_width": 3, "bold": False, "align": "bottom"}
    value.update(changes)
    return value


class SubtitleStyleTests(unittest.TestCase):
    def test_twelve_vietnamese_capable_fonts(self):
        self.assertEqual(len(FONTS), 12)
        self.assertEqual(len(set(FONTS)), 12)
        for missing_vietnamese in ("Georgia", "Trebuchet MS", "Arial Black", "Impact", "Bahnschrift"):
            self.assertNotIn(missing_vietnamese, FONTS)

    def test_default_values_reproduce_todays_style_line(self):
        self.assertEqual(ass_style_line(SubtitleStyle.from_mapping(style())), TODAY)
        self.assertEqual(ass_event_prefix(SubtitleStyle.from_mapping(style())), "")

    def test_styled_line_maps_colours_size_box_and_alignment(self):
        chosen = SubtitleStyle.from_mapping(style(font="Segoe UI Black", size=1.25, primary="#fde047", outline="#1a2b3c",
                                                  box="#000000", box_opacity=0.75, outline_width=2.5, bold=True, align="top"))
        self.assertEqual(ass_style_line(chosen),
                         "Style: Default,Segoe UI Black,60,&H0047E0FD,&H000000FF,&H003C2B1A,&H40000000,-1,0,0,0,100,100,0,0,4,2.5,12,8,40,40,48,1")
        self.assertEqual(chosen.primary, "#FDE047", "colours are stored upper-case")

    def test_position_places_the_block_centre_on_the_1920x1080_canvas(self):
        placed = SubtitleStyle.from_mapping(style(x=0.5, y=0.25))
        self.assertEqual(ass_event_prefix(placed), r"{\an5\pos(960,270)}")
        self.assertEqual(placed.to_dict()["y"], 0.25)
        self.assertNotIn("x", SubtitleStyle.from_mapping(style()).to_dict())

    def test_invalid_styles_are_refused(self):
        for changes in ({"font": "Comic Sans MS"}, {"font": "Georgia"}, {"size": 0.5}, {"size": 2.01}, {"size": True},
                        {"primary": "white"}, {"primary": "#FFF"}, {"outline": None}, {"box": "#00000080"},
                        {"box_opacity": 1.5}, {"outline_width": 9}, {"outline_width": float("nan")}, {"bold": 1},
                        {"align": "left"}, {"x": 0.5}, {"x": 1.2, "y": 0.5}, {"extra": 1}):
            with self.subTest(changes=changes), self.assertRaises(SubtitleStyleError):
                SubtitleStyle.from_mapping(style(**changes))
        with self.assertRaises(SubtitleStyleError):
            SubtitleStyle.from_mapping({"font": "Arial"})
        with self.assertRaises(SubtitleStyleError):
            SubtitleStyle.from_mapping(["Arial"])

    def test_text_cannot_open_override_blocks(self):
        self.assertEqual(escape_ass_text(r"a {\pos(0,0)} b" + "\nc"), r"a \{\\pos(0,0)\} b\Nc")


if __name__ == "__main__":
    unittest.main()

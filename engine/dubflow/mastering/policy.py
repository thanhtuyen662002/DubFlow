"""What each workflow may do at each level, and how a style paces it (ADR-0052 §4).

A *workflow* fixes the goal and the operations allowed: dubbing keeps the
content and its order; a product video is built from the seller's own footage;
a compilation keeps its ranking; a documentary illustrates a new script. A
*level* (off, light, balanced, dynamic) opens more of those operations, and
means something different per workflow. A *style* only tunes the pacing inside
the bounds the workflow and level allow; it never grants an operation.

``check`` holds a plan to its policy, so a planner bug cannot ship a plan that
reorders a dubbed video, zooms past its bound, or zooms so much of the video that
the parallel export (``edit_parts.rs``) has no safe place to cut.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .plan import LEVELS, STYLES, WORKFLOWS, Options, Plan

# Operations a planner may perform.
SHOT_AWARE_CUTS = "shot_aware_cuts"      # cut only inside one shot of the source
TRIM_PAUSES = "trim_pauses"              # drop a measured silence between two pieces of speech
REORDER = "reorder"                      # show footage out of its source order
HOOK = "hook"                            # open with a teaser piece from later in the video
ZOOM = "zoom"                            # a gentle punch-in or push inside the protected boxes
DYNAMIC_CAPTIONS = "dynamic_captions"    # phrase / keyword / word-highlight captions on the final speech
COLOR_MATCH = "color_match"              # bring sources to one look (no strong sharpen, product colours kept)
LOUDNESS = "loudness_normalize"          # one loudness and a true-peak ceiling for the output
TRANSITIONS = "transitions"              # restrained transitions between items or chapters
SFX_ACCENTS = "sfx_accents"              # short sounds on item changes and key moments
TEXT_EMPHASIS = "text_emphasis"          # titles, numbers, product names on screen
OPERATIONS = (SHOT_AWARE_CUTS, TRIM_PAUSES, REORDER, HOOK, ZOOM, DYNAMIC_CAPTIONS, COLOR_MATCH, LOUDNESS, TRANSITIONS,
              SFX_ACCENTS, TEXT_EMPHASIS)

AUDIO_POLICIES = ("preserve_ambience", "narration_music", "narration_ambient", "mixed_commentary", "product_voiceover")
CAPTION_STYLES = ("plain", "phrase", "keyword", "word_highlight")
HOOK_MODES = ("off", "suggest", "auto")  # suggest: planned but flagged needs_review, never applied silently


@dataclass(frozen=True)
class Pacing:
    """Segment lengths in milliseconds a planner aims for."""

    minimum: int
    target: int
    maximum: int


# Starting points, not limits of the product: every planner may hold a shot longer when the content needs it to be
# understood (and says so in the segment's reasons).
STYLE_PACING: Mapping[str, Pacing] = {
    "natural": Pacing(2000, 3800, 6500),
    "dynamic": Pacing(1200, 2400, 4500),
    "cinematic": Pacing(2500, 5000, 8000),
    "technical": Pacing(2200, 4200, 7000),
    "entertainment": Pacing(1200, 2600, 5000),
    "sports": Pacing(900, 2000, 4000),
    "educational": Pacing(2200, 4000, 7000),
    "storytelling": Pacing(2000, 4200, 7500),
}
# A higher level paces a little faster, never under the style's minimum.
LEVEL_TEMPO = {"off": 1000, "light": 1100, "balanced": 1000, "dynamic": 850}


@dataclass(frozen=True)
class Policy:
    workflow: str
    options: Options
    operations: frozenset[str]
    pacing: Pacing
    max_zoom: int = 1000             # milli; 1000 = no zoom
    max_zoom_share: int = 0          # milli of the timeline that may be zoomed
    hook: str = "off"
    hook_max_ms: int = 0
    audio: str = "preserve_ambience"
    captions: str = "plain"
    trim_pause_min_ms: int | None = None
    keep_source_audio: bool = True

    def allows(self, operation: str) -> bool:
        if operation not in OPERATIONS:
            raise ValueError(f"unknown operation {operation!r}")
        return operation in self.operations

    @property
    def enabled(self) -> bool:
        return bool(self.operations)


def _ops(*names: str) -> frozenset[str]:
    return frozenset(names)


# (workflow, level) → what is allowed. "off" is absent: it allows nothing.
_TABLE: Mapping[tuple[str, str], dict] = {
    # Lồng tiếng: content, order and speech are kept. Light only levels the sound and the colour.
    ("dubbing_preserve", "light"): dict(operations=_ops(LOUDNESS, COLOR_MATCH)),
    ("dubbing_preserve", "balanced"): dict(operations=_ops(LOUDNESS, COLOR_MATCH, DYNAMIC_CAPTIONS, ZOOM),
                                           max_zoom=1080, max_zoom_share=200, captions="phrase"),
    ("dubbing_preserve", "dynamic"): dict(operations=_ops(LOUDNESS, COLOR_MATCH, DYNAMIC_CAPTIONS, ZOOM, HOOK, TRIM_PAUSES),
                                          max_zoom=1120, max_zoom_share=300, captions="word_highlight", hook="suggest",
                                          hook_max_ms=5000, trim_pause_min_ms=1200),
    # Bán hàng: the seller's footage, chosen per sentence (so order is free), voice-over first.
    ("product_showcase", "light"): dict(operations=_ops(SHOT_AWARE_CUTS, REORDER, LOUDNESS), audio="product_voiceover",
                                        keep_source_audio=False),
    ("product_showcase", "balanced"): dict(operations=_ops(SHOT_AWARE_CUTS, REORDER, LOUDNESS, HOOK, ZOOM, TEXT_EMPHASIS,
                                                           COLOR_MATCH, TRANSITIONS),
                                           max_zoom=1150, max_zoom_share=400, hook="auto", hook_max_ms=3000,
                                           audio="product_voiceover", captions="phrase", keep_source_audio=False),
    ("product_showcase", "dynamic"): dict(operations=_ops(SHOT_AWARE_CUTS, REORDER, LOUDNESS, HOOK, ZOOM, TEXT_EMPHASIS,
                                                          COLOR_MATCH, TRANSITIONS, SFX_ACCENTS, DYNAMIC_CAPTIONS),
                                          max_zoom=1250, max_zoom_share=500, hook="auto", hook_max_ms=3000,
                                          audio="product_voiceover", captions="keyword", keep_source_audio=False),
    # Tổng hợp: the ranking is the script's; the source's own sound is kept where it carries the moment.
    ("compilation_entertainment", "light"): dict(operations=_ops(SHOT_AWARE_CUTS, LOUDNESS), audio="mixed_commentary"),
    ("compilation_entertainment", "balanced"): dict(operations=_ops(SHOT_AWARE_CUTS, LOUDNESS, TRANSITIONS, ZOOM, SFX_ACCENTS,
                                                                    TEXT_EMPHASIS, HOOK),
                                                    max_zoom=1150, max_zoom_share=300, hook="auto", hook_max_ms=6000,
                                                    audio="mixed_commentary", captions="phrase"),
    ("compilation_entertainment", "dynamic"): dict(operations=_ops(SHOT_AWARE_CUTS, LOUDNESS, TRANSITIONS, ZOOM, SFX_ACCENTS,
                                                                   TEXT_EMPHASIS, HOOK, TRIM_PAUSES, DYNAMIC_CAPTIONS),
                                                   max_zoom=1200, max_zoom_share=400, hook="auto", hook_max_ms=8000,
                                                   audio="mixed_commentary", captions="keyword", trim_pause_min_ms=700),
    # Phim tài liệu: footage illustrates a new script (order is free); restraint of a documentary at every level.
    ("documentary_narrative", "light"): dict(operations=_ops(REORDER, COLOR_MATCH, LOUDNESS), audio="narration_ambient"),
    ("documentary_narrative", "balanced"): dict(operations=_ops(REORDER, COLOR_MATCH, LOUDNESS, SHOT_AWARE_CUTS, ZOOM, TRANSITIONS),
                                                max_zoom=1080, max_zoom_share=250, audio="narration_ambient"),
    ("documentary_narrative", "dynamic"): dict(operations=_ops(REORDER, COLOR_MATCH, LOUDNESS, SHOT_AWARE_CUTS, ZOOM, TRANSITIONS,
                                                               HOOK, TEXT_EMPHASIS),
                                               max_zoom=1140, max_zoom_share=350, hook="auto", hook_max_ms=12000,
                                               audio="narration_ambient", captions="phrase"),
}


def pacing(style: str, level: str) -> Pacing:
    base = STYLE_PACING[style]
    tempo = LEVEL_TEMPO[level]
    target = min(base.maximum, max(base.minimum, base.target * tempo // 1000))
    return Pacing(base.minimum, target, base.maximum)


def resolve(workflow: str, options: Options) -> Policy:
    if workflow not in WORKFLOWS:
        raise ValueError(f"unknown workflow {workflow!r}")
    if not isinstance(options, Options):
        raise TypeError("options must be mastering Options")
    if options.level == "off":
        return Policy(workflow, options, frozenset(), STYLE_PACING[options.style])
    return Policy(workflow, options, pacing=pacing(options.style, options.level), **_TABLE[(workflow, options.level)])


def check(plan: Plan, policy: Policy) -> list[str]:
    """What in ``plan`` its policy does not allow; empty when the plan may be rendered."""
    problems: list[str] = []
    if plan.workflow != policy.workflow or plan.options != policy.options:
        problems.append("the plan was made for another workflow or options")
    if not policy.enabled:
        problems.append("mastering is off: the workflow renders as before, without a plan")
        return problems
    zoomed = 0
    last_end: dict[str, int] = {}
    for segment in plan.segments:
        framing = segment.framing
        if framing is not None and framing.zooms:
            zoomed += segment.timeline.length
            if not policy.allows(ZOOM):
                problems.append(f"{segment.segment_id}: zoom is not allowed")
            elif max(framing.zoom_from, framing.zoom_to) > policy.max_zoom:
                problems.append(f"{segment.segment_id}: zoom {max(framing.zoom_from, framing.zoom_to)} exceeds {policy.max_zoom}")
        if segment.role == "hook":
            if policy.hook == "off":
                problems.append(f"{segment.segment_id}: a hook is not allowed")
            elif policy.hook == "suggest" and not segment.needs_review:
                problems.append(f"{segment.segment_id}: a suggested hook must be marked for review")
            elif segment.timeline.length > policy.hook_max_ms * plan.time_base.denominator // (1000 * plan.time_base.numerator):
                problems.append(f"{segment.segment_id}: the hook is longer than {policy.hook_max_ms} ms")
        elif not policy.allows(REORDER) and segment.role != "still":
            # Without reorder, each source plays forward: a later segment never shows earlier footage.
            if segment.source.start < last_end.get(segment.source_id, 0):
                problems.append(f"{segment.segment_id}: footage of {segment.source_id} is shown out of order")
            last_end[segment.source_id] = segment.source.end
        if segment.audio_mode == "source" and not policy.keep_source_audio:
            problems.append(f"{segment.segment_id}: this workflow plays the voice-over, not the source sound")
    if zoomed * 1000 > policy.max_zoom_share * plan.duration:
        problems.append(f"{zoomed * 100 // plan.duration} % of the video zooms; at most {policy.max_zoom_share / 10:g} % may")
    if plan.workflow == "dubbing_preserve":
        # Dubbing keeps the whole source in order; only measured pauses may be dropped (TRIM_PAUSES).
        footage = [s for s in plan.segments if s.role not in ("hook", "still")]
        if not policy.allows(TRIM_PAUSES) and any(b.source.start != a.source.end for a, b in zip(footage, footage[1:])
                                                  if a.source_id == b.source_id):
            problems.append("a dubbed video cannot leave out footage at this level")
    return problems


def describe() -> dict:
    """Every workflow × level as plain data (for the desktop and for the report)."""
    table = {}
    for workflow in WORKFLOWS:
        for level in LEVELS:
            policy = resolve(workflow, Options(level))
            table[f"{workflow}/{level}"] = {
                "operations": sorted(policy.operations), "max_zoom": policy.max_zoom, "max_zoom_share": policy.max_zoom_share,
                "hook": policy.hook, "audio": policy.audio, "captions": policy.captions,
                "trim_pause_min_ms": policy.trim_pause_min_ms, "keep_source_audio": policy.keep_source_audio,
            }
    table["styles"] = {style: STYLE_PACING[style].__dict__ for style in STYLES}
    return table

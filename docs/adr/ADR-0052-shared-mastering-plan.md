# ADR-0052: Shared mastering planner, plan contract and workflow policy

Status: Accepted. Epic: Unified Intelligent Video Production & Mastering Engine (Phase 1).
Audit: `docs/mastering/PHASE0_ARCHITECTURE_AUDIT.md`.

## Context

Four workflows (Lồng tiếng, Bán hàng, Tổng hợp, Phim tài liệu) each hard-code how
footage is chosen and paced: `product/render.plan` splits the narration into equal
≤5 s parts and cycles the visuals; `compilation/job` plays one user trim per item;
`docu/matcher` has its own shot-aware rules; dubbing keeps the picture as is. There
is no saved, versioned description of editing decisions that could be reviewed,
edited and rendered again without analysing the media again, and no place where
"what is allowed" differs per workflow.

## Decision

1. **Planner, not renderer.** A new package `engine/dubflow/mastering/` decides;
   each workflow converts the plan into the input of the renderer it already uses
   (`product/render.ffmpeg_argv`, `compilation/render`, `docu/roughcut` via
   `visual_plan.json`, `media/adapter`, or the Chỉnh sửa `ExportRequest`). No new
   FFmpeg render engine is added.
2. **Contract `contracts/mastering/schema-v1.json`** defines two documents:
   - `mastering_options` — what the user picked: `level` (`off`, `light`,
     `balanced`, `dynamic`), `style` (`natural`, `dynamic`, `cinematic`,
     `technical`, `entertainment`, `sports`, `educational`, `storytelling`).
   - `mastering_plan` — the decisions: workflow, options, sources (duration,
     geometry, frame rate, rights, origin), ordered segments, warnings and
     provenance (planner id, version, algorithm recipe, input digests).
   Each segment has `segment_id`, `source_id`, half-open `source` and `timeline`
   intervals, `role` (`primary`, `demonstration`, `reaction`, `broll`, `hook`,
   `transition`, `still`), `audio_mode` (`source`, `narration`, `mixed`, `mute`),
   `speech_refs` (cue/sentence ids, never copied timestamps), `framing` (zoom
   in milli-units, centre and protected boxes in basis points), `effects`,
   `confidence_milli` (0–1000), `needs_review` and `reasons`.
3. **Integer time.** Every time is a decimal-string tick in the plan's single
   reduced `time_base` (default 1/1000 s, `media.CANONICAL_TIME_BASE`), as in
   the timeline contract (ADR-0002). Timeline segments are contiguous, ordered
   and non-overlapping; a segment's source span equals its timeline span unless
   it is a `still` (source span 0). Retiming is out of scope for v1. Conversion
   to milliseconds or float seconds happens only at the renderer boundary.
4. **Workflow vs style.** `policy.py` maps `(workflow, level)` to the operations
   allowed (`shot_aware_cuts`, `trim_pauses`, `reorder`, `hook`, `zoom`,
   `captions_dynamic`, `color_match`, `audio_policy`, …) and their bounds (max
   zoom, share of zoomed segments, minimum/maximum segment length); `style` only
   tunes pacing inside those bounds. Fixed invariants, enforced by validation
   and tests: no automatic flip, no fractional rotation, no stretch, no export
   zoom; `dubbing_preserve` never reorders, never drops speech and has no hook
   below `dynamic` (and there only as a suggestion flagged `needs_review`).
5. **`off` is the old behaviour.** `off` is the default everywhere. A workflow
   does not call the planner when the options are absent or `off`, so job
   output (FFmpeg arguments, segments, captions) is unchanged.
6. **Analysis once.** `analysis.py` reads each source in one FFmpeg pass
   (scene cuts with `docu/shots.py`'s algorithm, motion from the same
   thumbnails, `silencedetect`) and caches the result next to the run, keyed by
   file size, mtime and recipe. A saved plan records the digests of its inputs;
   loading it with unchanged inputs renders again without analysis.
7. **Low confidence is data.** A segment the planner is unsure of carries a low
   `confidence_milli` and `needs_review`; workflows fall back to their legacy
   choice when analysis fails.
8. **Parallel render safety.** Zoom, reverse and freeze make a segment unsplittable
   in `edit_parts.rs`. The policy caps the share of zoomed segments so long
   exports keep cut points; the planner never moves `edit_parts` boundaries.

## Compatibility

New, additive namespace. No existing schema, worker protocol, SQLite table or
artifact changes. Plans are written under each run's `.dubflow-work/` as
`mastering_plan.json`; older apps ignore the file. A consumer rejects a plan with
an unknown `schema_version` and re-plans rather than guessing. The planner
version and recipe are pinned in the plan's provenance so a re-render of an old
plan uses the decisions it recorded.

## Consequences

- Phase 2–4 add per-workflow planners on this contract; Phase 5 adds the
  "Tối ưu hậu kỳ tự động" UI that writes `mastering_options`.
- Docu keeps `visual_plan.json` as its render input; a later ADR decides whether
  it is produced from a mastering plan.

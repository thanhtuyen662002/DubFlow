# ADR-0051: Documentary cuts edited by hand (per-slice edits and cover)

Status: Accepted. Issue #630.

## Context

A documentary's film is rendered from `visual_plan.json`: per narration scene, a
list of slices (`source_id`, `start`, `end`, `at`, `tier`, optional `kind`/`ref`,
`reused`/`variation`, `crossed`). Chỉnh sửa showed the rendered film as one clip
and could only change the cover (caption band, logo boxes) per source
(`cover_edits.json`, Issue #520). The owner asked to edit each cut and its cover.

## Decision

1. `visual_plan.json` slices may carry an optional `cover`:
   `{"band": {"top", "bottom", "share"} | null, "logos": [[x, y, w, h], ...]}`
   (fractions of the source frame, validated like the per-source cover). When
   present, the render frames that slice with it
   (`visual.cover_framing` → `safezone.frame_logos`) instead of the source's
   framing. Slices edited by hand also carry `duration` and `tier: -1`, as
   `replace_timeline_clip` already wrote.
2. New engine command `slices --run-dir --request <json file>` (Tauri
   `docu_slices`, the request is passed as a file because a ~180-cut film is too
   long for a command line). It replaces the slices of the named scenes. Each
   scene's `start`/`end` (the narration timing) never changes: the first cut
   starts with the scene, cuts tile it, each is at least 0.5 s and inside its
   source. A cut that still shows its planned footage keeps `tier`, `kind`/`ref`,
   `reused`/`variation`, `crossed`; other footage is hand-picked (`tier: -1`).
3. `.dubflow-work/overrides.json` gains an optional `scenes` map
   (`scene_id → {start, end, slices}`) next to the existing per-index `slices`.
   A new plan (pacing, framing, rescan) puts a hand-cut scene back whole when
   its narration timing is unchanged, and says how many cuts it dropped
   otherwise; a changed script drops all overrides, as before.

## Compatibility

All additions are optional keys. Plans and overrides written by older versions
load unchanged (no `cover`, no `scenes`): the render uses the source framing and
re-planning behaves as before. An older app reading a plan with per-slice
`cover` ignores the key and renders with the source cover; it rewrites
`overrides.json` without `scenes` only when the owner picks footage over MCP,
which loses hand cuts on a later re-plan but never corrupts the plan. Render
receipts include the slice's filter graph, so a cut with a new cover renders
again and unchanged cuts are reused. No migration is required.

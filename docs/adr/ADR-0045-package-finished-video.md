# ADR-0045: Optional packaging of finished dubbing videos

Status: Proposed. Issue #376.

## Decision

The desktop validates a completed `final_vi.mp4` through `review_dir` and writes
a version 1 spec in `.dubflow-work/package/spec.json`. Its fields are
`schema_version`, `video`, `intro`, `outro`, `voice_id`, `speed`, `music`,
`music_volume` and `background`. Text is bounded to 600 Unicode characters per
card, speed to 1.0–1.3, volume to 0–1. At least one card or music is required.
The engine rejects unsupported versions and invalid or missing inputs.

`keep` uses the finished video's audio. `replace` mixes Vietnamese dialogue
with looped user music. `duck` adds the separated original accompaniment at
25%; it requires the background stem and does not infer separation from other
files. Stem/musical inputs are padded or trimmed to the main video duration.
The actual recorded voice is preferred; the catalog default is used when no
voice was recorded. A recorded voice missing from this runtime fails explicitly.

Cards reuse product narration and caption timing, with a blurred, darkened
frame of the main video, centered outlined ASS captions at its resolution,
and its rational frame rate. Parts are normalized to identical video settings
and 48 kHz stereo AAC 192k, then concatenated. Publish only after validating the
partial output duration, resolution and frame rate. The sole result is
`final_vi_packaged.mp4`; original exports and stems are read-only.

RUNNING/COMPLETED/FAILED/CANCELLED status and a cooperative cancellation marker
live in the package work directory. Cancellation kills FFmpeg through the same
runner as product videos and preserves any previously published result.
Normalized parts have atomic receipts binding source content hashes, recipe
code, voice profile, encoder and output content hashes. A retry reuses verified
completed parts; changed input or corrupt work invalidates them. FFmpeg streams
media with bounded memory. Card narration is bounded by the text limit.
The host prevents overlapping package invocations for the same output and reaps
the worker, reporting unexpected termination instead of polling forever.

## Compatibility and migration

This is an additive, optional post-export feature. Existing manifests, canonical
timelines, SQLite records, worker protocols and editable exports are unchanged.
Old apps ignore the new file/directory; existing outputs need no migration.
The spec and status are private versioned package artifacts. Future incompatible
recipes must invalidate work files before reuse. The feature uses the installed
app-owned Python, media runtime and pinned voice packs, like product rendering.

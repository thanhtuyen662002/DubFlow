# ADR-0025: Source slots and derived dubbing windows

- Status: Proposed, Issue #166 / Draft PR #195.
- Versions: TTS document 2, private dubbing placement 1, private TTS checkpoint 2,
  VieNeu parent producer 3.3.0. Timeline/worker/SQLite/model formats unchanged.

## Evidence and decision

The actual installed a0fa portrait Sintel run synthesized only 3 of 11 cues.
Seven complete translations did not fit their short source utterance intervals
at the safe speaking rate; one independently failed complete-speech validation.
The current gap between recognized source cues is unused by the dubbing fitter.

Preserve canonical source cue IDs, integer source-derived start/end, transcript,
translation, subtitles and editable timeline. TTS document 2 retains these
`slot_start`/`slot_end` points and adds a separate `render_window_end` to each
artifact. `actual_end` remains the end of measured PCM placed at `slot_start`.
The explicit render limit never shortens the original source slot. New consumers
validate `actual_end <= render_window_end` without the old overrun tolerance.

The production allocator sorts all recognized source cues once. It grants at
most 2000 ms after a source end, bounded by decoded/media extent and 120 ms
before the next recognized source start. An overlap on either side receives
no extension. Original overlapping source slots are retained; this policy does
not claim to solve overlapping voices or diarization. An inter-cue gap is not
certified silence, a scene boundary, a visual character or calibrated confidence.
Keep that uncertainty explicit in the placement receipt and used-gap advisories.

VieNeu chooses the smallest interval that accommodates natural speech, up to
the render limit. It pads only up to the original source-slot duration when
speech is shorter. When natural speech exceeds the render limit, preserve the
existing <=1.3 pitch-preserving tempo cap and bounded measured correction
passes. Never cut spoken samples, shorten translated text or relax native
complete-speech refusal. The native child's pinned natural-waveform cache
avoids new model sampling during tempo fitting. The complete-speech failure
remains a separate defect to measure and repair.

Both mixer bridges consume measured `actual_end` for explicit TTS2 artifacts,
so speech tails are neither truncated to a source end nor padded/ducked across
an entire unused render budget. TTS1 mixing behavior is unchanged. Source
audio is retained on refused cues. An actually used extension becomes a visible
`TTS_INTERCUE_GAP_USED` advisory; it is not quality approval or lip-sync evidence.

## Identity, checkpoints and compatibility

Private placement1 contains the recipe, source extent and decoded-audio hash,
canonical cue-input digest, unchanged source slots, derived limits, neighboring
source start, overlap flag and original confidence. Its canonical digest is
the TTS input identity. Window/neighbor/source/text changes invalidate reuse.
Expose its hash/path in localization audio metadata and export an identical
editable copy; the canonical editable timeline remains unchanged.

TTS1 output serialization and request identity remain byte-compatible when no
render window is supplied. New readers accept strict TTS1 and TTS2 and reject
unknown versions or mismatched fields. Timeline `TimePoint` remains version1
inside either document. A document cannot mix implicit and explicit windows.
Checkpoint2 records the explicit TTS2 artifact and metadata checksum; the new
store still reads checkpoint1 with implicit TTS1 artifacts. Artifact validation
checks window equality and actual waveform hashes/quality on reuse. Do not
promote a checkpoint1 record to2 by adding a window or copying an old hash.

Old readers refuse document2/checkpoint2. No in-place migration is needed:
existing installed jobs keep their immutable old runtime/producer/model pins,
outputs and source timestamps. New runs use a fresh compatible runtime and
generation. The B2 generation already pins b2/adapter/native/checkpoint/mixer
code and recipe; new source bytes invalidate descendants. Rollback retains
the coherent previous runtime and its own artifacts. Never relabel old audio,
repin old job IDs, strip a render limit or overwrite a published old export.
Standard MP4/PCM/subtitle exports stay usable for consumers without TTS2 support.

## Qualification

Deterministic tests cover unchanged source timing and TTS1 serialization,
overlap/neighbor/media/2000 ms bounds, strict overrun rejection, spoken-tail
mixing, safe tempo caps, malformed/unknown2 and reuse/invalidation. Actual
retained-model/source probes and new packaged/runtime/native evidence are
separate from fixture wiring. All original166/175 acceptance and four CI lanes
remain mandatory, including real film/human quality, long form, batch/restart
and clean-machine acquisition. This ADR does not qualify those gates.

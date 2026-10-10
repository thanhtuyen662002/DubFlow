# ADR-0025: Source slots and derived dubbing windows

- Status: Proposed, Issue #166 / Draft PR #195.
- Versions: TTS document 2, private dubbing placement 1, private TTS checkpoint 2,
  VieNeu parent producer 3.4.0. Timeline/worker/SQLite/model formats unchanged.

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
avoids new model sampling during tempo fitting.

The actual short-cue diagnosis found that the pinned SDK heuristic allowed
only 18 acoustic frames for "Ngồi yên.". Both existing seeds hit that bound
without EOS; the same retry seed with a bounded 36-frame budget ended naturally
at frame19 (72960 samples, 1.52 seconds). Producer3.4 computes the same pinned
SDK phoneme cap explicitly for the first attempt. Only missing EOS permits
one fresh retry with the existing second seed and at most twice that budget,
still bounded by the global300 acoustic frames and32 seconds of finite PCM.
Disable the SDK's secondary heuristic cap for these explicitly bounded calls;
keep its source checksums, weights, runtime versions and babble retries unchanged.
Require actual EOS and preserve complete PCM; runtime errors do not trigger
this retry. Report reseeding and any actual budget extension as advisories,
including cached tempo reuse. No additional attempts or forced EOS are allowed.

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
The new inference fields and native/parent source hashes distinguish producer3.4
from3.2/3.3 even though the model bytes and25 voice presets are unchanged. Keep
old immutable profiles with their installed runtimes; never overwrite them
with the new recipe or reuse their cached speech as producer3.4 evidence.
The private schema1 native bridge accepts at most two unique reviewed synthesis
advisories; a frame-budget extension requires the reseed advisory. Absent/empty
and old singleton warnings remain valid. Old immutable bridges reject the new
optional warning, so producer3.4 ships its parent, child and profile together.
Standard MP4/PCM/subtitle exports stay usable for consumers without TTS2 support.

## Qualification

Deterministic tests cover unchanged source timing and TTS1 serialization,
overlap/neighbor/media/2000 ms bounds, strict overrun rejection, spoken-tail
mixing, safe tempo caps, malformed/unknown2 and reuse/invalidation. EOS tests
cover the initial heuristic, doubled/capped retry, two-seed refusal, runtime-error
refusal, finite PCM and warning-preserving natural-waveform cache. The release
harness authors synthetic subtitles strictly inside its own media extent;
the product's rejection of out-of-source dialogue remains unchanged. Actual
retained-model/source probes and new packaged/runtime/native evidence are
separate from fixture wiring. All original166/175 acceptance and four CI lanes
remain mandatory, including real film/human quality, long form, batch/restart
and clean-machine acquisition. This ADR does not qualify those gates.

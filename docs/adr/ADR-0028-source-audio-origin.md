# ADR-0028: Source audio origin in analysis, mixing and rendering

Status: Proposed by Issue #166 / Draft PR #195.

## Evidence and decision

The actual installed be792fb portrait decode discarded the source timestamp
gap: first video PTS1024 at1/12288, first audio PTS72944 at1/48000. Its
8641536-frame stereo PCM exactly equals an independently decoded source-clock
reference after68944 leading zero samples. The gap is4309/3000 seconds.
Valid codecs, hashes and B1 waveform equivalence did not prove synchronization.

Production analysis and source beds now use `source-video-origin-pcm-v1`:
preserve input PTS (`-copyts`), rescale the exact first video point to the
chosen PCM time base with explicit floor rounding, then use bounded FFmpeg
`aresample:async=1:first_pts` to pad late audio, trim pre-video audio and
retain internal timestamp gaps. WAV sample zero maps to that source point;
rounding contributes less than one output sample. Signed origins use floor,
including negative non-integral points. Unknown/invalid video PTS/time bases
fail with `MEDIA_TIMELINE_INVALID`; duration/FPS/float start times cannot guess
the missing clock. The exact source PTS/time base remain in probe/provenance.

The production render shifts video PTS to its first frame before subtitle
burn-in. An original-audio render uses the same source-origin resampling and
then zero-based audio PTS; a supplied mixed WAV already uses that origin.
Soft subtitles and the existing canonical cues use the resulting relative
timeline. Original channel count remains untouched in B1, and B2 keeps its
stereo source bed. This establishes clock mapping, not recognition accuracy,
human lip synchronization, acting quality or calibrated speech confidence.

## Compatibility and migration

The analysis checkpoint now binds source hash, exact source point, PCM rate,
channels, recipe and media adapter digest. Old checkpoints lacking that
identity cannot reuse a previous zero-origin decode. Transcript input binds
this identity; existing per-chunk ASR binding therefore rejects old analysis
and transcript evidence. Translation and B2 generation descendants invalidate
through changed input identity. B2 generation explicitly includes the decode
identity, including when an unchanged sidecar supplies dialogue text. Render
identity already includes the media adapter digest.

The localization manifest and editable timeline gain an optional versioned
`source_clock_mapping`/`clock_mapping` object retaining the absolute source
point as decimal strings and the explicit PCM rounding rule. The public
canonical timeline/worker/TTS schemas and supervisor SQLite stay unchanged.
Consumers still read legacy exports lacking this optional mapping, but cannot
infer that their audio was synchronized. No old cue, export, model, admitted
producer or private evidence is relabeled. Existing installed jobs keep their
immutable runtime; a corrected run uses a new job/runtime binding. Rollback
retains the old coherent producer and its own artifacts. Atomic publication
preserves a prior valid export until the successor passes QC.

## Qualification

Deterministic regressions cover signed/fractional rescaling, unknown-clock
refusal, analysis/B1/B2 origin wiring, mapping provenance and cache identity.
Owned FFmpeg tests separately exercise actual PCM and zero-based output clocks;
the staged/installed Windows lane must record actual packaged producer evidence.
Real retained media is checked separately with immutable source/reference
hashes. All original166/175 acceptance and four CI lanes remain required.

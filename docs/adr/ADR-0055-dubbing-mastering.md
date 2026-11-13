# ADR-0055: Mastering in Lồng tiếng (`dubbing_preserve`)

Status: Accepted. Epic #637 (Phase 3). Builds on ADR-0052 (shared plan and policy) and ADR-0054 (editorial layer).

## Context

The Lồng tiếng job (`worker/production_job.py`) runs as resumable stages, each recorded in `checkpoint.json` with
its input hash: probe → audio extract → ASR → translation → subtitles (`captions_vi.srt/.ass`) → optional
separation → B2 audio (`worker/b2_audio.py`: TTS per phrase in measured windows, `mix/adapter.py` mix with the
source bed or the separated accompaniment, `final_mix.wav`) → render (`media/adapter.py::render`: one FFmpeg pass,
cover/blur of source captions, burned Vietnamese subtitles, H.264/AAC, parallel parts for long videos) → QC
(`qc_report.json`, manifest, editable copies). The render stage is skipped only when its identity
(`render_identity()`: source, translation, subtitles hash, audio hash, recipe, adapter source hash, blur, cover)
matches; a B2 run always re-renders. The supervisor protocol (`worker/protocol.py`) is a fixed message set.

What the old "Studio Mastering" text asked for and the code does not do (and will not): a fixed 105→115→108 %
zoom cycle, a fixed 100/10/8 % mix, pitch shift, fake ambience, wiping the source audio, 200 ms cuts, flips,
odd-degree rotation, stretching on export.

Subtitles are written from the translated cues' source times, before B2. When B2 places a phrase later or longer
than its source cue (window widening, fast fit), the Vietnamese caption can drift from the Vietnamese voice.

## Decision

1. **Options.** `mastering.json` beside the outputs (the same pattern as `subtitle_style.json` and `cover.json`,
   so the supervisor protocol and the command are unchanged): `{"schema_version": 1, "level": "off|light|balanced|
   dynamic", "style": ..., "enable_climax_hook": false}`. An absent file means `off`; a broken file fails the
   job like a broken subtitle style (it is never silently read as off).
2. **Off** is today's job, byte for byte: no measurement, no plan, no AI call, the same FFmpeg arguments, the same
   render identity. Mastering never makes an AI call at any level; Gemini is never required for dubbing.
3. **No length or order change.** `dubbing_preserve` keeps the whole source in order at every level: the policy
   drops `trim_pauses` from Dynamic (it was allowed in Phase 1's table) and the plan's duration equals the source's.
   `hook` is allowed only when `enable_climax_hook` is true, and then only as a **suggestion** (a time range with
   reasons, `needs_review`), never rendered into the video.
4. **Measured, bounded operations** (`mastering/planners/dubbing.py`, pure; measurements in
   `mastering/measure.py`, LGPL FFmpeg filters only):
   - Light: loudness (two-pass `loudnorm`, linear, target −16 LUFS / −1.5 dBTP, measured on the final mix; skipped
     when already within 1 LU and under the ceiling) and mild colour (`colorlevels` from measured luma percentiles
     of sampled frames, only when the range is compressed; per-channel stretch bounded, no saturation change, no
     sharpen). No timeline change.
   - Balanced: Light + caption normalisation (timing from the final TTS placement, `TtsArtifact.slot_start` →
     `actual_end` shared over a phrase's member cues by text length; never source-language word timestamps; at
     most 2 lines of 42 characters, at least 1 s on screen, no overlaps) + safe framing (only a measured
     window-box, bars on both axes, is cropped and scaled back uniformly within `max_zoom`; letterbox or pillarbox
     alone is left as is because removing it would stretch or crop picture).
   - Dynamic: Balanced with wider colour bounds and phrase-split captions; the optional climax/teaser suggestion
     when enabled.
5. **Where it plugs in.** A `mastering` checkpoint stage after B2 (or after subtitles when B2 is off) writes
   `mastering/dubbing-plan.json` (inputs: options, recipe, final mix hash, TTS document hash, subtitles hash,
   source hash). The render gets the mastered mix (`final_mix_mastered.wav`) as `audio_path` and the colour/framing
   filters through a new optional `media.render(mastering_filters=...)` argument (inserted after the source-caption
   cover and before the burned subtitles, so captions are not colour-graded); the retimed captions replace
   `captions_vi.srt/.ass`. The render identity gains a `mastering` entry only when mastering is on, so off keeps
   its identity and a change of level, recipe, mix or TTS re-renders. The editable `final_mix.wav` stays the
   un-normalised mix (the editor applies its own −14 LUFS normalise); the mastered mix is an artifact.
6. **Resume and invalidation.** The plan is reused only when its input hash matches; any change upstream (new
   translation, new TTS, new options, new recipe) re-plans and re-renders. A failure inside mastering never fails
   the job: the job renders as off and records `MASTERING_SKIPPED: <reason>` in QC.

## Consequences

- Legacy jobs and jobs without `mastering.json` are unchanged (regression test compares FFmpeg argv and render
  identity).
- Mastering costs one `loudnorm` analysis + one apply over the mix and a few sampled frames: no model, no GPU.
  Amended 2026-11-13 (#821): plus, for a dubbed mix, one audio-only AAC trial encode of the audio the render will use
  (the render's own `-c:a aac -b:a 192k`) and one `loudnorm` measurement of it. When that encoded true peak is over
  −1.5 dBTP, the mix is made again from the PCM with the planner's filter followed by a linear `volume` cut
  (cumulative, (peak + 1.5) + 0.3 dB each time), encoded and measured again: at most `AAC_TRIM_TRIES` (3) re-applies,
  and if none is under the ceiling the one with the lowest measured peak is kept. Only linear gain: no limiter,
  compressor, dynamic `loudnorm` or loudness-range change. The trial result is stored in the plan and the stage, so a
  resumed job does not encode again; a trial that cannot run leaves the plan as planned and never turns mastering
  off (the next run of the job tries the trial again instead of reusing that plan). The final MP4 is still measured once after the render, and QC records `mastering.conformance` (passed,
  corrected, failed, unmeasured, silent, not_applicable for the B1 fallback and Vietsub only); failed or unmeasured
  adds a non-informational warning (`MASTERING_TRUE_PEAK_OVER` / `MASTERING_TRUE_PEAK_UNMEASURED`) while `status`
  stays "passed" (the report is valid and the render succeeded). The B1 source sound is measured and warned, never
  trimmed; when the B1 or Vietsub-only export cannot be measured, the record says `unmeasured: true` (B1 also warns
  `MASTERING_TRUE_PEAK_UNMEASURED`). Off is unchanged: no trial, no measurement, no new QC key. Recipe `dubbing-master-v4`.
  Every intermediate FFmpeg output of the stage (the planner's PCM partial, the AAC trial file, the trim candidates)
  is written only inside a fresh private directory (`tempfile.mkdtemp` in the `mastering` folder, removed afterwards)
  and renamed over `final_mix_mastered.wav`; a symlink or junction there is replaced, never written through. A
  linked `mastering` folder (symlink or junction) is refused by the stage itself before any plan, partial or trial is
  read or written, whether or not the planner adds a filter: mastering then renders as off with `MASTERING_SKIPPED`.
  The card shows a Vietsub-only export's
  measurement as information only: the source sound is not mastered and the ceiling does not apply to it.
  A dubbed (B2) export that measures no sound at all after AAC keeps conformance `silent` and is not a pass
  (Product System Design §19.3 "Không silence toàn video"): it adds the non-informational warning
  `MASTERING_OUTPUT_SILENT`, so `mastering.result.final_qc` is "warning" and the card shows a warning row, whether
  the dubbed mix was already silent before mastering or lost its sound later. The source's own silence (B1 fallback,
  Vietsub only) is recorded and not warned. On Windows a file briefly held open never costs a measured trim: every
  trial encodes to its own file, one that cannot be deleted is left to the private directory's removal, and the
  renames over the mastered mix, the plan and the checkpoint are tried again with a short backoff (#766 class). `.scratch-*` directories left by a
  killed worker are removed at the start of the stage: only real directories with that prefix directly inside the
  non-link `mastering` folder, never through a link; one that cannot be examined or removed is left.
- The UI to choose levels and review suggestions is Phase 5; until then `mastering.json` is written by tests,
  the E2E harness or a power user.

# ADR-0049: Documentary projects (Phim tài liệu), phase 1

Status: Proposed. Issue #440.

## Context

"Phim tài liệu" turns 2–5 Chinese sources about one engineering subject into an
English explainer documentary for a US/UK audience. The work is split into four
phases: (1) project, source contexts and script; (2) narration and the visual
plan; (3) the built-in editor; (4) an MCP server and the CapCut bridge. This ADR
fixes the phase 1 artifacts that the later phases build on.

## Decision

### Project folder and request

The page sends a request to the host. The host validates it and creates
`DubFlow Output\Phim tai lieu - <title>\run-<12 hex>\`, the same naming as
"Tổng hợp" (ADR-0046), then writes `.dubflow-work\spec.json`:

```json
{
  "schema_version": 1,
  "title": "≤120 chars",
  "topic": "≤600 chars, optional note to the writer",
  "target_minutes": 11,
  "cinematic_framing_preset": "none | letterbox_235 | overscan_108",
  "burn_end_credits": false,
  "sources": [
    {
      "id": "src_01",
      "title": "≤120 chars",
      "video": "absolute path of the original source video",
      "transcript": "absolute path of transcript.json or .srt",
      "origin_url": "https://… or null",
      "credit": "{…} from source_credit.json, or null"
    }
  ]
}
```

- **Sources.** There are 2–5 sources, with ids `src_01`… in the user's order.
  - A finished DubFlow job contributes its own `.dubflow-work\transcript.json` and the original source video named in `job_manifest.json`. The dubbed `final_vi.mp4` is never used as B-roll, because it carries burned Vietnamese subtitles.
  - A local source is any video plus an `.srt`.
- **Target length.** `target_minutes` is an integer from 3 to 20.
- **`cinematic_framing_preset`.** A project-wide look that phase 2 applies uniformly to the whole timeline: a 2.35:1 letterbox, or a 108% overscan. It is a single directorial choice. It is never randomised per clip.
- **`burn_end_credits`.** Defaults to false. Credits are always written to `source_credit.json` and `description.txt` (phase 2). An end card is rendered only when the user turns this on.
- **Read-only sources.** Source files are never modified or copied. Phase 2 must keep the source-media store from evicting a source that a project still names.

### Source contexts

Running `python -B -m engine.dubflow.docu context --spec <spec> --ffprobe <ffprobe>`
writes `.dubflow-work\sources\<id>\source_context.json` for every source. The last
stdout line is an ASCII JSON summary of the form `{"state": "COMPLETED" | "FAILED", "message", "sources": [{id, duration, segments}]}`.

```json
{
  "schema_version": 1,
  "source_id": "src_01",
  "title": "…",
  "video_path": "…",
  "duration": 945.5,
  "segments": [
    {
      "seg_id": "src_01_seg_001",
      "start": 0.0,
      "end": 4.2,
      "original_text": "…",
      "extracted_keywords": ["800万立方米"],
      "quantities": [
        {
          "text": "800万立方米",
          "dimension": "volume",
          "si": 8000000.0,
          "imperial": "282.5 million cubic feet",
          "comparisons": ["about 7.6 times the volume of the Empire State Building"]
        }
      ]
    }
  ]
}
```

- **Times.** Times are seconds with millisecond precision.
- **Quantities.** `engine/dubflow/docu/units.py` parses quantities: Chinese and Arabic numerals with 万/亿 scales, plus metric, Chinese, imperial and currency units. Code computes the imperial/USD conversions and the comparisons against a fixed benchmark table (Empire State Building, Statue of Liberty, American football field, Boeing 747, Olympic pool, Manhattan, Hoover Dam, …). The yuan-to-dollar rate is a dated constant.

### Script

The page asks the saved AI provider (ADR-0042) for one chapter at a time, in the
fixed order HOOK, PROBLEM, ENGINEERING_SOLUTION, CRITICAL_EVALUATION. Each chapter's
target length is the matching share of `target_minutes` (7/20/50/23 %). The prompt
contains:

- the timed source lines, as `[src_01:12.0-18.5] …`;
- the code-computed quantity facts;
- the chapters already written.

The page tells the user what is sent and to whom. The transcripts are data, never
instructions.

Running `python -B -m engine.dubflow.docu check --run-dir <dir> --script <draft.json>`
validates and normalises the draft. It writes `documentary_script.json` in the run
folder atomically and prints the normalised script as the last stdout line, as
ASCII JSON. The format is the one in the DocuFlow specification:
`project_title`, `target_Coordinates_3D` (or null), and `chapters[].scenes[]` with
`scene_id`, `narration_text`, `matched_source_refs`, `visual_search_tags`,
`on_screen_graphic`, `ambient_sfx_tag`, plus:

- `needs_review` (bool) and `review_notes` (Vietnamese strings), computed by `check`;
- `acknowledged_numbers` (strings the user confirmed), kept from the draft.

**Errors versus repairs.** Only a structurally unusable draft is an error: not an
object, or no scene with narration. Everything else is repaired and noted:

- unknown or out-of-range refs are dropped or clamped;
- ids are renumbered `sc_001`…;
- unknown moods and SFX tags fall back;
- chapters are put back in order.

A scene left without refs is marked for review. It is never removed.

**Numbers are never hard-dropped.** A number in the narration or the graphic is accepted when it matches any of:

- a source quantity, after unit conversion (±6 %);
- a benchmark ratio of a source quantity of the same dimension (±15 %);
- a benchmark constant named in the same sentence (±3 %);
- a USD conversion of a yuan amount (±10 %).

Small bare integers (0–10) and acknowledged numbers are skipped. Any other number
marks the scene `needs_review` and adds a note. Metric units left in the narration
are noted too.

## Consequences

Phase 2 reads `spec.json`, the source contexts and `documentary_script.json`
without needing the AI. The checker can be rerun after every edit on the page.
No new runtime dependency is added: the engine uses only the standard library and
the bundled ffprobe.

## Phase 2a: master narration (Issue #442)

- **The voice.** The narrator is Kokoro-82M v1.0 (fp32), run through the sherpa-onnx 1.13.8 already in the runtime, so no new Python dependency is added.
  - `models/manifests/production-kokoro-en-v1.json` pins 15 files (367 MB): the model, the voice table, the US/UK lexicons and an English espeak-ng data subset. Every file is downloaded on first use and hash-checked by `ensure_model_profile` (licensing: `docs/licenses/kokoro.md`).
  - Nine US and UK voices are offered; the default is `am_michael`.
  - On the reference machine the fp32 model runs at about 0.4× real time on 4 CPU threads, against 0.95× for int8.
- **The narration job.** `python -B -m engine.dubflow.docu narrate` reads the saved `documentary_script.json` and speaks one sentence per request. Over-long sentences are split at clause marks.
  - **Pauses:** 0.4 s between sentences, 0.8 s between scenes, 1.6 s between chapters (longer so the music can rise there in phase 2b).
  - **Cache:** each piece is cached under `.dubflow-work/narration/cache`, keyed by its text, voice, speed and the voice manifest. Reruns and edits only speak what changed. Cancelling stops between sentences.
  - **Loudness:** a two-pass `loudnorm` brings the track to -16 LUFS integrated with a -1.5 dBTP ceiling, in linear mode, so no time moves.
  - **Outputs:**
    - `narration.wav`: 48 kHz mono PCM.
    - `captions_en.srt`: balanced cues of two 42-character lines at most.
    - `master_audio_timeline.json`: every chapter, scene and sentence with `start` and `end`. Scenes also carry `slot_end` (the next scene's start, used for the visuals), `exact_duration_ms`, the voice, the speed, the measured loudness and `script_sha256`.
- **Staleness.** `check` now stamps `documentary_script.json` with `script_sha256`, a hash of the spoken words only. The page compares it with the timeline's hash and shows when a narration no longer matches the script.
- **Host.** It runs the job in the background (`docu_narrate`, `docu_narration_status`, `docu_narration_cancel`), refuses a second run while one is active, and turns an abandoned RUNNING status into FAILED or CANCELLED.
- **Word timing deferred.** Word-level timing for on-screen numbers moves to phase 2b, where the callouts are placed. Sentence times already come from exact sample counts.

## Phase 2b-1: the visual plan (Issue #448)

- **Shots.** `shots.py` cuts each source into shots with the bundled FFmpeg's `scdet` filter (threshold 10, frames scaled to 320 px wide, about 30× real time), so OpenCV is not needed.
  - Cuts closer than 0.4 s are one transition.
  - Results are cached in `sources/<id>/shots.json`, keyed by the file's size, modification time and the threshold.
- **Safe zone.** `safezone.py` reuses the dubbing pipeline's PP-OCRv4 caption scan, with the source's speech times. Only captions count: fixed text such as logos and channel names is never targeted.
  - Captions on screen for at least 25 % of the speech give the source a caption band.
  - The framing is the smallest zoom (at most 114 %) and upward shift that clears that band, on top of the project's uniform `cinematic_framing_preset` (letterbox 2.35:1 or 108 % overscan).
  - When 114 % cannot clear the band, the plan says so and suggests the letterbox. Phase 2b-2 may add the existing edge-stretch cover.
- **Matcher.** `matcher.py` fills each scene's slot (its narration plus the pause after it; the first scene starts at 0) with equal 3.0–4.5 s slices. When a slot cannot split into slices of at least 3 s, it gets one slice.
  - **Where footage comes from**, in order of preference:
    1. the footage the script cites;
    2. 6 s around it;
    3. the rest of the cited sources;
    4. any source;
    5. reuse, as a last resort.
    Each scene notes when it had to go beyond the cited footage.
  - **Interleaving:** consecutive slices prefer another source (A/B). Two consecutive slices from one source are at least 8 s apart in it. No source range is used twice.
  - **Cut points:** slices start on shot boundaries and end on a cut within 0.4 s when the remainder still makes a full slice.
  - The plan is deterministic.
- **Output.** `visual.py` refuses a narration made from another script and writes `visual_plan.json`: the per-source framing and every scene's slices (`at`, source `start` and `end`, tier, warnings), with the script hash and the timeline's hash and duration.
- **Host and page.** The host runs it like the narration (`docu_plan`, `docu_plan_status`, `docu_plan_cancel`, logging to `engine.log`). The page shows each scene's slices as preview buttons and flags a plan made before the script or narration changed.

## Phase 2b-2: the rough cut (Issue #450)

The rough cut is rendered by the engine, like "Tổng hợp" and "Bán hàng", so there is a viewable film before the editor work. Phase 3 brings the same visual operations into the built-in editor, so the plan can be edited and re-exported there.

- **Slices.** `roughcut.py` renders every slice of `visual_plan.json` as its own 1920×1080, 30 fps H.264 file with no sound.
  - **Length:** frame-exact; slice boundaries are rounded from the timeline, so the frame count adds up to the narration.
  - **Order of operations:**
    1. an edge-stretch cover over the caption band, when the source's framing cannot clear it;
    2. the framing crop (zoom, upward shift);
    3. fill to 16:9, or a fit over a blurred fill for sources narrower than 1.5:1;
    4. one uniform look: a gentle S-curve plus 8 % saturation, because the bundled FFmpeg has no `eq`;
    5. on slices from shots of 6 s or more, a 4 % Ken Burns push with `zoompan` on a 2× upscale;
    6. the letterbox bars.
  - **Rotation:** sizes come from a probe that honours rotation tags.
  - **Encoding:** H.264 uses Media Foundation, falling back to libopenh264.
  - **Receipts:** each slice keeps one (source file, start, frames, filter graph), so a cancelled or repeated render only redoes what changed.
- **Assembly.** The concat demuxer joins the files without re-encoding. The narration is muxed as AAC 192 kb/s and the result is published atomically as `documentary.mp4`. Rendering refuses a plan whose script or timeline hash no longer matches.
- **Credits.** `source_credit.json` and `description.txt` are always written. The description holds the title, YouTube chapters from the timeline (the first at 0:00), the footage credits (creator, platform, link) and a synthetic-voice note. A 6 s "Footage" end card is added only when `burn_end_credits` is true.
- **Matcher change.** Uncited footage from the rest of a cited source and from any other source now ranks the same, so A/B interleaving decides between them. Before, a scene whose cited footage ran out stayed on the same source.
- **Measured** with the bundled runtime: a real 28.8 s plan from a 1080p Bilibili source rendered in 11.7 s, at exactly 863 frames.

## Phase 3a: the rough cut in the built-in editor (Issue #452)

- **Editor target.** `documentary.mp4` of a valid documentary run is a new `docu` editor target, kept in its own `.dubflow-work/edit`. `captions_en.srt` is its "Phụ đề" track. The editor's existing tools work on it as on a compilation: cut and trim, insert, overlays, Âm thanh (sound effects and music, with ducking), Chữ, Phụ đề, Che and export.
- **Seeded callouts.** On the first open (no saved draft) the "Chữ" track is seeded from the script's on-screen graphics, placed 0.3 s after each scene's narrated start and lasting at most 5 s within the scene's slot:
  - METRIC_CALLOUT: value, title and subtext as a dark slate card (`#0F172A` at 90 %, the editor's 0.05 opacity steps) with a cyan outline, top right;
  - LOWER_THIRD: a lower third that slides up;
  - LABEL: a centred label.
  A saved draft is never re-seeded.
- **Navigation.** The documentary page opens the editor with "Chỉnh sửa" next to the rough cut. Leaving the editor returns to the documentary page.
- **Caption-band warning.** When the caption band cannot be cleared, the warning now says it is covered by edge stretch, and suggests the letterbox only when the project does not already use it.
- **Deferred.** Editing the plan slice by slice inside the editor (per-segment framing and narration lanes in the editor's own renderer) remains later work, as does the MCP server and CapCut bridge (phase 4).

## Phase 4a: CapCut draft (Issue #454)

The owner chose to write drafts straight into CapCut's draft folder. CapCut stays an adapter: the draft is made from DubFlow's own rough cut.

- **Draft contents.** `capcut_draft.py` writes a CapCut 9.x draft into `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\DubFlow - <title>`. The folder name is numbered and never overwrites.
  - Files: `draft_content.json` (version 360000), `draft_meta_info.json`, `draft_settings`, `timeline_layout.json` and two small configs, written compact like CapCut and moved into place from a temporary folder.
  - Video track: every rendered slice (and the end card) is a separate clip on the main track, at frame-exact microsecond times.
  - Audio track: the narration.
  - English captions stay in `captions_en.srt`, for CapCut's caption import.
- **Template.** `engine/dubflow/docu/capcut/capcut_9_template.json` was derived from a CapCut 9.6–made empty draft and a CapCut-saved project, keeping their structure and defaults. Device id, MAC addresses, paths and names were removed, and a test guards that.
- **Media.** The clips are `capcut_media` in the run folder: hard links of the rendered slices (no extra space; a copy across volumes). Cleaning the render cache therefore cannot break the draft.
- **CapCut's project list.** CapCut lists drafts from `root_meta_info.json`, which it rewrites on exit. So the export refuses while `CapCut.exe` runs, keeps `root_meta_info.json.dubflow-backup`, and puts the draft first in `all_draft_store`.
- **Verified** on CapCut 9.6.0: the exported draft is listed first in its projects.
- **Host and page.** The host exports (`docu_capcut`) and can start CapCut (`docu_launch_capcut`). The page offers "Xuất sang CapCut" after the rough cut.

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

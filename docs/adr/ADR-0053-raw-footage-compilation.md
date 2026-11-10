# ADR-0053: Tổng hợp from un-dubbed footage (raw media path)

Status: Accepted. Epic #637 (Phase 2). Builds on ADR-0052 (mastering plan) and ADR-0054 (editorial layer).

## Context

`compilation/job.load_spec` accepts only `final_vi.mp4` inside `DubFlow Output`: every item of a Tổng hợp
video must first go through Lồng tiếng, and each item plays one user trim after an optional lead card. A
Top N or "tổng hợp tình huống" programme, though, is a new script over many sources: the sources need not be
dubbed, an item may use several clips, the narration is new and the pictures must follow it.

## Decision

1. **Two paths, one job module.** `schema_version: 1` specs keep the legacy path byte for byte (same fields,
   same `final_vi.mp4` rule, same parts, receipts and output). `schema_version: 2` is the raw media path. The
   desktop picks the version; nothing converts an old spec.
2. **Raw sources.** A v2 source is any local video file (`.mp4 .mov .mkv .webm .m4v .avi`) the user chose, from
   Hàng đợi (its original download) or from the computer. It is read, never written. Optional per source: a
   trim (`start_ms`/`end_ms`), a transcript (`.srt` next to it, e.g. a queue run's `captions_vi.srt`), the user's
   notes, a credit (`creator`, `platform`, `url`, `license`) and `rights` (`owned`, `licensed`, `permission`,
   `unknown`). Credits and rights go into the plan's sources, `description.txt` and `program.json`.
3. **Programme structure.** A v2 spec is a programme: `program.kind` (`top_n`, `list`), `program.ranking`
   (`user`: the listed order and ranks are kept exactly; `suggested`: the AI may propose an order, which is
   written with its reasons to `program.json` and applied only when the spec says `apply_suggestion: true`),
   then `hook`, `intro`, `items[]` (id, rank, title, narration, notes, `verify` notes, sources, keep source
   audio `auto|on|off`) and `outro`. A missing narration may be drafted by the user's AI from the item's own
   title, notes and transcript; a drafted line is marked `needs_review` and lists what must be checked.
4. **New timeline.** Each narrated part (hook, intro, each item, outro) is spoken first (TTS); its sentences and
   spoken lengths become the part's timeline. Source transcripts are evidence for choosing footage only; their
   timestamps never become output times. Captions follow the synthesized speech (`product/script.captions`).
   When the footage of an item is shorter than its narration, its shots are reused (in source order, flagged);
   when the narration is shorter than the moment, the item may end on the source's own sound (below).
5. **Planning.** `mastering/planners/compilation.py` makes one `mastering_plan` per part with workflow
   `compilation_entertainment` using the shared editorial layer (ADR-0054): each sentence gets the item's
   footage that matches it, per source in source order (the policy has no `reorder`), cut inside shots. After
   the narration an item may keep a short **source moment** (2–6 s, `audio_mode: source`) when its source has
   sound worth hearing (speech or a loud event in the transcript/audio) and keep-audio is `auto`/`on`. Under
   narration the source sound plays ducked (`mixed`, −16 dB) or muted (`off`). The hook is the programme's
   best-matching moment to the hook line, `role: hook`, within the policy's `hook_max_ms`, flagged for review.
6. **Render.** The existing compilation renderer gains `montage_argv`: the part's planned pieces through the
   same fit (blurred fill + whole picture, ADR-0034 geometry), one ASS (rank badge, item title, captions), the
   narration, the ducked source sound and the music, in one encode per part. Parts keep the legacy receipts
   (key = digest of the part's plan, narration, ASS and code), so editing one item re-renders only that part;
   the final join is the legacy `concat_argv`. No new render engine; no extra encode compared with v1.
7. **Storage.** Under the output's `.dubflow-work/`: `program.json` (the resolved programme, AI suggestions and
   drafts), `mastering/plan-<part>.json` (contract v1), `mastering/editorial-<part>.json` (decisions and
   alternatives, ADR-0054), analysis and candidate caches by content. Outputs: `compilation.mp4`,
   `chapters.txt`, `description.txt` (with credits), `captions.srt` (whole programme).

## Compatibility

v1 specs and outputs are unchanged and covered by the existing tests. v2 is additive; older apps reject it as
an unknown version (their current behaviour). No SQLite, worker protocol or checkpoint change.

## Consequences

- The Tổng hợp screen can offer "Từ video gốc" next to the dubbed list (Phase 5 UI).
- Rights are the user's statement, recorded and shown, not verified.

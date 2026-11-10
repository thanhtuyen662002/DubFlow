# ADR-0054: Shared editorial layer (content evidence, scoring and script alignment)

Status: Accepted. Epic #637 (Phase 2). Builds on ADR-0052. Renumbers the audit's planned ADRs: dubbing mastering
becomes ADR-0055, documentary narrative presets ADR-0056.

## Context

Phase 1 cuts at shot boundaries using cuts, motion and silences. Those signals segment media; they say nothing
about what a shot shows, so they cannot match a sentence of a script to the footage that illustrates it.
DubFlow already holds three kinds of content evidence: the user's own words about a clip (notes, file name),
transcripts of queue videos, and the user's AI provider, several of which accept images (`ai.client.Preset.vision`).
None is guaranteed: a key may be missing, a provider may be text-only, a call may fail.

## Decision

1. **Candidates** (`mastering/candidates.py`). Each source's shots (Phase 1 analysis), long shots split into
   windows, become candidates. One FFmpeg call per candidate grabs its middle frame twice: a 512 px JPEG (for the
   vision adapter and the UI) and a 160×90 grey frame from which pure Python measures brightness, contrast and
   sharpness (Laplacian variance) and a 64-bit dHash. Dark, flat (black/blank) and blurred candidates are marked
   unusable with the reason; near-identical candidates (dHash distance ≤ 6) share a duplicate group. Cached by
   source content.
2. **Evidence** (`mastering/evidence.py`). Per candidate, merged with its origin and confidence: `user`
   (notes; strongest), `metadata` (file name words), `transcript` (text spoken inside the span), `vision:<provider>/
   <model>` (description, objects, actions, visible text, labels from a closed vocabulary, product box, clarity,
   hook potential) and `signal` (quality, motion, audio activity). Text is folded (lower case, Vietnamese
   diacritics removed, stop words dropped) into terms for matching. Labels are only those the evidence states;
   `unknown` is a valid answer and nothing is inferred from motion alone.
3. **Vision adapter** (`mastering/vision.py`), optional. Used only when the job has an AI route whose preset
   declares `vision` (the user's own key, through `DUBFLOW_AI_KEY` like translation). Frames go in batches with a
   fixed system prompt and a JSON schema; answers are validated (vocabulary, ranges, lengths) and cached per frame
   digest × provider × model × prompt version. A frame budget per job bounds cost. No key, a text-only model, a
   refusal, a timeout or a malformed answer leaves the candidate without vision evidence and adds a warning; the
   job never fails for it and never needs a GPU or a network.
4. **Script units and alignment** (`mastering/align.py`). The narration's sentences, with their spoken lengths,
   are units on the output timeline (cuts fall in the pause between sentences). Each unit gets a function
   (`hook`, `benefit`, `demonstration`, `feature`, `result`, `cta`, or `narration` for compilations) from its
   position and Vietnamese cue words. A candidate's score for a unit combines relevance (term overlap weighted by
   rarity), role fit (unit function × candidate labels), quality, a small length fit, and penalties for reuse,
   duplicates and repeating the previous source; motion is a minor term, used only for hooks and pacing.
   Candidates are assigned unit by unit (policy pacing; per source in order when the policy has no `reorder`).
5. **Explainable, honest confidence.** Each segment's `reasons` say which unit it illustrates and why (matched
   terms and their origin, label, quality), its `speech_refs` name the unit, and its confidence comes from the
   evidence: with no content evidence at all the plan is marked `signals_only`, every segment is capped at 450 and
   `needs_review`, and the report says the choice was made from cuts and quality only.
6. **Decisions and edits.** Beside each plan, `editorial-*.json` (schema `contracts/mastering/editorial-v1.json`)
   records per unit the chosen candidates and the next best alternatives with scores and reasons, the evidence
   summary per candidate and the mode (`content` or `signals_only`). A spec's `editorial.edits` pins candidates to
   a unit, excludes candidates or whole sources, and is applied before scoring; pinned choices are confidence 1000
   with reason "chọn bởi người dùng". Editing the script or one clip re-plans; analysis, candidates and vision
   answers of unchanged files are reused.
7. **Facts.** The planner never writes claims. For Bán hàng, a sentence that states a number or unit not found in
   the product's name, features or notes is flagged `needs_review` ("thông số chưa có trong thông tin sản phẩm").

## Compatibility

Additive. Plans stay contract v1 (`speech_refs`, `reasons`, `confidence_milli`, `framing` already exist).
`editorial-v1.json` is a new document. Product spec gains optional `editorial` (`notes[]`, `edits`, `ai`);
absent, it behaves as Phase 1 with signals-only evidence.

## Consequences

- The quality of content matching depends on the evidence available; the plan and the report say which it was.
- Phase 3–4 reuse candidates, evidence and alignment for dubbing hooks and documentary footage.

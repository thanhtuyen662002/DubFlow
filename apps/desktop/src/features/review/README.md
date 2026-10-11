# Review Center component boundary

`mountReviewCenter(container, snapshot, actions)` reads strict public v1/v2
payloads. It groups six finding reasons and renders at most 30 finding buttons
per page. Integer source ticks remain identity; formatted milliseconds are only
display labels. Legacy and global/unmapped findings are read-only.
While a cue has unsaved changes, group/finding/page navigation is disabled until
the user saves or explicitly discards the edit. Keyboard selection retains focus
when the finding list is rebuilt.

Import `review.css` from the host page. No desktop main or native command is
wired by this component alone. All imported text is written as text, never HTML.

The host supplies voices compatible with the job's pinned model/producer.
An assigned voice absent from that list is retained until explicitly changed;
a missing voice is not replaced by a default during a translation-only edit.

- `preview(request)` receives job/revision/finding/cue identity. The native host
  must validate the committed manifest and whole selected WAV, then own
  playback. Artifact paths never become browser media URLs.
  Its second argument is an AbortSignal: changing finding/job or destroying the
  view aborts pending playback. The host must stop work/playback when cancelled.
- `correct(request)` receives an exact revision and changed fields. The backend
  must validate authorization, mapping and its durable compare-and-swap, then
  return the v2 metadata snapshot for revision + 1. The view accepts only the
  requested edit, consistently across findings for that cue, with the expected
  rerun plan. Other cues, source ranges, confidence and provenance stay intact.
  This acknowledgement means a correction was saved. It does not certify
  executed synthesis or physical replacement; those require separate validated
  supervisor operations and a refreshed snapshot from the host.
- A failed or mismatching correction response disables further changes until
  `replace(freshSnapshot)`: an uncertain write is never retried automatically.
- `replace`/`destroy` invalidate pending callbacks so an old response cannot
  replace the current view. The backend still owns durable concurrency checks.

`ReviewConnection` binds a transport to the selected job. Late snapshot replies
are ignored, a snapshot for another job is refused, stale action callbacks cannot
invoke the backend, and preview/correction requests must match current exact
job/revision/finding/cue identity. It propagates cancellation to preview operations
and advances its revision only after the strict correction acknowledgement.
An uncertain or mismatching correction clears the connection; a fresh load is
required before another mutation, including for callers outside the DOM view.
Only one correction may be submitted per loaded selection at a time. A refused
duplicate never reaches the transport or clears the pending edit. An old job's
late failure cannot unlock or invalidate a newer job's pending correction.
This is an adapter boundary; production Tauri command wiring and native playback
remain separate work. Native preview must verify the committed artifact graph for
the requested revision; a metadata correction never certifies new audio bytes.

## Local browser QA

From the repository root, using the existing desktop dependencies:

```powershell
node apps/desktop/node_modules/vite/bin/vite.js --config tests/review/vite.config.mts --host 127.0.0.1 --port 8771 --strictPort
```

Open `http://127.0.0.1:8771/tests/review/view-preview.html`. The page has 67
synthetic findings, including related findings for one cue and a global warning.
Its callbacks are demonstrations: they neither play audio nor alter real jobs.
Controls expose legacy payloads, delayed responses, reload and wrong-job
responses. This page is outside the shipped desktop entry point.

The registered `tests/review/review.test.mjs` executes the actual isolated Python
producer/store, validates its acknowledgement in TypeScript and rejects changed
job/revision/source/range/unrelated finding/voice/plan responses. Browser QA
provides additional DOM/interaction evidence; it is not a substitute for full
installed review, targeted regeneration and physical replacement/undo evidence.

# Review Center component boundary

The installed transport now calls `review_snapshot_v2`. Native discovery returns
either an original-only session after checking no graph exists, or an admitted
current display projection with `schema_version:3`. Its complete native token
keeps revision as a decimal string; the component never converts it to a Number
or manufactures a v2 revision. The current revision is shown after correction or
undo. Existing v1/v2 payload parsing remains explicit and unchanged.

Current B2 cue playback uses a separate full-token `review_audio_v2` request.
All token fields and the selected job/finding/cue must match; browser requests
never choose a media path. B1 has no invented synthesized preview. Current views
cannot call the old revision0 audio or correction callbacks. Current B1/B2
drafts can request `review_plan_v1` explicitly before confirmation. The native
reader checks the actual current content/token and mode, then returns the exact
scope and candidate metadata hash without running inference or changing durable
state. The client keeps the current token, rejects substituted scope/changes and
cancels a check when the draft or selection changes. Apply remains disabled until
the full-token execution/CAS route is integrated and qualified. Full-token mutation/
undo/export actions remain to integrate. A current native admission failure
propagates; it does not select an old original snapshot. Standard output remains
usable independently of Review.
Component tests and type checks do not qualify native binaries, installed GUI,
actual crash, cumulative regeneration or long-form product behavior.

`mountReviewCenter(container, snapshot, actions)` reads strict public v1/v2
payloads. It groups six finding reasons and renders at most 30 finding buttons
per page. Integer source ticks remain identity; formatted milliseconds are only
display labels. Legacy and global/unmapped findings are read-only.
While a cue has unsaved changes, group/finding/page navigation is disabled until
the user saves or explicitly discards the edit. Keyboard selection retains focus
when the finding list is rebuilt.

The desktop build includes `review.css`; other hosts must include it themselves.
All imported text is written as text, never HTML.

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
Native preview must verify the committed artifact graph for the requested
revision; a metadata correction never certifies new audio bytes.

## Optional production read-only panel

`host.ts` mounts a persistent panel for the selected completed job. It opens
on demand and survives unrelated queue polls. `transport.ts` uses versioned
Tauri snapshot/audio/cancel commands. Native admission retains the supervisor's
committed export pin in a bounded session, checks that it still matches before
audio delivery, and calls the owned isolated Python reader. Binary `audio-v1`
output is the same bounded WAV buffer whose whole hash/header/frame count was
validated; no returned file path is reopened for playback.

The webview plays only an in-memory Blob, releases its URL, and stops playback
on finding/job/reload/disposal changes. Abort also cancels native subprocess
work; native deadlines/output/concurrency/session limits bound stale work.
An expired, corrupt, legacy or unavailable optional review reports an explicit
reload/error while preserving the queue and standard exports. Revision0 is
read-only: correction controls remain disabled until supervisor-owned durable
correction, physical replacement and undo are integrated. Source wiring and
component tests do not qualify installed GUI playback or full174 acceptance.

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

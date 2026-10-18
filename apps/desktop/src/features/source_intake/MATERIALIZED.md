# Native materialization to local queue (same Issue207 / PR208)

**NOT_INTEGRATED.** This follow-up adds the requested hook/API and a minimal
shared-main patch for the integrator's published snapshot
`0c1e3662c21341a5d330039d82ff321d15c6a6ce`. It does not run or change Review,
original jobs, model work, native/provider contracts, or another worktree.

## API and minimal shared glue

`materialized.ts` exports:

- `materializedSource(observation)` returns a receipt only for the exact native
  `source_downloaded` event. Ready/preparing/status, completed enumeration,
  progress and even a source_items row marked downloaded do not qualify.
- The native path is preserved byte for byte, including commas, Vietnamese and
  Windows verbatim prefixes. Shape/UTF-8/id/hash/positive-safe-size checks are
  secondary guards; they do not replace the native path/hash validation.
- `enqueueMaterializedSources(events, queue)` feeds one exact path at a time
  through the existing QueueController.addPaths boundary. It reports queued,
  already_present and per-item errors; no separate premature delivered flag can
  suppress an item after a capacity/storage error.
- `createSourceEventPump(queue, invoke).poll()` is the single consumer of
  source_service_events_v1. Concurrent polls share one in-flight call, and the
  returned batch preserves every raw event for status rendering. Only validated
  downloaded receipts are passed into the local queue.

```ts
const sourceEventPump = createSourceEventPump(queue, (command) => invoke(command));
// After the trusted current-service bootstrap, from the one source observer:
const { events, materialized } = await sourceEventPump.poll();
// Render events; report materialized.rejected. Do not enqueue in a second hook.
```

`wiring/materialized-0c1.patch` supplies the import/pump initialization only.
Its exact main.ts Git blob, LF SHA256, patch hash and required caller wiring are
in preimages.json. The integrator owns the polling trigger, source status UI,
trusted manage/bootstrap, native explicit download dispatch and Tauri build.
This patch is a separate follow-up on0c1, not an alternative legacy184 server.
The current-service host patch still requires its own exact native preimage.

Path-idempotence is provided by the existing QueueController snapshot: replay
and queue reopen do not add a second row while that path remains in a correctly
persisted queue. This is not a new durable transaction across native SQLite and
browser storage. A crash between destructive event drain and queue persistence,
user removal/re-enqueue, storage failure, or recovery of an old downloaded row
needs the broader168 reconciliation/validated readback flow. No source_items
snapshot is silently promoted to a new materialized receipt to hide that gap.

## Deterministic evidence

`source_materialized.test.mjs` runs directly and is also imported by the existing
source_intake test, including its --wire execution from required Rust Integration.
PASS: only downloaded events enqueue; preserved comma/space/Vietnamese/verbatim
paths; replay/reopen produce one queue row; malformed/multibyte/relative/hash/size
errors retain valid neighbors; capacity errors can be explicitly retried after
capacity changes; concurrent polls drain once; local input stays available while
a poll is pending. Strict TypeScript/bundle build passes. These are module tests,
not a claimed installed/native download.

## Actual pinned-f68 native probe — FAILED before materialization

The coordinator authorized a new Codex2 DATA scope using the readonly admitted
native producer, without any original/init/Review/media/model job replay. Before
launch, the supplied hashes matched:

- root `D:/DubFlowQA/native-review-current-f68d2e7-20261018/bundle`;
- manifest `c79cb3a173890cd3fd53e16295acd5166a8d5f4a69596dc187dc672ea5e98c63`;
- supervisor6876160B, SHA256
  `b693435dcc23d231d154ebd23332591a231f3c4685409707d9c5903c6016194e`;
- manifest source f68, version0.1.0-review-f68d2e7,35950 files/1942187685B.

Optional `tests/source_intake/native_probe.rs` was compiled outside the repository
with the existing GNU/LLD harness. It used the unchanged persistent adapter and
exact native CLI,180s startup admission budget, original15s request delivery and
3s retirement. Native workers use -I/-S/-B; the probe requests no bundle writes.
The new DATA is `D:/DubFlowQA/codex2 nguồn, source-f68-20261018-0546`.

Target: the exact f68 raw GitHub landscape.mp4 fixture supplied by the coordinator
(expected1621B/SHA25629e571877a9e6a79657a22f7173cbc94a6ccf390a4921c187b327b9317c08709).
The probe required actual start_video enumeration/worker retirement, then items
and an explicit original-producer download; it never equated enumeration with
materialization. Its f68-specific observation of two completed events is QA
logic for that pinned recipe, not a new universal RPC/worker-retirement contract.

Observed: **actual source_ready and source_preparing only**. After120s without
enumeration completion, the bounded observer failed and retired the owned tree.
Final snapshot: stopped, retired=true. Readonly SQLite inspection after retirement:
zero scans, zero items. No media download command was sent, no downloaded receipt
was fabricated, and no job was queued. The public stream does not distinguish
producer hashing/admission from provider inspection here; the cause is not
asserted to be an HTTP failure. No unchanged-condition retry was made.

Evidence: native-probe-f68.json; raw probe.ndjson/result.json remain in the new
DATA directory (6 files/86907B at observation). Manifest and supervisor hashes
were independently reread unchanged afterward. This is actual f68 startup/stop
evidence and a failed materialization probe, not qualification of snapshot0c1,
PR208, Douyin/Bilibili, the desktop host or complete168/175 acceptance. Native
producer preparation/inspection completion is the concrete remaining dependency
for live materialization evidence; shared entrypoints/native qualification stay
with the integrator.

## C2-06 enumeration-to-download gate (candidate on b8)

Before showing/sending Download, the current controller requires the private
native source_status.enumeration_retired === true at the observed dispatch
revision, in addition to completed enumeration and an explicit discovered item
identity. Native emits that positive observation only after successful terminal
validation and child exit/reap. Page completion and source_items never authorize
the transition. Cancellation/failure/stale or malformed observations cannot
resurrect a retired row or enqueue media. A lost positive observation can be read
back explicitly with refresh(scanId) using the existing status command.

This gate does not alter the materialization receipt/pump, deduplication or
local QueueController boundary: only a validated selected source_downloaded
event enters the local processing queue. Prior media and admission records are
preserved on enumerator cancellation/failure. A restarted service does not infer
old worker-exit success from completed SQLite rows; the broader old-result and
destructive-drain reconciliation gap remains open. Matching native/controller
release compatibility and exact execution evidence are in CURRENT_SERVICE.md and
the C2-06 handoff. This candidate has not qualified a packaged GUI/native source
flow or repeated the old f68 probe.

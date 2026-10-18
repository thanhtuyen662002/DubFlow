# Source intake hardening handoff (#207 / Draft #208)

Status: **NOT_INTEGRATED**. This PR changes independent modules/tests only.
Main base: `2bd7f51129d19dc63ea1dc82f20f77e193da6fcb`. It does not change the
integrator's worktree, shared entrypoints, manifests, lockfiles or workflows.
The PR body records the final source HEAD and exact-head CI URLs.

Confirmed on #184 `90f580f8c18b97591f1d86cb1f2c754899d833ca`:

- Comma splitting queued truncated legal filenames and URLs (r4235512030).
- Slash collapsing merged distinct generic routes (r4235512034).
- JS character limits differed from Rust byte limits, and whole-batch native
  validation rejected valid neighbors (r4235512036).
- Synchronous `read_line`, undrained stderr and cleanup after read completion
  could hang all source commands indefinitely (r4241755444).

The replacement APIs are:

- `model.ts`: `parseSourceInput` accepts one reference per CRLF/LF/CR line;
  literal commas/spaces and generic path/query ordering survive. Rejections
  identify the input line without reflecting secret URL values. Limits count
  UTF-8 bytes: reference 4096, identity (including prefix) 1024, source ID 512,
  provider 64, accepted items 10000. Existing queue limits stay unchanged.
- `controller.ts`: `submitSources` adds local paths before awaiting any online
  command, reports each rejected item, and chunks network submissions by 64.
  Other local submissions have no dependency on an online promise.
- `host.ts`: scoped DOM wiring with a per-item errors list. It does not disable
  the local picker, processing button, or subsequent source submissions.
- Native `source_intake.rs`: `admit(Vec<Value>) -> Admission` validates and
  deserializes items independently. Invalid types, byte limits and duplicates
  become indexed rejections; accepted items retain their complete references.
- `source_transport::request`: synchronous on a blocking pool, with observed
  wall deadline (15s), separate retirement bound (3s), 16MiB request/stdout
  caps and 1MiB drained/discarded stderr cap. Reads/writes run on owned threads;
  the observer never performs blocking pipe I/O or joins unfinished threads.
  Success requires EOF, a completed write and actual zero process exit.
- The process starts inside a new Unix process group or, on Windows, suspended
  until assigned to a private kill-on-close Job Object. Every outcome retires
  the owned subtree and observes/reaps the child before joining I/O threads.
  `AtomicBool` cancellation also interrupts blocked writes/reads.
- `source_transport/host.rs`: five async Tauri commands use `spawn_blocking`;
  dropping the waiting future sets cancellation. `json_request` validates a
  single response after cleanup, with static redacted errors.

Public source1, worker1, producer pins, artifact formats and SQLite schema2
are unchanged; no migration or repinning occurs. The candidate private desktop
response adds `rejected: [{index, code, field}]`, and all-invalid input returns
`source_scan_rejected` without opening/creating a scan. Older consumers can
ignore the additive field, but matching new UI/native glue is needed for
per-item feedback. Legacy generic path identities already stored by candidate
184 are not rewritten; use a new intake for distinct previously-collapsed URLs.

## Applying the integration patches

The two patches are **alternatives**, never sequential patches on one tree.
`wiring/preimages.json` records every exact preimage Git blob, LF SHA256 and
patch SHA256. Check those before applying; refuse a mismatched file instead of
overwriting changes. Both alternatives were applied in separate detached
worktrees owned by this task, not in either worker's active checkout.

1. Bring the module/test-only commit(s) from #208 into an integration branch.
   For candidate184, replace its existing model with this corrected model.
2. On exact candidate184, `git apply --check candidate184.patch`, then apply it.
   This replaces the sync Tauri helper/commands with the module host, connects
   UI/error-list wiring, and changes the native request to `Vec<Value>` plus
   per-item admission. Accepted entries checkpoint; rejected entries are
   returned separately. The patch intentionally does not implement downloading.
3. On exact integration185 `f68d2e790df0b7577006f22357495317fc6cf3be`, use
   `integration185.patch` instead. It preserves existing Review handlers and
   wires the UI/Tauri modules only. **That tree has no candidate184 native
   enqueue/status/pause/resume/cancel dispatch.** Until the integrator reconciles
   source service plumbing, online input returns unavailable/rejected; local
   intake remains useful. Do not call this alternative full integration.
4. When reconciling candidate184 native plumbing into current main/185, use
   the corrected `admit`/response code from candidate184.patch and rerun the
   actual native stdio/durable-queue tests. Do not restore whole-batch typed
   deserialization or synchronous source commands. Recheck current source-owner
   and local-job recovery semantics rather than transplanting the old server
   wholesale. Materialization review r4243121352 remains in #168/#184 scope.
5. Run focused/native/local regressions, both required lanes, and the host build
   after wiring. Refresh Tested-Base-SHA against current main. Keep Draft;
   this handoff authorizes no merge, release, or completed #168 claim.

No CSS change is needed. Manifests and dependency pins are unchanged.

## Evidence

PASS locally:

- `node --experimental-strip-types apps/desktop/test/source_intake.test.mjs`:
  Vietnamese/comma/space filenames, URL commas and distinct paths/query order,
  exact/over UTF-8 limits, mixed lists, individual errors and network batching.
- `npm test --prefix apps/desktop`: existing local queue and voice regressions.
- `npm run build --prefix apps/desktop`: strict TS and bundle, including new
  modules. Both privately assembled candidate184 and integration185 alternatives
  also pass their own existing npm tests, source tests and TS/bundle build.
- Native Windows GNU/LLD standalone harness for the exact owned Rust test files:
  3 boundary tests (including executed TS-to-native producer compatibility) and
  6 controlled-child transport tests. Partial/no-newline stdout, silent child,
  blocked stdin, full stderr, byte overflow, error exit, parse error, cancellation,
  live parent/descendant OS observations and unrelated-neighbor survival. The
  descendant-held-pipe deadline case checks cleanup after parent exit as well.
- `python scripts/validate_governance.py`; `git diff --check`.

The ordinary local MSVC Cargo command **FAILED before compilation** because
`link.exe` is absent. The standalone harness uses the installed GNU Rust1.98,
bundled LLD and an external target directory with short sysroot paths. This
does not prove MSVC/Tauri/native candidate wiring compiles. Full workspace
tests run in the existing required Integration lane on the final PR source SHA;
the Rust boundary test actually invokes the TypeScript test rather than silently
skipping it. The source app path selects `supervisor-workspace` in the existing
registry; no required lane/workflow was edited.

NOT_RUN: installed/native Tauri host build, fully compiled native candidate
wiring, live providers, complete materialization or desktop E2E, GPU/release.
These remain integration/qualification work. Read final exact-head hosted CI
results in the PR before treating the component tests as remotely passed.

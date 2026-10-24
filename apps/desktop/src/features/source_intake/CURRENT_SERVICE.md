# Current source-serve adapter handoff (Issue207 / PR208)

**NOT_INTEGRATED.** Follow-up to the concrete f68 service finding. Source pins,
native/provider/worker decoders and shared entrypoints are unchanged. Legacy
candidate184 wiring remains only for the isolated candidate184 alternative.

## Confirmed current API and lifetime

Inspected exact PR185 `f68d2e790df0b7577006f22357495317fc6cf3be`:

- `main.rs:719` routes `source-serve` to the existing source_server module.
- `source_server.rs:2340` requires `--root`, `--data-root`,
  `--manifest-sha256`. No literal `server` or `--model-root` is accepted here.
- Before `source_ready`, Runtime::admit verifies the complete pinned manifest
  and final inventory, verifies the owned supervisor, and opens SourceOwner.
- Native requests are closed: start_video/start/status/items/download/resume/
  pause/cancel, download controls/retry, session operations and shutdown.
- Request lines including newline are at most64KiB. Native checks public refs,
  original producer pins, permissions, CAS/durable records and worker receipts.
- Closing stdin calls native shutdown; it cannot be done after each request.
- Native source_status may be either a command result or an asynchronous page
  commit. There is no request ID or universal RPC correlation in this protocol.

## Module API

`source_transport/service.rs` provides:

- Immutable `LaunchPin { root, data_root, manifest_sha256 }`. `command()` derives
  only `root/app/bin/dubflow-supervisor.exe` with the exact current CLI. The
  manifest pin is passed unchanged; no replacement/computed shortcut is used.
- `AdmissionWindow::new(Duration)` is required, finite and nonzero, with no
  default. The trusted entrypoint supplies the startup inventory budget. The
  adapter never skips hashing or stretches the15s request timeout to fake ready.
- `Client::launch(pin, admission)` returns a background actor in `admitting`.
  Only an observed source_ready/schema1/single_writer=true transitions to ready.
- `Client::submit(NativeRequest, Arc<AtomicBool>)` bounds serialization/pipe
  delivery to15s. Request bytes are capped at the stricter64KiB native limit;
  oversized requests are refused locally without retiring a healthy service.
- Success returns only `{delivery:"submitted"}`: completed pipe write, **not**
  native acceptance, durable commit, download completion or matching RPC reply.
- `drain()` delivers observed native events in stream order with monotonically
  increasing host observation sequences. These are not native request IDs.
- `snapshot()` separates admitting/ready/stopping/stopped, static failure and
  observed retirement. Ready records startup observation, not ongoing health.
- `stop()` requests whole-service retirement. Sending native Cancel/Pause instead
  preserves service stdin/owner and lets the native implementation stop one scan.
- Last client-handle drop also retires the owned service subtree. No automatic
  restart/retry, changed manifest, producer repin or legacy fallback occurs.

The actor owns the process before execution using the existing OS tree guard.
Stdout is incrementally framed and parsed; a partial line has the unchanged15s
observation deadline. The existing16MiB stdout cap is lifetime cumulative, never
reset per request. Stderr is drained/discarded with the unchanged1MiB lifetime
cap. Both ingress and UI event buffers hold at most64 events. Overflow is an
explicit terminal error rather than silent event loss. Kill/reap/join retirement
keeps the existing3s bound, including descendants holding inherited pipes.

Submission timeout/cancellation during an uncertain write retires the whole
stream rather than reusing potentially partial JSON. A request refused before
writing may leave the healthy service alive. Stop/shutdown states distinguish
signal delivery, source_shutdown observation, actual zero exit and retirement.
No native/provider work or durable write is implemented inside this adapter.

## Concrete shared-entrypoint support required

The integrator must provide one trusted installed-release bootstrap result:

1. Exact admitted installed root, external writable data root and expected
   manifest SHA256 from its trusted release/install context. Never take these
   values from webview command arguments or change them between requests.
2. An explicit finite budget for full inventory startup admission. There is no
   safe universal15s assumption and no native startup progress receipt in f68.
   Until the existing host selects this policy, startup integration is incomplete.
3. Manage exactly one SourceServiceHost per selected data root/app host:

```rust
// Values come from the trusted installed-release bootstrap, not the webview.
let source_host = source_host::SourceServiceHost::new(
    source_transport::service::LaunchPin {
        root: installed_root,
        data_root: approved_external_data_root,
        manifest_sha256: expected_manifest_sha256,
    },
    source_transport::service::AdmissionWindow::new(source_admission_budget)
        .map_err(|_| "invalid source admission policy")?,
);
// Add to the existing builder, retaining Review's managed state:
// .manage(source_host)
```

`wiring/integration185.patch` applies to the exact native main preimage in
`preimages.json`. It mounts `service_host.rs` and five handlers, preserving all
Review handlers. It deliberately does not invent that trusted bootstrap or its
budget. Without `.manage(source_host)`, calls fail closed due to missing state.
No TS/HTML/CSS/manifest/lockfile/workflow change is included in this alternative.

The module's five Tauri commands are source_service_start_v1, state_v1, events_v1,
send_v1 and stop_v1 (each with the source_service_ prefix). Start has no webview
root/pin arguments; send accepts the existing NativeRequest closed schema.
The old enqueue_sources UI is not compatible and must not be treated as wired.

A UI requiring exact per-command RPC acknowledgement needs native reply
correlation, which f68 does not supply for some events. Do not invent request_id
fields: the native closed decoder rejects them. Either consume the ordered
observation stream with truthful delivery states, or separately design/version
that native contract through the repository ADR/compatibility process. This PR
does the former and changes no public source1/worker1/schema2 contract.

## Evidence and remaining integration

Eight new controlled-child tests PASS on Windows GNU/LLD, using the checked-in
`source_intake_transport.rs` persistent tests and the same owned-tree guard:

- CLI/pin arguments and closed current schema; legacy/request_id refusal;
- slow startup outlasting the short fixture write budget without premature ready;
- startup timeout, wrong ready, cancellation and observed child retirement;
- native scan cancel keeps the service/owner/stdin live, followed by status and
  explicit source_shutdown/zero-exit/retirement;
- stderr pressure, partial frames, stdout/stderr caps and event overflow;
- pipe backpressure with finite write timeout/cancellation and service retirement;
- local oversized-request rejection retains a healthy service; dropping its last
  handle retires the actual active parent/descendant tree.
- A source_shutdown event without actual exit is refused and forced retirement
  shares one budget: the grace period does not add a second3s cleanup allowance.

Production15s/3s/caps are unchanged; short test write/partial bounds are80ms.
The admission-timeout fixture uses1.5s to observe the actual live process on the
Windows loader, separately from the80ms write bound. No assertion is skipped.
The old nine tests were not rerun locally just to repeat unchanged evidence;
required Integration executes the full workspace and all17 tests on final HEAD.

The new patch applies cleanly in a new task-owned detached f68 worktree. No
existing PR185 checkout, native QA run, shared main, source_server/owner/decoder,
worker, model or pinned artifact was changed. The later [MATERIALIZED.md](MATERIALIZED.md)
follow-up records an actual readonly f68 source_ready/start_video probe: startup
passed, but producer preparation did not finish within the bounded observation,
and materialization was not reached. Full Tauri/MSVC host compilation, managed
desktop bootstrap/UI orchestration and completed #168 qualification remain
unproven. Final exact-head/base CI URLs and SHA are recorded in the PR body.

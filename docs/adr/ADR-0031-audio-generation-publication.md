# ADR-0031: Complete audio generation publication

Status: Proposed — leaf #209 / Draft PR #210. This is a scoped repair of PR182,
not completion of #171 or authorization to select AUD-2 or release DubFlow.

## Evidence and decision

Candidate PR182 `7f39ed5e89394852543a01adca8c78691a3923d4` replaced three
public WAV files separately. The regression-first commit
`e51d1734e3b43b19da534448e9268a32be6eff87` inherits its production module
unchanged. A native Windows run fails the previous-result hash assertion on
replace 2 and 3, reproducing review `discussion_r4235461079`.

Keep the CPU gate and its `dubflow-cpu-gate-v2` DSP identity. Introduce an
explicit version-1 publication layout, independent of the schema-v2 result:

```text
output/current-audio.json
output/.audio-generations/<32 lowercase hex generation>/
    source_audio.wav
    dialogue_stem.wav
    background_stem.wav
    result.json
```

The supervisor serializes publishers for an output directory. A fresh UUID
directory `.<generation>.partial` owns all candidate writes on that volume.
Close WAV headers, flush/fsync every member, check unchanged input identity,
then read all three PCM payloads and verify sample rate, signed-16 format,
channels, frame count and SHA256. Missing/truncated/inconsistent data cannot
advance. `result.json` binds the three paths and hashes, metrics, warning and
fallback. Write/flush/fsync/read back that bounded manifest before sealing the
directory with a rename. Sealing is not publication.

Write/flush/fsync/read back a bounded pointer containing `publication_version`,
the generation name and manifest SHA256. **Replacing `current-audio.json` is
the sole visibility boundary.** Individual member renames or directory scans
cannot authorize completion. POSIX directories are fsynced in dependency order.
On Windows, Python does not expose directory fsync; same-volume replacement is
tested for process termination and native sharing locks. Power-cut/filesystem
or device-cache durability is NOT_RUN and remains a separate qualification gate.

An exception before the pointer replace leaves A current. After the boundary,
current can be complete B even if the caller never received success or a POSIX
directory fsync then fails. A's paths and hashes survive in either case. Readers
resolve the pointer once and keep the resulting immutable paths, so subsequent
publication cannot mix their A source with B stems. The module never unlinks a
generation, legacy result, partial directory, or orphan pointer. Retention/GC
requires the supervisor's actual reference graph and is outside this leaf.

## Restart and compatibility

`load_current(output_dir)` reads one pointer, verifies the manifest hash and
generation containment, then verifies every recorded file. It returns a
`SeparationResult`, or `None` when no publication pointer exists. Invalid current
data raises an error; it never silently falls back to the newest directory.
The supervisor can still validate a previously pinned result with `read_result`.
These files are integrity evidence, not authentication against an attacker able
to rewrite all bytes and hashes. Workers do not mutate SQLite.

Restart classifications are explicit from layout and validated evidence:

- referenced sealed directory with valid manifest/files: published;
- sealed directory without current reference: retained orphan/unpublished;
- `.<generation>.partial`: incomplete, even if all WAV names exist;
- `.current-audio.<generation>.partial`: uncommitted selection, ignored.

Retry starts a fresh generation from the input. This repair does not promise
mid-analysis checkpoint reuse or auto-promote an orphan. It preserves all such
evidence for later supervised reconciliation. Disk-full is an ordinary failed
candidate; it cannot authorize deletion of old generations to make room.

`process(source_path, output_dir)`, `SeparationResult` fields, its schema-v2
`to_dict()`, and metric meanings are unchanged. Returned paths now identify a
generation. Consumers must use the returned/recorded paths; do not reconstruct
three mutable flat filenames. No aliases are maintained because independent
alias updates would recreate the bug. The package adds exports for this existing
candidate API and the two validating readers; benchmark exports remain intact.

Historical flat files stay untouched. `read_result(recorded_schema_v2_result)`
validates their metadata/frame counts/hashes before returning the same API.
A bare legacy directory cannot prove a historically consistent set: without
recorded hashes, retain it for inspection and recompute in a fresh generation.
No automatic migration or relabeling occurs. Older consumers of recorded paths
can keep reading them. Downgrade must preserve this store and continue using
pinned paths; an old writer cannot interpret the new current pointer.

This changes the optional backend's private output layout. Existing job/runtime
pins must remain immutable. An integrator choosing this publication format must
pin this producer revision/publication version and invalidate old unsafe reuse;
do not copy these modules into an already pinned runtime or mark old jobs as
new-format successes. No migration, worker protocol, canonical timeline or
model/lockfile changes are included.

## Bounds, consumer handoff and verification

PCM iteration and output buffering use 4096 frames. Hash reads are at most
1MiB; JSON documents at most64KiB (plus one overflow-detection byte). Analysis
levels spool as binary doubles. Eight radix passes select exactly the previous
positive-level 20th percentile with 256 counters and 32KiB reads, eliminating
duration-sized sorted/active lists without changing gate decisions. Temporary
spool lifetime follows the processing context; interrupted output directories
are retained. No whole WAV is loaded into RAM and no model/cache is added.

Residual speech/background quality stay `None`, decision stays `AUD-0`, and the
existing source duck/mix fallback remains authoritative. Synthetic activity is
not speech-quality evidence.

PR182 and current main do not yet call this optional backend from
`worker/production_job.py`. There is no existing cleanup call to rewrite, so
this leaf includes no shared-entrypoint patch and claims no worker integration.
To use the repaired existing API, call `result = CpuAttenuationBackend().process`
with the source PCM and a supervisor-owned stage directory, persist
`result.to_dict()` only after return, and consume its three recorded paths and
hashes. Restart can resolve `load_current` or verify an explicitly pinned result
with `read_result`; AUD-0 remains selected. Selecting/wiring an optional cleanup
stage and its producer pin belongs to the #171 integrator, with fresh pipeline
QC/export evidence. Ordinary editable audio copies are a different publication
scope and are unchanged here.

Tests distinguish injected read/write/flush/fsync/replace exceptions from a
parent actually killing child processes at nine publication milestones and
recovering with a fresh process. Native Windows `CreateFileW` denies sharing
for the current pointer and an old stem to test actual rename/retained-reader
behavior. Linux skips that test with explicit NOT_RUN. Other regressions cover
bad candidates, corrupt committed data, leftovers, short document writes,
legacy recorded results, exact percentile selection, source mutation, bounded
large-file I/O/memory, API and AUD-0. These tests do not qualify installed runtime,
six-hour media, real speech quality, GPU, power loss or the full release gates.
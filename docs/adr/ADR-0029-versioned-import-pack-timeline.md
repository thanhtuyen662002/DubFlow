# ADR-0029: Versioned portable import-pack timeline

## Status

Accepted for Issue #172; production integration remains in progress.

## Context

Released import-pack v1 has assets and tracks, with no timeline requirement.
Making that field mandatory under the same version rejects valid saved output.
Treating millisecond aliases as ticks also shifts dialogue when the declared
time base differs from 1/1000. Wide JSON numbers lose precision in editors.

## Decision

Keep the released schema-v1 unchanged. New producers with an explicit canonical
mapping emit schema-v2. Its reduced time-base numerator/denominator are positive
unsigned 64-bit decimal strings; duration and cue boundaries are nonnegative
signed 64-bit decimal strings (duration may be null). No float or frame identity
is introduced. The adapter validates and serializes this mapping before publish.

The input API accepts integers or canonical decimal strings. Millisecond aliases
use exact rational rescaling from 1/1000 into the declared base, rounding starts
down and ends up to cover the supplied interval. Explicit ticks retain their
values. Dual representations must match those conversions or publication fails.
Arithmetic overflow, malformed values and invalid ranges fail before promotion.

## Compatibility and migration

Readers dispatch on manifest schema_version. Valid released v1 packs remain
readable, movable and hash checked without adding a synthetic timeline or
rewriting bytes. Legacy callers without a mapping still emit v1. Readers also
accept the unmerged historical v1 draft's valid optional integer timeline without
changing it. V2 requires the normalized decimal-string timeline. Unknown future
versions fail explicitly; existing job/runtime versions remain pinned.

An old v1 reader rejects v2 explicitly. Consumers must select a v2-capable adapter
before producing a direct draft. Importing v1 gives only the timing information
actually present; recovering canonical cue identity requires the original
canonical project, not a zero-cue or guessed migration. No in-place migration or
durable database change is required. Standard MP4, subtitles and audio remain
available independently of any direct CapCut adapter.

## Complete editable resources and private copy recovery

V2 resources include the raw source video, localized MP4, both SRT/ASS, the
unchanged canonical timeline and every available original/dialogue/final-mix
stem. The production assembler accepts the current canonical job-manifest v1,
checks its QC and artifact hashes/sizes and binds all copies to those hashes.
Source-derived cue ticks are projected into the pack descriptor; text,
confidence and native source clock mapping remain in the unchanged timeline
asset. A B1/no-audio result does not create placeholder audio.

Each v2 track identifies its resource role and default activation. Raw video is
the active editing source; the localized render remains available as an
alternative. With a final mix, that mix is active and the component stems and
embedded video audio are disabled. Only one subtitle alternative is active.
Readers reject simultaneously enabled alternatives/component mixes. Direct
handoff additionally requires an explicit caller-selected controlled directory.

Copying large video/stems uses bounded 4 MiB buffers and a private checkpoint
folder identified by the complete content/format/timeline pins. Inputs are
hash bound; a resumed copy compares the entire retained prefix, preserves the
proven prefix and recomputes an invalid private tail once before final hash
verification. Incomplete metadata markers resume only when their bytes match
the current pinned request. Unrecognized folders/markers are retained and
refused. A completed independently validated folder is renamed atomically to
its final sibling; its hidden input-pin receipt contains no absolute paths.
Canonical exports and durable SQLite state are never mutated by this adapter.
Older random private staging directories are not silently adopted or deleted.

The new private copy recipe is `portable-import-pack-copy-v2`. Existing jobs keep
their producer/runtime pins; a changed request produces a different private
checkpoint identity. Worker/desktop integration will publish the pack path and
represent optional failure as a visible scoped warning, preserving standard
output. That integration and installed Windows qualification remain required.

## Evidence and limits

Optional installed discovery uses the versioned read-only
`windows-uninstall-registry-v1` adapter. It bounds HKCU/HKLM enumeration in both
32/64-bit views, closes handles and preserves exact three/four-component build
strings. Access denial, changed/incomplete metadata and multiple registrations
prevent version selection. `NOT_REGISTERED` describes only this registration
scope; it does not assert that every portable executable is absent. No arbitrary
path, uninstall command or executable is read/launched. Ambient environment
hints do not count as installed evidence. Registration does not establish a
tested direct-draft format; the production compatibility matrix stays empty
until actual installed vendor-version qualification is recorded.
The production boundary also catches optional failures before draft generation
(such as permission-denied directory creation or malformed compatibility data),
returning the independently validated pack with a scoped fallback reason.

Registry fields/views follow the Windows
[uninstall metadata](https://learn.microsoft.com/en-us/windows/win32/msi/uninstall-registry-key)
and [alternate-view API](https://learn.microsoft.com/en-us/windows/win32/winprog64/accessing-an-alternate-registry-view).

Regressions use an unchanged fixture generated by the released main v1 producer,
90 kHz rescaling, fractional tick intervals, conflicting aliases, wide values,
overflow, moved folders and corrupted assets. These establish format correctness;
they do not qualify the pending worker/desktop wiring or a live CapCut version.

## Production publication and desktop validation

The worker builds a pack after the standard MP4, subtitles, timeline and QC
are valid. `production-editable-pack-v1` binds the job, every copied resource
hash and the pack producer source digests. Completed copies and interrupted
copy checkpoints live under `.dubflow-work/editable-pack/<identity>/`; they
cannot be published as unfinished editable resources. QC timestamps and
absolute staging paths do not cause a new copy identity. A completed candidate
is reused only after independent full resource validation against the current
canonical hashes. Changed/corrupt/unrecognized candidates are retained and
refused; they cannot be relabelled as a successor pack.

The verified directory is renamed to pending `editable/import-pack` and
participates in the existing editable-directory publication journal. The
standard export and pack are committed/rolled back together. No publication
journal, SQLite or worker wire schema changes. Optional copy/discovery failure
preserves standard MP4/subtitles/audio/timeline and private checkpoints, with
an explicit unavailable descriptor and scoped QC advisory. There is no
automatic unbounded retry. The controlled direct-vendor matrix remains empty
until genuine installed-version qualification; portable output does not
require vendor discovery or launch.

Canonical job manifest v1 gains an optional `editable_pack` descriptor v1:
available/unavailable state, fixed relative path, exact manifest hash,
content/producer binding and optional discovery/fallback metadata. Available
packs also have a normal hashed `import_pack_manifest` artifact. Existing
readers retain standard artifacts; new readers report legacy exports without
a descriptor as unavailable rather than inventing a pack. Existing admitted
jobs keep their old runtime and bytes. No in-place migration or repinning;
rollback retains the previous producer and standard exports.

The desktop obtains a completed job's output from supervisor status, invokes
the current app-owned Python with `-I -S -B` and independently checks canonical
QC, job identity, fixed path, manifest hash and every portable resource. This
read-only check uses no SQLite writes, system Python, vendor paths/executables
or inference. It runs off the UI thread with a 60-second observation bound
and bounded report pipe. Only a successful independent report exposes a pack
path. Verification is session-local; restored queue paths must be checked
again. A manual recheck detects edits/removal without rewriting job state or
discarding the completed video. Native compilation, installed Windows worker
and GUI usability remain separate required evidence for this integration.

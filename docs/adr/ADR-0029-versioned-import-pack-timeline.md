# ADR-0029: Versioned portable import-pack timeline

## Status

Accepted for Issue #172; production integration remains in progress.

## Corrected Review standard-bundle input

An additive `create_pack_from_review_bundle` entry consumes the explicit
`review_corrected_standard_bundle`/`review-corrected-standard-bundle-v1` format
owned by Review (PR185, ADR0030). It requires a trusted manifest SHA256 and the
complete expected current token, including decimal public revision, snapshot,
graph and immutable-original manifest hashes. It does not fabricate a legacy
job-manifest v1 or rewrite the original job/runtime/model pins. Native current
admission remains the supervisor's responsibility; this Python adapter handles
offline bytes and returns `current_generation_checked: false` in its binding.

The adapter requires native canonical manifest bytes/generation identity,
separate original token, snapshot byte hash/job/wide integer revision, the exact
ordered B1/B2 resource set and a closed directory inventory. Inputs must be
sealed, single-link regular files without symlink/reparse ancestors. It streams
full SHA256/size checks under a720s validation bound and rechecks open-handle/path
identity. Windows opens existing files read-only through
[CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
with `FILE_FLAG_OPEN_REPARSE_POINT` and read sharing only; the handle transfers
to the app-owned Python runtime using
[open_osfhandle](https://docs.python.org/3/library/msvcrt.html#msvcrt.open_osfhandle).
No executable, account, registry, vendor database or SQLite state is accessed.

The unchanged existing production v2 assembler then copies raw/localized video,
both captions, exact canonical timeline, QC, the hashed Review manifest and all
three B2 original/dialogue/final-mix PCM resources. B1 introduces no PCM/mix. The
descriptor projects canonical tick mapping through the existing rational clock
adapter; original text/confidence/source-clock bytes stay in the unchanged
timeline asset. The new pack ID includes the complete generation key, so prior
generations and copies cannot be relabeled as current. All copied resource hashes
are independently compared to the complete expected input set after assembly.
Inputs rehash again after copying; overlap in either direction is refused.

The existing private checkpoint/copy/guide recipe and v1/v2 pack schemas remain
unchanged. A late verification refusal retains the complete pack for explicit
recovery; no automatic cleanup or source mutation occurs. Direct vendor creation
is not selected by this entry. Production adapter processing-release admission,
native final-current-token fencing/publication and installed whole-flow evidence
remain required before this is advertised as the current editable output.

Five new Windows Python byte-fixture tests pass (0.547s): B1/B2 mode/default
tracks and complete resources; moved bundle/pack,90kHz tick mapping and revision
9007199254740993; changed token/equal-size corruption/extra resource; role/path/
snapshot/duplicate metadata; overlap/hardlink refusals. Their first run had a
test-only readonly-hardlink unlink error after the correct refusal; the owned
fixture teardown was fixed and the failure retained. These tests do not qualify
encoded media, installed native/CapCut, real crash or long-form acceptance.

## Isolated Review editable worker and guarded private copies

`worker/review_editable_pack.py` accepts only app-owned Python `-I -S -B`, the
native byte gate and a sealed, hash-pinned closed context. The versioned context
binds the full current token, native published standard-bundle generation/root,
storage root, processing recipe identity and explicit storage limits. It derives
the only candidate location as `.dubflow-review-media-v1/<job-SHA>/`
`.editable-pending-v1/<generation>/import-pack`; arbitrary output paths are not
accepted. The actual published standard-bundle directory must match the native
job/generation namespace by physical identity after no-link checks.

The native caller must retain its existing output-wide writer lease and fixed
volume admission across this child. The worker does not manufacture either
authority. It counts every file/orphan/old version in the namespace with bounded
depth/entry/deadline and no-follow/single-link checks. An optional storage guard
threads through the existing production/Review/v2-copy APIs. Its default is
`None` for legacy callers; no public pack schema or private content recipe is
changed. The guard runs before copy/metadata writes, at4MiB copy boundaries and
before promotion, reserving all remaining resource/marker/guide/manifest bytes.
Already present prefixes/metadata are subtracted only for allocation accounting;
their content still undergoes the existing prefix/full-hash checks.

Maximum aggregate may not exceed32GiB and free reserve may not fall below2GiB.
Windows explicitly queries caller-available bytes through
[GetDiskFreeSpaceExW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getdiskfreespaceexw),
refusing unknown/inconsistent observations. It does not substitute total volume
free bytes for quota-limited caller capacity. A quota/space refusal preserves
private checkpoints and all prior outputs; a changed available-space condition
can resume. Complete candidates are reused only after independent full pack
validation and exact resource binding to the same standard-bundle manifest.

The bounded result reports the context hash, recipe identity, full token,
candidate path/manifest hash and resources. Native must independently admit all
candidate files, processing/source/volume/quota/current state and publication;
the worker reports no live-current, graph publication or readiness authority.
It launches no vendor executable and opens no SQLite/model files. The explicit
capability is inventoried with the new application sources; original producer,
model/runtime/media pins remain separate. Native storage inventory/layout and
final editable retention/publication integration are still required before a
real job may invoke this child. Older readers refuse the new editable directory
tag; existing historical ROOT/jobs are not used for new worker execution.

Eight focused Windows Python cases pass (5.205s): the prior five bridge cases
plus actual isolated child creation/recovery/gate refusal, aggregate quota
refusal and changed-reserve failure/resume. Input and recovered pack hashes/
mtime remain unchanged. These fixture/process tests do not establish current
native product, installed vendor, real crash or long-form acceptance. Native
processing receipt coverage is authored separately in PR185 and awaits hosted
compilation; combined-release admission/final publication is not claimed.

Metadata reservations preserve the physical manifest bytes produced by the old
platform-default `write_text` newline behavior, including CRLF on Windows. A
focused filesystem assertion compares the new file against that unchanged write
operation; adding storage callbacks does not silently change the copy recipe's
manifest bytes. The fresh-space helper explicitly returns caller-available
capacity, not a guessed volume-wide free-space value.

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

## Exact vendor build identity

### Ancillary manual editing guidance

New packs include `HUONG_DAN.txt`, bounded to128KiB UTF-8, with steps derived from
their normalized default tracks and relative resource names. It names the
selected raw video, final mix and embedded-audio mute policy, keeps component
stems as editing alternatives and directs subtitle files to the caption import
surface. The user's9.6 desktop observation confirms media Import > From device.
CapCut's [subtitle import help](https://www.capcut.com/help/how-to-import-subtitles)
documents separate caption-file import. Its [project import help](https://www.capcut.com/help/import-a-previous-project-into-the-current-project)
describes importing an exported video into a project, which does not qualify
an adapter-generated native project opener. These sources do not add any tested
vendor build to the direct-draft matrix.

The guide is ancillary rather than a manifest resource or vendor draft. Public
pack schemas/resource/timeline hashes are unchanged, and older published packs
remain readable without this file. Private copy recipe
`portable-import-pack-copy-v2-guide1` includes its exact text hash, keeping old
interrupted copy namespaces intact and distinct. Current worker producer-source
binding already includes the adapter bytes, so new guidance cannot silently
rewrite a saved old job's pack. Rollback preserves previous media/pack readers;
there is no in-place migration or SQLite/updater format change. Native vendor
handoff and current installed usability still require their separate evidence.

The draft boundary accepts bounded `major.minor.patch` and
`major.minor.patch.build` identities, matching native registration discovery.
It preserves the fourth component in compatibility lookup, generated metadata
and output names. A buildless version never authorizes a four-component build;
neighboring builds require their own explicit qualification. Legacy three-part
entries retain their identity and format. Existing pinned jobs and portable
pack schemas are unchanged; no in-place migration is needed. Rollback retains
the portable fallback for four-part versions a previous adapter cannot parse.

Deterministic fixture tests establish exact version transport and reject a
backend that truncates a tested build. They do not establish native project
compatibility. The production matrix remains empty until a generated project
under a controlled user-selected directory has actually been opened, inspected
and exported with the exact installed vendor build. Vendor-created QA project
observations alone do not qualify an adapter-generated handoff.

The immutable direct-draft envelope v1 admits only three-component versions.
Successor envelope v2 transports bounded exact three/four-component identities.
New fixture producers emit v2. Readers accept both, reject a four-component v1
identity, unknown schema versions/fields and a resource inventory differing from
the pinned pack. Archived v1 objects are read without migration or rewriting;
existing jobs retain their producer/model/contract pins. A v1-only rollback
refuses v2 and returns the portable import pack. No durable database migration
or installed vendor-format compatibility is implied by envelope v2.

Envelope admission is bounded to 16 MiB and rejects duplicate JSON keys,
invalid UTF-8/non-JSON constants and nonregular/reparse output. This bound
covers all 256 schema-bounded asset paths, including escaped Unicode. A repeated
schema_version must not select v1 for one reader and v2 for another. Refusal
keeps the prior validated draft and all portable/canonical resources, and
cleans only the current private partial file. Valid v1/v2 bytes remain readable;
no schema rewrite, automatic migration or new supported vendor build is added.
### Native-pinned volume checks for private Review copying

The unpublished private editable worker context now requires `output_volume_id`.
Native supplies its OS volume pin in the sealed context. Every storage guard
freshly compares the Windows volume GUID and fixed/writable NTFS capabilities
before allowing copying to continue; caller-available bytes remain separately
checked. POSIX device IDs allow deterministic byte fixtures, while native
production storage still refuses unqualified providers. Missing/changed pins
refuse, rather than accepting a drive-letter hint or rebinding an original job.
This draft-only private context change does not alter stable import-pack v1/v2,
the guide1/content recipe, or an existing producer/model/runtime binding.

The source-matched native supervisor will independently validate/seal/publish the
candidate. The worker still reports no native-current or publication authority.
Four focused current-source Windows Python cases pass in4.940s, including a
changed-volume refusal with original source hashes/mtime unchanged. This is
component evidence; native compilation, current installed B1/B2, cumulative/
after-undo, GUI, actual crash and long-form qualification remain required.

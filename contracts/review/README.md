# Review Center contracts

`schema-v1.json` is the released display payload and remains unchanged.
`schema-v2.json` is the proposed production snapshot with job/revision,
canonical rational time base, explicit cue binding, nullable global range and
unknown confidence, and diagnostic/confidence provenance. Dispatch by version.
V1 can be displayed; missing original-job mapping cannot authorize regeneration.
`store-v1.json` is a distinct supervisor-owned atomic snapshot/undo envelope.
`preview-v1.json` describes one hash-verified PCM WAV from the isolated reader.
`export-pin-v1.json` carries the native supervisor's committed manifest identity;
only existing succeeded jobs with immutable output binding can supply this pin.
See ADR-0030 for compatibility, recovery limits and pending production evidence.

Review findings are explainable downstream data. Bound items carry a canonical
integer-tick range, source/translated text, speaker/voice assignment, output
preview, confidence and the exact downstream stage IDs that may be rerun.
Editing one cue produces a previewable regeneration plan and preserves all
unaffected valid artifacts until replacement validation succeeds.

Generic warnings are valid input when advanced QC is unavailable. Quick Mode
continues with a focused risk list; it never turns the absence of Review Center
into a full manual-review requirement.

`original-seed-v1.json` is the owned metadata adapter packet, bound to a
native completed export. `bound-original-graph-v1.json` is the distinct
supervisor-owned revision-zero envelope: exact source graph bytes and sealed
native copies of the original manifest plus all consumed JSON documents.
B1 stores three documents; B2 stores seven. This format grants no model or media
admission and cannot enable correction/undo. Unknown versions need an explicit
compatibility reader; existing released display payloads remain usable.

`original-cue-context-v1.json` binds exact native before/after/source-graph bytes
for the owned request adapter. `prepared-cue-request-v1.json` returns a prepared
request bound to that context. Native admission must independently bind catalog,
original input and stale-client token. These are preparation formats only:
model weights and replacement media remain unverified and no graph is published.

`cue-candidate-v1.json` is emitted only by the admitted owned producer after
read-only verification of the original physical model pack. Native code binds
its exact request, cue, provenance, metadata hashes and private waveform scope,
then copies and verifies PCM into sealed immutable storage.
`native-accepted-cue-context-v1.json` carries native-owned request/candidate
bytes and that accepted waveform pin to the metadata adapter.
`accepted-cue-documents-v1.json` changes only the WAV path and dependent document
hash. Native code independently checks those field changes and revalidates all
accepted objects. None of these formats publishes an active playback graph or
qualifies mix/render/QC/export replacement and undo.

`native-mix-context-v1.json` binds native original snapshots/graph/request,
sealed original and accepted cue documents, source PCM and distinct admitted
metadata/waveform roots. `audio-composition-v2.json` represents those separate
roots; unchanged v1 compositions keep their original encoding and hashes.
Readers must explicitly support v2; no v1 object or SQLite generation migrates
implicitly. `mix-candidate-v1.json` is the owned checkpointable private AUD-0
producer packet. Native admission binds all assignments/source/producer, hashes
and PCM headers. Its verified private audio is not immutable retained media and
cannot publish an active graph or qualify render/export/QC/undo.

`native-retained-mix-context-v1.json` carries exact admitted candidate mix bytes,
composition hash and native retained role descriptors. The read-only metadata
adapter emits `retained-mix-document-v1.json`, changing only the three artifact
paths while preserving producer/source/timing/chunk/confidence/quality evidence.
It does not inspect media or establish locator/volume authority from JSON.
Native code must independently bind the context, admit path-only differences,
validate canonical locators and revalidate all physical media before graph CAS.
Its result requires `native_media_revalidation_required=true` and never claims
publication. These private additive formats have no implicit legacy migration.
The native retention command uses its own path-only transformation over typed
retained objects, independently revalidates physical pins, and seals the new mix
document. It keeps the candidate document separate. The Python adapter serves as
a deterministic oracle; it is not a replacement producer or a native authority.
The desktop and active graph are not wired to these private retained documents.

`native-render-context-v1.json` binds native-admitted original manifest/timeline,
exact before/after snapshots, corrected timeline and source pin. B2 additionally
requires retained mix/composition pins; B1 cannot invent a mix. The private
owned render worker emits `render-candidate-v1.json` after subtitle generation,
FFmpeg render, stream/duration QC and full audio/video decode. Voice-only edits
preserve exact original subtitle/timeline bytes. Completed candidate resume
revalidates all inputs and five outputs and reruns probe/decode without render.
Absolute paths are confined read/write hints, not volume/locator authority.
Native release-inventory admission, contained dispatch, physical output
retention/admission and graph CAS remain mandatory before desktop activation.
The private entry point does not enable long-form chunk scheduling, editable
export or undo. Unknown versions refuse; original exports remain unchanged.

`effective-cue-context-v1.json` and `prepared-effective-cue-request-v1.json`
are distinct private preparation tags for a current generation. The caller
supplies its native token and separately admitted source/document pins. The
reader reopens the complete current composition, preserving every original
cue's timing, mapping and confidence while binding current text and per-cue
voice/document assignments. Serialized paths cannot construct admitted pins.
The prepared request retains the current public revision and selected cue's
current producer/voice provenance. Old original-only adapters refuse these tags.
These formats do not admit a processing release, execute a model, mutate SQLite
or publish a graph. Native effective-state recovery, independent request/mix
admission and versioned cumulative publication remain required before these
preparation adapters can participate in actual correction or undo.

`native-effective-state-v1.json` is a separate read-only native view of current
content and its archived origin. It keeps the current public token, current
timeline and native metadata pins separate from the revision-zero archive.
Original B2 inputs reopen every per-cue PCM pin and bind per-cue spoken-text
references to the sealed original documents. Retained publications use the
existing physical five-role render/three-role PCM reader. Legacy publication-v1
does not record the changed cue's spoken-text reference: it remains valid for
playback, but reports an incomplete generation dependency inventory. No private
checkpoint or path hint repairs that gap. Content-composition snapshot identity
is explicit and may differ from the current public token after undo. This view
does not admit a processing release, create a generation or enable the GUI.

`native-speech-inventory-v1.json` closes the missing per-cue reference set. Each
row binds the exact accepted TTS document and spoken-text document; a changed
cue additionally retains its native acceptance context, recipe identity and
sealed PCM pin. Native recovery rebuilds the inventory from its admitted request,
proposal and composition, rechecks the accepted PCM and verifies exact path-only
TTS/hash-only spoken retargets. Source mapping, confidence bits, current voice,
checkpoint identity and recorded rewrite advice remain bound.

`native-publication-v2.json` adds that sealed inventory to the first-generation
B2 publication and uses `review_bound_retained_render_graph_v2`. Existing v1
graphs and sealed bytes are read with the v1 reader and never migrated. The
effective-state reader can expose complete speech references from a v2 record.
This is a dependency retention step. Cumulative generation/current processing
capability, fixed-output per-cue retention/quota integration, corrected standard
and editable publication and full native/installed acceptance remain pending.

`native-retained-cue-audio-v1.json` adds an explicit mono cue role in the same
native output namespace, with a canonical output-volume/job/content locator.
It does not change the stereo full-mix contract. The retained-generation path
copies admitted cue PCM onto the output volume before metadata acceptance and
mixing; the resulting native acceptance context receives confined physical
paths. Every per-cue proof in publication-v2 now requires that retained locator.
Recovery derives its path from the native locator, rechecks full hash/header,
readonly/single-link file identity and fresh volume, then compares it to the
exact accepted TTS/spoken evidence. No hint or control-store path is a fallback.
The existing aggregate quota counts cue, mix, render, old/undo and orphan bytes;
copy checks fresh caller reserve every four MiB and before sealing. The legacy
private non-retained synthesis command remains separate and cannot publish a
generation. Actual current/native/installed/cumulative acceptance is still a gate.

### Effective composition identity after playback and undo

The pending effective reader carries immutable `content_composition_json` and
its exact `composition_snapshot_json` separately from the current public token.
It also exposes a new `effective_composition_json` projection. Native admission
and the owned Python preparation independently permit only monotonic revision
and playback preview differences between those snapshots; diagnostics, plans,
cue text/voices, source clocks and confidence remain exact. The projection changes
only `review_snapshot_sha256`, never rewrites accepted composition/media bytes.
Python reopens the original content with separately admitted document/source
pins before projecting. Preparation binds both composition identities and the
independently supplied content snapshot; serialized paths grant no authority.
This unreleased private successor format still needs processing capability and
cumulative native execution/publication before any actual acceptance claim.

### Explicit compatible processing release admission

`review-effective-state` may receive an explicit processing root, manifest
SHA/byte count and source commit. Native admission retains the original job
producer binding and model root, verifies both complete import inventories,
and compares every runtime/TTS/mix/model/media inventory pin. A changed,
missing or additional protected dependency refuses. The processing worker is a
separate recorded descriptor, never copied into or repinned on the old bundle.

`engine/dubflow/review/processing-capabilities-v1.json` is release-inventoried
metadata for the `prepare_effective_cue` Python function, not a runnable CLI or
cumulative processing permission. Native parsing requires its exact closed
capability/format/compatibility declaration and an inventoried function module.
`native-processing-release-v1.json` records the separate source/release and
capability/entry pins, original producer/release/protected inventory identity
and original model root. The command rechecks the current durable token after
this read-only admission. No model weights are verified or provisioned here.
Without explicit selection, the state view continues to report no admitted
processing release. Native request dispatch, current mix/render and cumulative
publication/CAS/undo remain required before actual product qualification.

### Current cue preparation dispatch

`review-prepare-effective-cue` derives an exact proposal from the admitted
current snapshot, then dispatches the inventoried isolated
`effective_cue_execution.py` adapter through native stdin byte gating.
`native-effective-cue-bindings-v1.json` carries separately admitted content
snapshot/composition/source/document/canonical input pins. It grants no UI path
authority. The adapter reopens those pins and uses the current TTS provenance,
keeping the monotonic public token and all prior cue/voice edits.

Native code compares the entire projected composition against its independently
derived projection and admits the request through BoundCueRequest. Immutable
input fields use canonical byte comparison, including confidence signed-zero
bits. Current token and physical dependencies are fenced after dispatch, before
sealing the private request/proposal. The processing capability now additionally
records this inventoried byte-gated dispatch entry; it still grants preparation
only. Original-only commands and sealed original/v1 graphs remain unchanged.
No TTS, mix, render, SQLite graph mutation or GUI activation occurs in this
command. Those cumulative execution/publication/undo/export steps remain gates.

### Current cue TTS execution

`review-synthesize-effective-cue` additionally requires an inventoried closed
`processing-cue-synthesis-v1.json` capability and a native independently hashed
TTS recipe. `native-processing-cue-synthesis-v1.json` records this separate
admission; the original model root remains pinned and model weights are verified
by the owned read-only producer before loading. Missing capability or recipe
drift refuses. Existing preparation-only releases keep their original meaning.

`native-effective-cue-result-v1.json` records a private selected cue PCM and
accepted TTS/spoken metadata. Native code reopens the canonical output-volume
locator, request, source mapping, exact confidence bits and recorded spoken
rewrite evidence, then fences the processing release and current durable token.
The result grants no current mix/render, active graph, CAS/undo or GUI authority.
All current cumulative/export/installed/long-form/crash gates remain mandatory.

### Current-assignment private mix

`review-mix-effective-cue` requires the separate inventoried
`processing-effective-mix-v1.json` capability before inference. Its owned
isolated adapter consumes `native-effective-mix-context-v1.json`, reopens the
current bindings/projection and accepted TTS/spoken evidence, and streams the
existing protected AUD-0 producer without speech inference or SQLite writes.

`effective-mix-candidate-v1.json` binds the exact current token/request/context.
Native validation independently preserves every unselected cue/document and
checks source/producer, confidence bits, failure/duck-window/chunk evidence.
Only after full physical verification are three PCM roles retained and exactly
their paths retargeted. `native-processing-effective-mix-v1.json` and
`native-effective-mix-result-v1.json` keep this private processing/retention
evidence separate from current render/publication/CAS/undo/export authority.
Original-only commands, sealed graphs and all producer/model pins remain valid
under their existing meanings. Native/installed/long-form acceptance is pending.

### Cumulative current timeline and private render preparation

`review-prepare-effective-timeline` is a distinct B1/B2 metadata command. Native
admission separately opens the original revision-zero archive and the current
graph, then preserves the complete current token without normalizing a restored
public revision. Native and owned Python independently reconstruct current
translations against the original timeline before applying one new edit.
Unwarned cues, source clocks/ranges and IEEE confidence bits remain unchanged;
voice-only changes retain the exact current timeline bytes.

`native-effective-timeline-context-v1.json`, `prepared-effective-timeline-v1.json`,
`native-processing-effective-timeline-v1.json` and
`native-effective-timeline-result-v1.json` describe private metadata evidence.
The separate inventoried `processing-effective-timeline-v1.json` capability
grants neither model/media execution nor graph publication.

`native-effective-render-context-v1.json` requires current caption pins in
addition to the original manifest/archive and current timeline/token. The owned
current render adapter independently verifies complete SRT/ASS events, preserves
exact current caption bytes on a voice-only edit and reopens all input pins after
render/QC. A separate `processing-effective-render-v1.json` capability and
`native-processing-effective-render-v1.json` receipt bind twelve ordered source
and executable fingerprints to `review-private-effective-render-v1`; original
and current tags/checkpoints are not interchangeable.

`review-render-effective-b1` and `review-render-effective-b2` dispatch the
distinct current worker after processing capability, current-token and output
volume admission. B1 renders current captions with the archived source audio;
B2 runs only the selected cue through the admitted current TTS/mix path first.
Native code independently checks complete current caption events, source
geometry/exact rational FPS, AAC and duration within one source frame. Five
physical output roles are retained under the existing aggregate quota/reserve
and canonical volume locators, then independently reopened after dropping the
writer lease. Playback paths come only from that reopened retained final video.

`native-effective-render-retained-v1.json` binds current token/context, the
source-bound processing recipe, candidate provenance and five locators.
`native-effective-render-private-v1.json` and the B1/B2 result schemas describe
private retained evidence; `cumulative_publication_prepared` remains false.
Native execution/hosted tests and cumulative publication/CAS/undo remain
unqualified/unimplemented. Tiny owned-media B1 and mocked B2 tests cannot qualify
current native/installed B1/B2, corrected standard/editable exports, chunked
long-form behavior, GUI/liveness/crash recovery, real four-hour or full172/175
acceptance. Existing original guards and producer/model/worker pins are intact.

### Cumulative per-cue speech-reference layer in progress

`native-speech-inventory-v2.json` preserves every current cue's TTS/spoken
references and original cue order while replacing only the selected cue row.
Already admitted legacy publication-v2 acceptance stays explicitly tagged
`legacy_publication_v2`; it is not relabeled as a new current-generation proof.
Unmodified original rows have no generation proof. A changed row uses a sealed
`native-effective-speech-generation-v1.json` proof with the complete current
token, current before snapshot/view, next proposal, bound request, baseline
composition, source-bound processing receipt and physically admitted cue.

The native factory reopens accepted cue PCM/TTS/spoken metadata and the retained
three-role mix, checks complete replacement composition against the native
current assignment, then seals the private inventory. All unselected current
rows and references stay exact, including an earlier voice/translation change.
An unrelated assignment change, missing/duplicate/reordered cue, foreign pin or
unknown proof tag refuses. Legacy v1 inventory/source objects remain unchanged.

This layer is source in progress and is not wired to publication-v3/CAS/undo.
Its new native tests are authored but unrun. A private inventory is not durable
graph authority or product qualification; current native/installed/cumulative/
after-undo, export, chunked/crash/GUI/long-form and full172/174/175 gates remain.

The pending native speech generation proof also references the complete
`native-generation-state-reference-v1.json` before state. Its actual decimal
public counter, snapshot/graph byte hashes, original job binding and undo
depth/target are sealed without revision normalization. Capturing a reference
requires a native effective state and matching live supervisor state before
and after sealing. References are reopened from the existing accepted store;
their parser grants neither physical graph admission nor CAS authority.

The complete referenced state must also match its actual immutable supervisor
SQLite version. The read-only history API rechecks original binding, hashes,
payload and undo ancestry, rejects a future or substituted token, and never
initializes/migrates the feature or changes head/rows/undo. Two native history
store tests are authored for commit/undo/read-only reopen and corrupted records.

A separate historical reader feeds that referenced state through the same
physical original/legacy publication reader used by the live current wrapper.
The live wrapper retains both head fences; history uses its real prior counter
without pretending to be the current head. Recovered effective metadata must
match the sealed native view exactly. One caller-owned recovery deadline covers
all nested legacy media work; proof recursion refuses cycles, depth above256,
more than4096 proof reads, panic or late completion. Repeated references are
read again rather than accepted from an admission cache.

The four new native reference/ancestry/view/budget tests are authored, not run.
Publication-v3 graph dispatch, full speech-proof recovery, CAS/undo and actual
product acceptance remain required; these references are a recovery interface.

Pending effective speech recovery now shares the current preparation request
binder, including independent canonical input/confidence-bit checks. It binds
the selected proof to the exact stored and physically admitted before-state,
proposal, effective baseline, processing synthesis recipe/catalog and retained
cue/TTS/spoken bytes. The complete inventory must equal the native one-row
replacement, preserving every other prior TTS/spoken/proof reference.

Current mix execution privately retains its original accepted worker packet
for future v3 recovery. Existing command packet schemas and meanings are
unchanged. Recovery rebinds the full native preparation/source/current context,
processing capability and packet checksums; it reuses the independent existing
segment/failure/duck-window/chunk/provenance oracle and reopens all three native
PCM roles under the fixed output volume/quota/reserve/path-only-retarget fences.
No producer, DSP, TTS/model inference or state mutation runs during recovery.
These source interfaces are not yet connected to a complete v3 record or CAS.
### Cumulative publication v3 (pending native qualification)

`native-publication-v3.json` is a separate closed tag; v1/v2 keep their original
meanings. Its before-state reference must match an actual immutable supervisor
revision and recover through the same physical native reader, including undo
states. The original archive token remains separate from the actual public token.
The complete walk shares one deadline, cycle/depth/read bounds and no admission
cache. Render context is derived from native current/original content and exact
processing receipts before all five roles are reopened. B2 also rechecks the
selected generation, all prior speech assignments, original mix packet and all
three retained PCM roles. B1 requires every speech/mix field to be null.

The opt-in `review-commit-effective-b1` and `review-commit-effective-b2` commands
prepare this record through the native reader and use the existing supervisor CAS
and monotonic undo. Existing private commands remain private. Desktop readiness
stays false. This source is pending compilation/actual qualification; it does not
prove cumulative editing, installed GUI, crash recovery or long-form acceptance.

The active response recognizes an admitted v3 graph. Render recovery rechecks its
live processing recipe after all five roles, in addition to the sealed metadata
hash. Three pending native metadata/SQLite tests cover reference recovery after
undo, forged views, changed original artifact bytes and refusal of a v3 receipt
without processing/physical render. These tests are authored but not yet run.

### Current corrected export input (pending native qualification)

`review-prepare-active-export` takes the complete current token, including the
original manifest hash, and opens the supervisor store read-only. The closed
`native-current-export-input-v1.json` packet describes the actually active
corrected graph; original/restored-original exports continue through their
existing route. Full native v1/v2/v3 physical admission and selection share one
720s recovery walk and before/after head fences. No original-only guard changes.

All five retained roles reopen through canonical output-volume locators under
the existing32GiB aggregate/2GiB reserve policy. Physical timeline bytes equal
the admitted current cumulative timeline. Original source remains bound to the
archived manifest and render context. B2 reopens all three retained PCM roles
and selects the current final mix; B1 requires no mix. Paths are obtained from
native reopened objects; mutable hints are never used for file access.

The private Rust constructor grants no decoder/worker/browser authority. The
serialized packet is an observation, not a durable input lease. The future
standard/editable publisher must re-admit the active graph, reopen and copy/hash
all assets, validate its complete portable pack and fence the full current head
again before publication. This command copies no assets, changes no SQLite state
and sets export publication/readiness false. Two native refusal regressions are
authored but unrun; the selector, actual corrected export publication and full
installed/cumulative/chunked/GUI/crash/long-form acceptance remain unqualified.

The pending corrected-export copy primitive stages files within explicit
`.exports-pending-v1/<native-generation-key>` directories in the existing locked
Review media store. Inventories account pending/published/old/orphan exports
across all jobs in the same quota. Restart verifies the complete saved prefix
against the admitted source before appending; final full hashes, no-follow and
single-link checks, readonly sealing and atomic no-replace rename protect each
file. Fresh reserve checks cover all remaining assets before copy/every4MiB/
preseal. Old storage inventories fail closed on the added directory layout;
immutable historical product roots are never used to execute this new stage.

Four filesystem regression cases are authored but unrun. Copying alone does not
publish a complete export: current admission, complete portable manifest/pack
validation, final head fence, atomic directory publication and actual product
qualification still need to be connected and verified.

### Versioned native corrected standard bundle (pending qualification)

`review-publish-active-export` connects typed native current inputs to real
bounded resumable copies and a complete relative-resource manifest. Five render
roles/source and all three B2 PCM roles are copied; B1 has six resources. The
entire current token/snapshot, separate origin, mode and native publication/render
hashes bind the deterministic generation key. All resource paths are portable
basenames; snapshot bytes remain exact identity metadata, not portable IO paths.

Independent bundle validation rehashes every sealed resource, checks its exact
directory inventory, closed role/file table and snapshot/generation identity.
A second full current native admission must reproduce the same manifest before
final state/quota/reserve fences and native no-replace directory publication.
The existing job guard covers publication; SQLite opens read-only. Interrupted
staging is resumed by the same generation; fully published generations are
revalidated/reused. Original standard files and prior exports remain intact.

The explicit offline `review-validate-export-bundle` command requires the trusted
manifest hash. It verifies a moved bundle's bytes without claiming live state or
media decoding. Production editable-pack integration with PR183 is still required;
the new result keeps that flag false. This standard publication source and native
tests are pending compilation/execution, with no current installed/crash/long-form
acceptance implied. No historical driver/job/root or source pin is replayed.

Resume reservation counts only bytes still to allocate after inspecting existing
prefix sizes and reverified sealed targets. Prefix content is still compared
against the native source before append, and the aggregate includes every actual
byte. Three additional native bundle/directory regression cases are authored but
unrun; their byte fixtures make no encoded-media/product claim.

`native-processing-editable-pack-v1.json` is a separate optional processing
receipt for PR183's isolated guarded-copy worker. It reuses full processing/origin
admission and exact protected runtime/model/media pins, then verifies a closed
inventoried capability plus ten ordered dependencies. The native recipe hash
is source-bound; the execution hash additionally binds the context. It gives no
current-state, vendor or graph publication authority. Missing/changed capability
or dependency refuses; existing original/effective admission semantics stay
unchanged. Its new native regression is authored but unrun. Combined-release
admission, dispatch, explicit editable storage inventory/physical retention and
final current editable publication/actual product qualification remain required.
### Current corrected editable publication (pending native qualification)

`review-publish-active-editable-pack` takes the complete actual current token and
explicit source-matched processing selection. It requires a previously published
exact native current standard bundle, derives its sealed worker context and
private output path, and holds the existing job/output leases through copying.
The worker context's `output_volume_id` is a native OS pin, freshly compared
during guarded copying; worker output never admits native volume/current state.
Native independently checks all v2 assets/tracks/integer timeline projection,
guide/marker bytes, copied full hashes and closed single-link physical inventory.

The explicit pending/published editable layouts count with all old/undo/orphan/
other-job output bytes. Full source/current/processing re-admission precedes
readonly seal and no-replace directory publication; late failure preserves the
complete version. `native-current-editable-result-v1.json` distinguishes native
current publication from historical worker hints and keeps vendor/graph/readiness
flags false. Original export routes and immutable producer bindings remain.
Six authored native tests have not executed; Python/byte fixture evidence does
not qualify Rust, installed media, cumulative/undo, crash or long-form behavior.

### Current desktop display projection

`native-desktop-state-v1.json` is emitted by the read-only `review-desktop-state`
route. An initialized graph must pass full native physical current recovery;
errors cannot become original fallback. Only `original_only` with a null snapshot
means no graph exists. A current snapshot uses separate `schema-v3.json` with
the complete actual token and canonical decimal revision; durable snapshot-v2
bytes and hashes remain unchanged.

Tauri snapshot-v2 sessions retain native current tokens. Current sessions refuse
old revision0 audio; original-only sessions recheck native absence around audio
and snapshot delivery. Frontend current-v3 views preserve signed64 counters and
retain `correction_ready:false`; full-token editing, audio, undo and export actions
remain to integrate and qualify. The old v1 endpoint and public v1/v2 payload
contracts remain available explicitly; native failure never silently selects one.

### Current cue audio (pending native qualification)

`native-current-cue-request-v1.json` carries the full actual native token and
finding/cue identities, with no media path. `review-current-audio` readmits the
current B2 composition, selects its assigned accepted TTS document, hashes and
checks the complete single-link PCM wave and fences the actual head before
returning bounded bytes. Changed/retained documents require readonly waveforms;
only the exact accepted original TTS metadata object keeps its archived
producer's pinned-source policy. Missing current audio never falls back to the
original. Failed/unmapped audio refuses; B1 does not invent TTS.
Tauri audio-v2 binds that request to its native current session and original pin;
frontend routing/cancellation preserves decimal revisions and all hash fields.
The new Node/type checks pass; five Rust/Tauri tests remain unrun. Full native,
installed GUI and actual media qualification are still mandatory.

### Current correction proposal before confirmation

`native-current-correction-plan-v1.json` defines a separate private read-only
proposal, bound to every actual token field, finding/cue and normalized changes.
`review-current-plan` physically admits current content, calls the native mode
planner and fences the head. B1 excludes TTS/mix and rejects voice assignment;
B2 distinguishes translation and voice-only scope. ASR/OCR and valid output stay
retained. Candidate revision/hash identify logical metadata, not produced media.

The result's read_only is true and processing_release_admitted,
active_graph_published and execution_ready are false. No inference, processing
release admission or durable write is implied. Tauri/session/client checks retain
the complete decimal token and cancel stale requests. Current desktop drafts can
request this check; actual confirmation/execution/CAS remains separately gated.
Three new native/Tauri tests are authored/unrun; Node/type checks pass. Full
native, installed GUI, media, crash and long-form acceptance remains mandatory.

### Explicit current confirmation

`native-current-confirmation-v1.json` wraps the complete read-only native plan
with an explicit true confirmation. `review-confirm-current` re-derives that plan
and requires exact canonical equality, then selects only its own installed
source-matched native/manifest processing root. Missing compiled source identity,
ambiguous inventory, changed executable bytes or a changing manifest refuses.
Full processing/runtime/model admission remains the commit path's responsibility.

Current B1/B2 commit commands additionally require original-manifest hash, exact
cue and candidate snapshot hash. These are checked under the existing job lock,
before worker/context mutation. Preparation commands reject confirmation fields.
No durable schema or original job pin changes. Four new native tests remain
authored/unrun; source/format checks do not replace exact-head CI, actual native
execution, desktop apply/undo/export or full product acceptance.

### Current desktop apply result

`desktop-current-apply-result-v1.json` contains the actual before token, a new
host session ID and a fresh schema-v3 current snapshot after native CAS and full
physical readmission. The host consumes its exact saved plan once; browser paths
or hash claims cannot substitute for it. Native confirmation still re-derives
the plan and performs full processing/result admission before CAS.

The final snapshot hash may differ from the logical candidate because native
retained preview paths are included only after result validation. Revisions
remain exact decimal strings. Client/session rotation requires exact source,
cue/plan and immutable-field preservation. Cancellation or an uncertain reply
invalidates the session and requires discovery; it never auto-retries. Current
undo/export/initialization and installed/crash/long-form acceptance remain open.

### Explicit desktop current undo

`desktop-current-undo-v1.json` closes the confirmation request over all five
actual current token fields, with no caller-selected target, path or graph. The
host requires the exact native current session, consumes every older session and
saved plan for that job and shares the existing per-job apply guard. A fresh
native current read must equal the bound token before calling the existing
`review-undo-active`. That command physically admits current and prior graphs and
performs its existing monotonic CAS under the job execution guard; no worker,
producer, model, SQLite schema or original version pin is changed.

The acknowledgement must match a newly physically admitted schema3 snapshot,
including exact decimal next revision, source-manifest/hash identity and all
snapshot metadata. Source findings, cue IDs, integer ranges, confidence and
warnings remain exact; only prior translation, voice, preview and last-plan
content may be restored. Prior graph content may be reused, but the public token
counter never moves backwards. Only this checked result grants a replacement
native session. Cancellation, stale input or an uncertain outcome requires fresh
discovery and cannot automatically resend undo or revive an old plan.

The UI asks for an explicit second confirmation. A positive revision alone is
not proof of remaining undo history; native refusal is authoritative and does
not manufacture an undo target. Source/client tests are not physical native,
installed GUI, crash, cumulative/after-undo or long-form qualification. All
standard/editable export and full172/174/175 acceptance gates remain required.

### Confirmed current desktop standard and editable export

`desktop-current-export-v1.json` closes the request over explicit confirmation,
format and all five actual current token fields. No destination, processing root,
worker entry or vendor target is accepted. `review-confirm-current-export` binds
the request to the durable current token and dispatches the existing native
publisher. Standard publication selects no processing release. Editable
publication uses only the executable's existing compiled-source/installed-layout/
manifest/native-byte selector and the full processing admission before the
existing editable publisher; it still requires the exact current standard bundle.

Tauri consumes old job sessions/plans and shares the apply/undo guard. Before and
after publication, full physical current reads and original pin checks must agree
without advancing the graph/token. Native publication flags, format, mode-derived
file count and manifest identities must match before a replacement session and
small display receipt are returned. A display path never becomes browser IO,
media playback or automatic vendor launch authority. Editable receipts retain
the exact standard-bundle manifest binding; its failure cannot change a prior
standard result. Cancellation or uncertainty requires actual reload, not retry.

UI confirmation and receipts are separate for the two formats. The client first
validates the current standard receipt, then permits an editable request bound
to it. Editing/undo clears those in-memory bindings; published files remain.
The source is pending and new Rust tests are unrun. Actual installed/current
B1B2/physical publication/vendor/timing/GUI/crash/long-form acceptance is separate.

### Explicit original-only desktop initialization

`desktop-current-initialize-v1.json` accepts only explicit confirmation and the
bound original job ID. The session must come from native-current-absent discovery;
legacy or already-current sessions cannot initialize. The host stores the exact
bounded original view alongside its native export pin. It consumes all older job
sessions/plans under the same apply/undo/export guard and rechecks native absence
and the original pin before using the existing `review-original
--initialize-original` command with owned roots.

No original reader/seed guard, producer/model/runtime/worker format or durable
schema is changed. Native metadata admission and its job guard still own the
initial optional history write. The acknowledgement and a fresh native current
read must preserve the whole displayed original snapshot at revision0, including
null preview/confidence/warnings, before the replacement full-token session is
admitted. This does not authorize physical media reuse or inference. A late,
cancelled, changed or uncertain result requires actual discovery without retry.
UI confirmation is separate from first correction confirmation; actions refresh
from the returned current state. Source/metadata tests do not qualify actual
installed initialization, subsequent correction, crash or long-form acceptance.


`desktop-request-activity-v1.json` is the closed, memory-only host request
observation. It is not a worker heartbeat, QC result, publication receipt or
current token. Request/job/operation mismatch, backwards elapsed time or invalid
field/phase relations refuses the observation only. Missing/stale observation
never authorizes a retry or reclassifies the durable operation as stopped.


`native-stage-observation-v1.json` defines opt-in native stderr records and the
separate closed desktop stage envelope. The existing host-only activity v1 is
unchanged. Progress records cannot change durable state, inputs, pins or results.
The writer is bounded/nonblocking; the reader separates valid records from the
unchanged ordinary diagnostic cap and stdout. Sequence/epoch/count regressions
invalidate observation only. Entering a native stage or retaining a live process
is not worker/chunk/QC advancement or publication success.


`desktop-request-failure-v1.json` adds optional bounded errors for actual bound
mutation requests. Exact request/job/operation and UTF8 byte caps are enforced;
legacy/pre-binding failures may remain strings. Outcome is always unconfirmed,
never a claim about whether a possible CAS committed. No retry is authorized.
Only actual nonzero-exit ordinary stderr is attached; missing diagnostics stay
null. Existing success envelopes, host activity v1 and native-stage observation
contracts remain unchanged. Inert display details do not enter artifact authority.

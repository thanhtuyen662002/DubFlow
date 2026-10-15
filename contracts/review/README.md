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

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

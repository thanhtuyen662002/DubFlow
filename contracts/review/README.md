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

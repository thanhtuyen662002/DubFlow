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

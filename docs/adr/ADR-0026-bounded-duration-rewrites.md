# ADR-0026: Bounded lexical duration rewrite and spoken-text evidence

- Status: Proposed, Issue #166 / Draft PR #195.
- B2 TTS pipeline producer: 3.5.0. Underlying VieNeu synthesis remains 3.4.0.
- Private spoken-text receipt/document: 1. Existing TTS1/2, placement1,
  checkpoint1/2, timeline, worker protocol and SQLite formats stay unchanged.

## Decision and limits

The actual pinned zh→en→vi alternatives for three refused real-video cues
included shorter strings with changed pronouns and a longer third string.
Model likelihood and character count cannot authorize semantic rewriting.

Add one offline, auditable lexical contraction: a single lowercase
`lần đầu tiên` after a temporal connective becomes `lần đầu`. Preserve every
other word, pronoun, negation, number, predicate and punctuation. Decline quotes,
control characters, multiple occurrences and capitalized interior words that
may denote names/titles. This intentionally covers a narrow first-time adverb;
it is not a general contextual translation engine or a semantic confidence
calibration. It can remove emphasis; show an explicit review advisory.

Only typed DURATION_FIT_REQUIRED permits one synthesis with changed text.
Other native/model/complete-speech failures retain their ordinary behavior.
The same approved voice/model/runtime, EOS bounds, safe tempo cap and measured
WAV validation remain in force. A second duration refusal preserves B1/source
fallback. No arbitrary truncation, new model sampling loop, forced EOS or
increase of the speed cap is allowed. The original B1 text, subtitles, canonical
cue ID and integer time points remain unchanged.

## Provenance and interruption recovery

After the outer adapter validates and atomically publishes a WAV, commit a
bounded per-cue spoken-text receipt, then its existing TTS checkpoint. The
receipt binds the complete original TtsInput, exact spoken text/hash, rule,
mode, recipe source hash, generation/checkpoint identity, artifact hash and
audio hash. A matching receipt is mandatory for reuse under the new producer,
including when original speech succeeded. Missing/corrupt/duplicate/oversized
or mismatched evidence invalidates only that cue; actual WAV validation still
occurs independently in LocalTtsAdapter. An orphan receipt without a reusable
checkpoint never authorizes reuse.

The final private document binds these per-cue decisions to the exact TTS
document hash and its voice/model/config provenance. Publish its hash/path in
audio metadata and an identical hashed editable copy. Applied rewrites emit
TTS_DURATION_REWRITE_APPLIED and remain completed-with-warnings. Unsupported
rewrites are refused normally; no numeric semantic/intelligibility score is
invented. The new document is evidence, not canonical dialogue or timing.

## Compatibility and rollback

New B2 generations pin this module's source digest along with existing recipe
inputs. The producer/fingerprint changes from 3.4.0 to 3.5.0 for the worker
pipeline; standalone unchanged VieNeu synthesis still reports 3.4.0. Old
checkpoints cannot satisfy the new identity. Old immutable job/runtime/output
bindings remain intact; new runs use fresh coherent runtime generations.
No old audio is relabelled, no durable/public schema is mutated, and no in-place
migration is required. Rollback uses the retained previous runtime/artifacts.
Consumers that do not read the new private document retain usable standard
MP4/PCM/SRT/ASS exports.

## Qualification

Deterministic evidence must cover finite changed-text retry, preserved source
text/ticks, rejection of pronoun/negation/name/number edits, independent WAV
duration/quality validation, receipt tampering and interrupted receipt versus
checkpoint publication, and unaffected neighboring cues. Actual owned-model
speech and measured fitting/fidelity evidence are separate from fixtures. All
four exact-head/current-base lanes and the complete #166/#175 gates still apply.
A narrow rewrite passing does not resolve every long cue or approve all voices.

The staged and installed Windows production smoke invokes the isolated owned
Python duration_rewrite_smoke driver. It verifies the exact source SHA and full
bundle inventory, loads two approved stock voices, measures natural original
and rewritten speech and authors one bounded control window for each. Both
must execute the changed-text fallback, validate actual PCM and reuse the
checkpoint through a fresh uninitialized engine with unchanged waveform bytes
and mtime. This is required real-backend evidence for the changed route, not
real-video timing or human fidelity. The release-bundle Integration root covers
the driver. No workflow/check names or general release gate are changed.

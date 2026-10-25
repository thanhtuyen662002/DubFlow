# ADR-0036: Bounded dubbing fit ladder and separated-bed source restoration

Status: Proposed, Issue #276. Extends ADR-0025/0026/0035.

## Decision

Keep the existing natural/atempo <=1.3 route, 2000 ms postroll and 120 ms
next-cue guard as the first attempt. Only DURATION_FIT_REQUIRED advances one
cue through these finite steps: widen postroll to at most 4000 ms, condense
Vietnamese once, then allow <=1.5 tempo. Windows remain bounded by media end
and the next source cue minus guard; overlap on either side forbids extension.
Tempo correction retains the existing three measured passes, complete-speech
checks and refusal to cut spoken samples. Other failures stop the ladder.
VieNeu reuses its natural PCM for window/tempo changes; condensation permits
at most one additional text inference (with the existing bounded EOS policy).

Condensation applies an ordered lexical rule set to fillers, redundant
connectives/adverbs and final particles. Quoted/control-containing text and
potential capitalized names/titles are declined. Numeric classifiers and
locative/demonstrative complements are protected; names, numbers and negation
are never rewritten. Original translation/subtitles and canonical source ticks
remain authoritative. These rules can remove emphasis: the warning names all
applied rules and asks for spoken-text review, without claiming calibrated
semantic fidelity. No new model or dependency is introduced.

The engine boundary carries an optional selected render limit internally.
The outer adapter checks it against independently allocated fallback limits,
checks neighboring intervals, validates PCM, and records the selected limit in
the existing TTS2 artifact. Fast fitting requires explicit adapter opt-in and
a cue/speed advisory, and cannot exceed 1500 milli. The default config remains
1300 to preserve successful first-route request/artifact identities and bytes.
Widening, rewrite and fast-fit warnings plus selected speed/window are bound
to the spoken-text receipt and checkpoint. Recovery revalidates these limits
and waveform bytes, including when no new inference runs.

When accompaniment is selected, build a separate `mix_bed.wav` from that stem
with source PCM in failed canonical slots. Union overlapping failed windows
before applying 20 ms linear crossfades inside their edges. Process bounded
PCM blocks and publish atomically. Never add source over accompaniment: replace
samples, preserving source music gain. Keep `background_stem.wav` pure and keep
the original source separately. A TTS file rejected at mix time permits one
bed rebuild for the newly observed failures. Emit
`SEPARATION_SOURCE_RESTORED: <cue>` for every restored slot. All-TTS failure
continues to the existing B1/source export fallback. A wholly refused voice
group remains a per-cue failure document so other voice groups can still dub.
Job manifest/QC warnings retain the fit and restoration advisories.

## Identity and compatibility

No public contract, timeline, worker protocol or SQLite migration is required.
TTS1/2 and mix1 serialization remain compatible. The private spoken receipt
recipe changes and binds selected fit evidence; old receipts cannot authorize
reuse under it. Generation/checkpoint/cache identities pin the updated parent,
native, rewrite and B2 allocator sources; mix recipe identity also pins the bed
builder. Affected render limits participate in artifact hashes; changed
text/config/window participate in synthesis-cache keys. Healthy first-route
requests retain their previous config, placement input, producer and artifact
identity. Existing generations may be recomputed in new private generations;
published exports and pinned old runtimes are never relabelled or migrated.

The native recipe now declares a 1.3 primary/1.5 fallback limit. The exact
previous data-only model manifest recipe remains accepted so its model/voice
hashes do not change; both parent and child code hashes distinguish the new
capability. No other inference/manifest differences are accepted. Rollback
uses the retained previous runtime and outputs.

## Evidence limits

Fake-engine tests cover each rung, protected text, strict window/rate bounds,
unchanged successful identities, receipt validation, resume and source-restored
PCM/fades across block boundaries. These tests do not measure real-model
intelligibility or the reported 45/124 undubbed rate. A before/after count for
the original 246 s video requires that authorized source and its installed
models; it remains NOT_RUN without those inputs. No models/media are downloaded
or committed for deterministic qualification.

## B2 phrase grouping (Issue #276)

The owner measured 29/124 undubbed cues after the per-cue ladder on the 246 s
Douyin source, down from 45/124. Of those failures, 21 overlap a neighbour;
their median source slot is 1.0 s and the Vietnamese requires a median
6.1 syllables/s. Extending individual windows cannot share spare time inside
contiguous dialogue. These measurements are owner-provided, not a new local run.

Before B2 TTS, sort cues by integer start/end and cue ID and greedily join
consecutive cues when their gap from the previous cue's end is strictly below
250 ms (overlap counts as zero). Require the same resolved voice and detected
speaker, at most five cues, a source span at most 8000 ms and joined text at most
512 characters. Group before partitioning by voice so an intervening speaker
cannot disappear from the adjacency test. Production passes speaker labels
separately from casting: two speakers assigned the same voice still never join.
Without detected speakers, the selected single voice is the grouping boundary.

Join Vietnamese texts in source order with `, `, or one space when the previous
text ends in `.`, `!`, `?` or `…`. A singleton retains its existing cue ID,
text, confidence, source interval, TTS configuration and window calculation.
A grouped segment uses `phrase-<first cue ID>` (a deterministic bounded digest
when needed to avoid a source-ID collision), minimum member confidence, first
start and last member end. Nested overlaps retain their full source coverage
for overlap detection, so they cannot create a false postroll gap. Apply the
entire existing finite fit ladder to the phrase, allocating normal/widened
postroll against the next phrase and media end. Source restoration covers the
failed phrase's entire span, including internal gaps, with the existing fades.

Speech within a phrase is no longer aligned to individual subtitle lines.
Its internal drift is bounded by the phrase's source span (at most 8 s for
grouped cues); the existing bounded postroll can extend its tail. This trades
line-level synchronization for natural continuous speech and sharing short
neighbours' spare time. It does not claim lip sync or real-model intelligibility.
Subtitles, translation.json, review overrides, canonical cue identities/ticks
and B1 remain per cue. Editing one cue updates its phrase text; the existing
text/window/voice/config-keyed synthesis cache skips unchanged phrase requests.
An edit that crosses the text bound or a recast may also change adjacent groups.

### Phrase evidence and compatibility

Keep public TTS1/2, mix1 and checkpoint schemas unchanged. The B2 file at
`tts_document.json` (and each voice-specific variant) is now a private
`dubbing_tts_document` v1 envelope containing the strict public `tts_document`,
the grouping recipe and `member_cue_ids`, a segment-ID-to-ordered-cue-list map.
Map every segment, including singletons and failures. The private spoken-text
document repeats this map and binds the exact envelope bytes by hash; individual
spoken receipts and checkpoints continue to validate accepted phrase PCM.
Read-only spoken-evidence validation requires the independently expected map
when reading an envelope. Placement windows record the same ordered members;
the placement input hash also binds the complete grouping inputs and recipe.

B2 returns membership to the production worker. Manifest/QC failure totals
count original member cues, and the stage checkpoint lists all voice documents.
Desktop review reads those documents plus mix failures, expands phrase IDs,
and sets `dub_failed` on every member. Legacy flat documents without member
lists still treat the segment ID as the cue ID. Resolve Windows verbatim and
ordinary paths before checking that review evidence stays within the work cache.
If every phrase fails TTS, retain the failure documents/counts in the review
checkpoint while exporting the existing B1/source fallback.

The new phrase/window recipe and pinned B2/receipt-reader source hashes
invalidate old private generations, checkpoints and synthesis-cache namespaces.
Retain the singleton/native producer and request fields; no model/voice bytes
or inference bounds change. Old published artifacts and pinned runtimes remain
immutable. Readers of new B2 files must unwrap the private envelope before
validating public TTS; rollback uses the retained previous runtime and outputs.
No in-place migration, dependency, public contract or packaging change is needed.

Deterministic fake-engine tests cover gap/overlap/span/count/text/speaker bounds,
punctuation, singleton inputs and identity, member evidence and tampering,
phrase-level fitting, receipt/checkpoint recovery, restoration over internal
gaps, cue failure totals, review mapping and one edited phrase's cache miss.
No real-video rerun or model download is part of this local change.

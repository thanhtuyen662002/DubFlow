# Portable CapCut import pack v1/v2

### Corrected Review bundle adapter (pending full integration)

The additive `create_pack_from_review_bundle` production entry takes an explicit
trusted Review standard-bundle manifest hash and complete expected public token.
It verifies closed B1/B2 resources, sealed no-follow/single-link input bytes and
the exact snapshot/generation binding, then uses the unchanged v2 assembler.
Raw/localized video, SRT/ASS, canonical timeline, QC, Review identity metadata and
all available B2 original/dialogue/final-mix PCM files remain separate copied
resources with default tracks. B1 invents no audio resources. Both source bundle
and pack can move without rewriting references; wide public counters and source
clock/text/confidence bytes stay exact in the copied metadata.

This adapter does not admit live Review state or select/launch CapCut. Its
returned binding explicitly says current generation was not checked. Native
source-matched processing/final-token publication and installed actual-product
qualification remain required. Existing pack schemas/copy recipes and legacy
job-manifest input are unchanged. Five new Windows byte-fixture tests pass;
they are component evidence, not current whole-product or vendor acceptance.

The additive isolated `review_editable_pack.py` worker uses the closed
`review-worker-context-v1.json` and returns `review-worker-result-v1.json`.
App-owned Python flags/native byte gate/context hash and explicit source recipe
are required. Candidate paths derive from the native job/generation storage
namespace. Optional copy guards account all old/pending/orphan bytes and fresh
caller-available reserve before writes/each4MiB/pre-promotion; default legacy
behavior and public pack schemas remain unchanged. Complete candidate reuse
rehashes and binds all resources without rewriting. Eight focused Windows
Python cases pass; native volume/writer/current admission and final editable
retention/publication remain required. The worker has no vendor/SQLite/model or
live-current authority and is not invoked against historical product roots.

The import pack is a portable folder with a manifest and copied canonical
editable assets. Paths in the manifest are always relative to the pack root;
the pack can be moved to another directory without rewriting the manifest.
Track order is explicit: video, original or dubbed audio, then subtitles. Every
v2 pack also contains a required `timeline` object with an exact rational tick
time base, optional duration, and cue mappings. All wide integer fields use
canonical decimal strings. The mapping stays portable when the folder is
moved; it never uses frame indices or floating-point seconds as durable
identity.

Released v1 packs have no required timeline. Their schema is preserved and they
remain readable without rewriting media or inventing timing. A caller without
a canonical mapping produces v1; a caller with a mapping produces v2. Readers
reject unknown future versions explicitly. See ADR-0029 for bounds, rational
rescaling, outward rounding of millisecond aliases and migration policy.

Production v2 assembly copies raw/localized video, both SRT/ASS, the complete
canonical timeline and available original/dialogue/final-mix stems. Resource
paths are relative and every copy is pinned to its canonical content hash.
Tracks include `role`, `enabled_by_default` and (for video)
`mute_embedded_audio`: a final mix is active while its component stems and the
embedded source audio are disabled; only one subtitle alternative is active.
The original full timeline remains a separately hashed resource, retaining
text, confidence and native source clock mapping without guessing a new clock.

Large copies use private content-bound checkpoints and bounded buffers. A
restart verifies retained prefixes before continuing; interrupted/unrecognized
work is never advertised as a completed pack or used to replace canonical
output. A completed pack is independently validated before atomic promotion.

The adapter does not inspect or write CapCut's private project database.
New packs also include bounded UTF-8 `HUONG_DAN.txt` instructions derived from
their normalized default tracks and relative file names. They identify media
intake, separate caption import, the selected video/mix and embedded-audio mute
policy. The text is ancillary guidance; it is not a vendor project format or a
supported-build declaration. Resource hashes and timeline identity remain in
the unchanged manifest. Released packs need not contain this file.

Its exact text hash is included in the new private copy recipe
`portable-import-pack-copy-v2-guide1`. A different guide/request gets a separate
private checkpoint; old interrupted copies and already published packs are
preserved, not rewritten or silently adopted. This changes no public pack
schema, SQLite state, original media or pinned job/runtime.

The direct-draft adapter may consume this pack only for an explicitly tested
CapCut version and a caller-selected controlled directory. An unsupported or
unavailable CapCut version always returns this pack as the actionable fallback;
it never invalidates the canonical MP4 or this stable handoff.
Private Review copying also requires the native-pinned `output_volume_id` in
`review-worker-context-v1.json`. A fresh OS GUID/fixed writable NTFS check runs
with storage guards; a changed/missing pin refuses without granting worker
volume/current authority. POSIX device IDs support byte fixtures only. This
unpublished private context addition leaves the stable v1/v2 pack formats and
existing producer bindings unchanged. Native validation/publication and current
installed acceptance remain separate requirements.

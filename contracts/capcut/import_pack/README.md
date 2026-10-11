# Portable CapCut import pack v1/v2

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

The adapter does not inspect or write CapCut's private project database.
The direct-draft adapter may consume this pack only for an explicitly tested
CapCut version and a caller-selected controlled directory. An unsupported or
unavailable CapCut version always returns this pack as the actionable fallback;
it never invalidates the canonical MP4 or this stable handoff.

# CapCut import pack v1

The import pack is a portable folder with a manifest and copied canonical
editable assets. Paths in the manifest are always relative to the pack root;
the pack can be moved to another directory without rewriting the manifest.
Track order is explicit: video, original or dubbed audio, then subtitles. Every
pack also contains a `timeline` object with an integer tick time base, optional
duration, and cue mappings. The mapping stays portable when the folder is
moved; it never uses frame indices or floating-point seconds as durable
identity.

The adapter does not inspect or write CapCut's private project database. A
The direct-draft adapter may consume this pack only for an explicitly tested
CapCut version and a caller-selected controlled directory. An unsupported or
unavailable CapCut version always returns this pack as the actionable fallback;
it never invalidates the canonical MP4 or this stable handoff.

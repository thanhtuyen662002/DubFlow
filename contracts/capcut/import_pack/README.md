# CapCut import pack v1

The import pack is a portable folder with a manifest and copied canonical
editable assets. Paths in the manifest are always relative to the pack root;
the pack can be moved to another directory without rewriting the manifest.
Track order is explicit: video, original or dubbed audio, then subtitles.

The adapter does not inspect or write CapCut's private project database. A
direct-draft adapter may consume this pack later, but an unsupported CapCut
version never invalidates the canonical MP4 or this stable handoff.

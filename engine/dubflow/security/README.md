# Python security boundary

The Python adapter mirrors the Rust policy for path validation, collision-safe
filenames, archive extraction planning, argv construction, diagnostic
redaction, and streaming SHA-256 verification. It never invokes a shell and
does not perform archive writes itself.

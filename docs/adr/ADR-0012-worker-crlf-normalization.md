# ADR-0012 — Normalize CRLF at the worker JSONL boundary

Status: Accepted

## Context

Windows text fixtures and native worker processes conventionally terminate
JSONL records with `CRLF` (`\\r\\n`).  The worker protocol parser previously
removed only `LF` and then rejected the remaining `CR`, so the same valid
fixture passed on Linux and failed during Windows root test discovery.  A bare
or embedded carriage return is still malformed input and must remain rejected.

## Decision

`Envelope.from_line` strips exactly one `CRLF` terminator, or one `LF`
terminator, before validating the JSON object.  Any remaining `CR`, `LF`,
duplicate key, unknown field, invalid UTF-8 or oversized line remains an error.
The protocol schema and version do not change; this is transport newline
normalization at the boundary.

## Consequences

- Windows and Unix workers consume the same JSONL contract.
- Bare/embedded carriage returns cannot be used to smuggle a second record.
- Existing producers that emit `LF` remain byte-for-byte compatible.
- The fixture test explicitly covers both CRLF acceptance and bare-CR rejection.

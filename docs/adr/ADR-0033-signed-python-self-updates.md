# ADR-0033: Signed Python update discovery and retained-version activation

## Status

Accepted — 2026-10-25

## Context

The versioned Windows install retains complete application/runtime directories.
The supervisor needs a Python adapter to discover signed installer updates and
reactivate a retained version without modifying its artifacts.

## Decision

Update manifest schema 1 signs the UTF-8 canonical JSON object excluding its
`signature` member, using sorted keys, compact separators and no trailing
newline. Ed25519 key `dubflow-update-v1` authenticates the version, channel,
UTC publication time, notes and exact GitHub setup URL, size and SHA-256.
Unknown fields, unsupported keys and malformed metadata fail closed. This
manifest is separate from the existing release bundle manifest and its signing
format; neither signature format substitutes for the other.

The GitHub adapter chooses the highest newer eligible release, verifies its
manifest and streams its installer into a partial file. Only matching size and
SHA-256 permit atomic publication. Ordinary PR tests use generated test keys
and fake transports, never production signing credentials or live GitHub.

Installation and explicit rollback share activation writers. A retained version
must pass full bundle integrity/signature verification and qualification-status
parsing before activation changes any pointer, state or launcher. Each output
uses its existing atomic file writer; the three files are not a filesystem-wide
transaction. Version listing is inventory, not an integrity attestation.

## Compatibility and migration

This is additive: `current.json`, `install-state.json`, the launcher and release
manifest retain their current formats. No SQLite or job migration is needed.
Existing version directories remain usable and are never deleted by this
adapter. Existing jobs retain their pinned producer/runtime/model versions.
The supervisor still owns safe-checkpoint scheduling, process shutdown,
healthchecks and the decision to request rollback. Python discovery/download
does not switch a running application or execute downloaded installers.

Future update-schema or trust-key changes require explicit compatibility
planning. Schema 1 consumers reject unsupported schemas instead of silently
accepting an unsigned or incompatible fallback.

# ADR-0008: Single-writer project ownership lock

## Status

Accepted — 2026-09-29

## Context

SQLite's single-writer behavior does not protect the filesystem artifact and
temp namespace when two app instances, an updater restart, or a stale process
race. A worker must never become a second owner of a project.

## Decision

The supervisor acquires one OS-visible create-new lock covering the project
database, artifact namespace, and temp namespace. Metadata records schema,
supervisor role, PID, process-start token, scope, and acquisition time. A
second instance receives an explicit failure, read-only result, or handoff
request; it never silently becomes writable.

Stale recovery is proof-based. The caller injects a platform process probe that
checks both PID and process-start token. Only when the recorded owner is not
live may the old record be renamed to a unique `.stale-*` file before a new
create-new attempt. Corrupt metadata is an error and is never overwritten by
guesswork. Release is token-checked so a handle cannot delete a newer owner's
lock. A hard-killed process therefore leaves inspectable recovery metadata.

The artifact-store crate re-exports the supervisor primitive rather than
inventing a second lock. Worker roles are rejected by the API. The watchdog,
desktop, and workers consume status/IPC interfaces; only the supervisor owns
the lock and durable state.

## Compatibility and migration

This is an additive filesystem protocol and does not change SQLite migrations.
Existing projects acquire the lock before writable DB open. A future lock
metadata change requires a schema version and a compatibility reader; stale
records are retained for diagnostics and can be cleaned by a later safe pass.

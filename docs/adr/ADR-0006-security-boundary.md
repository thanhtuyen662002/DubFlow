# ADR-0006: Untrusted-input and package security boundary

## Status

Accepted — 2026-09-29

## Context

Downloaded media, subtitle names, archive members, model packages, browser
diagnostics, and FFmpeg arguments are untrusted. A path or shell mistake can
escape an owned root, execute a command, leak a session, or activate an
unapproved code-bearing package. Security behavior must remain deterministic
and testable without live sites or real model downloads.

## Decision

DubFlow validates all external path-like values before I/O. Relative paths are
normalized for both Windows separator styles and reject absolute/UNC/drive
prefixes, parent traversal, empty/dot components, controls, trailing dot or
space, colon, reserved device names, and overlong UTF-16 names. Untrusted
display names are sanitized and allocated case-insensitively with collision
suffixes.

Archive adapters first build a plan under an app-owned staging root. Zip Slip,
duplicate case-insensitive targets, symlink/hardlink members, and member or
total-size limits are rejected. The extraction implementation must use this
plan and a no-follow file creation primitive.

Process adapters pass an executable and argv vector directly to the process
API with shell execution disabled. Diagnostic maps and text redact cookies,
authorization, tokens, API keys, secrets, passwords, and sessions before
persistence or upload.

Runtime/model manifests classify packages as `weights-only` or
`code-bearing`. One-click defaults require redistribution eligibility and no
manual credentials/click-through. Code-bearing defaults additionally require
an approved signature and audited approval identifier, with hash, size,
compatibility, fallback, and rollback metadata retained.

## Consequences

The security crate and Python adapter are pure policy modules and can be tested
with synthetic inputs. They do not silently claim that a file is safe merely
because it exists, and they do not require a system Python, FFmpeg, live site,
or model download. Actual archive writers and process launchers remain
adapters that must consume the validated plans.

## Compatibility and migration

This is an additive boundary; existing artifacts and supervisor state are not
rewritten. New adapters adopt the validators at their input edge. Any future
policy relaxation requires a versioned ADR and fixtures for the exact edge
case rather than weakening a shared helper in place.

# ADR-0044: Optional source credit for finished jobs

- Status: Proposed
- Issue: #373
- Scope: desktop host and optional per-output user metadata.

## Decision

Store `source_credit.json` beside the finished video. Version 1 contains only
`schema_version: 1`, `creator`, `platform`, `url`, `rights` and `note`. Rights are
the user's declaration: `own`, `permission`, `cc` or `unknown`. Attribution alone
does not establish permission. Text limits are Unicode character counts: creator
120, platform 40, URL 500 and note 500. Reject control characters and nonempty
URLs without an HTTP(S) scheme and host. The Rust host validates reads and writes
and resolves output directories through the existing `review_dir` boundary.

Only the host writes this file. Serialize saves, flush and close
`source_credit.json.partial`, then rename it over the final file. An interrupted
regular partial file can be replaced on the next explicit save. Invalid final
files produce an error instead of silently resetting the user's declaration.

The get command returns null for a missing file; desktop consumers interpret
that as empty fields with unknown rights. This distinction lets the existing
queue `originUrl` prefill the URL and platform only when the file is absent. A
saved empty record remains empty. Prefill is a draft until the user saves it.

Posting suggestions load credit each time the dialog opens. Append attribution
to the displayed/copied description after offline or AI generation. Show a
reminder for unknown rights. Never include credit or private notes in provider
requests. Credit does not enter the rendering pipeline or establish media identity.

## Compatibility and migration

The file is optional and additive. Existing outputs need no migration and retain
unknown rights until edited. Queue snapshots, job manifests, worker protocols,
SQLite state and canonical export assets retain their existing formats. Older
apps can ignore the file. Unsupported schema versions fail explicitly and must
not be overwritten by an automatic prefill. Later versions require an explicit
compatibility plan before changing the schema.

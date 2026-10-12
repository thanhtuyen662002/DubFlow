# Versioned direct-draft boundary

Direct CapCut drafts are an optional adapter. DubFlow attempts them only for
an explicitly tested exact vendor version and writes only under a caller-provided
controlled directory. The canonical MP4 and portable import pack are produced
independently. Unknown versions, schema changes, validation failures and
CapCut update races return a structured fallback result instead of failing the
job.

Envelope v2 preserves bounded three- or four-component vendor identities. V1
remains an immutable three-component contract and is readable without rewriting
its bytes. A four-component version in a v1 envelope is invalid. New fixture
producers emit v2; rollback to a v1-only reader refuses v2 and keeps the portable
pack. Envelope validation binds the exact manifest and ordered resource list.
Readers reject duplicate JSON fields, invalid UTF-8/non-JSON constants,
nonregular or reparse files, and envelopes larger than 16 MiB. This bound covers
the schema's full 256-asset inventory with escaped paths. Refusal preserves any
previous valid draft and the portable pack; it does not qualify vendor support.

These envelopes are adapter metadata, not the vendor's native project format.
The fixture backend and schema tests do not qualify an installed CapCut version;
production requires separate controlled generated-project/open/export evidence.

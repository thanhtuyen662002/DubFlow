# Source adapter contract v1

Source adapters are provider-specific boundaries. They return stable source
identity and structured capability failures; they never write durable job state,
copy browser credentials into logs, or make a live website a required CI
dependency.

The durable deduplication key is `provider_id:source_id`. A canonical URL is
retained for display and diagnostics, but redirect variants must resolve to the
same provider/source identity. Channel enumeration returns an opaque,
checkpointable cursor so a restart can resume from the last emitted page.

Required failure classes include `AUTH_REQUIRED`, `RATE_LIMITED`,
`SOURCE_CHANGED`, `NOT_FOUND`, `PRIVATE`, `NETWORK` and `UNSUPPORTED`. Fixture
adapters implement the same interface without network access and are the only
adapters used by required deterministic CI.

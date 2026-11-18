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
adapters used by required deterministic CI. `PROXY_UNSUPPORTED` (not retryable,
action `disable_proxy`) means a configured network proxy is in use: until an
app-owned validating proxy exists, proxied source access is refused before any
request reaches the proxy, and the proxy is never bypassed (#884). The yt-dlp
helper refuses every network operation, the app-owned media transport refuses
every proxied request (including Douyin downloads from fresh page details), and
generic links are refused on the helper's own proxy test before the target is
looked up locally. `NO_PROXY` does not lift the refusal.

An enumeration page may also carry per-item `failures`. A poisoned, private or
deleted item is recorded with its source id and structured code while the
remaining page items continue. Incomplete pages must provide a cursor and
completed pages must not provide one; the supervisor rejects a cursor that
does not advance. The supervisor queue admits at most 10,000 identities per
scan and persists the cursor, item status, retry count, download byte progress
and content hash in one transaction per page.

Materializers accept only validated HTTP(S) or explicitly local candidates.
They publish through a temporary `.part` file followed by an atomic rename,
verify size/hash when supplied, and keep credentials out of URL identity,
manifest fields and diagnostics. Redirects are bounded and credential headers
are removed when a redirect crosses hosts. Provider-specific HLS/DASH streams
require an explicit muxer; a stream is never presented as a complete media
file by accident.

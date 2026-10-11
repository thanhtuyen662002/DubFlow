# ADR-0027 — Pinned provider pages for Douyin creators

Status: Proposed; implementation/qualification in #167 / PR #198.

## Problem

The reviewed `yt-dlp 2026.08.19` SDK handles Douyin video URLs but has no
creator extractor. Sending a `/user/<sec_user_id>` URL to GenericIE cannot
establish creator support or turn an inaccessible profile into an empty
completed scan. Generic/RSS page-N recovery evidence does not qualify Douyin.

## Decision

Keep SourceAdapter, native source ownership, worker envelope and SQLite schema
unchanged. Route Douyin enumeration to an isolated provider-only backend.
Bilibili and generic extraction continue through their existing SDK adapters.

The backend requests the fixed HTTPS Douyin `aweme/post` endpoint once per
page, with at most 20 items and four MiB of response bytes. Redirects are
refused before following; OS-protected provider cookies remain scoped to the
in-memory cookie jar and cross only private stdin. An existing protected
`msToken` can enter the ephemeral provider query, never identity, argv,
packets, diagnostic messages or durable cursors. There is no browser capture,
implicit authentication, new cookie storage, credential log or automatic retry.
Titles which echo a protected cookie value of at least eight characters are
refused. Provider errors and messages are mapped to fixed redacted failures.

Use only the pure Apache-2.0 F2-derived signer at the reviewed commit documented
in `docs/licenses/f2-douyin-request-signature.md`. Standard SM3 comes from the
owned runtime; there is no system runtime or mutable dependency fallback.
Both provider modules and notices are verified against the admitted release
inventory. Their approved hashes participate in the recipe. The child hashes
the exact module bytes again and executes them from memory, rejecting linked,
oversized or changed files before network I/O. License redistribution is a
required factory condition.

Only explicit successful provider JSON with an item array, a valid bounded
cursor and `has_more` can produce a page. Missing status, duplicate keys,
malformed/oversized JSON, empty continuation, increasing/repeated resumed
cursors and incomplete 10k-item scans never report completion. Each raw entry
counts toward the discovery budget even if poisoned, private or deleted.
Stable source IDs, bounded display titles and scoped failures are the only
page metadata returned. The existing supervisor deduplicates provider IDs and
commits the page/cursor/items in its existing transaction.

## Cursor compatibility

The private Douyin cursor is version 2 and binds provider, canonical channel
hash, producer recipe, provider `max_cursor` position and visited-entry count.
The first position is zero; continuation is positive and strictly decreases
after the first page. A partial provider page is allowed; offset slicing and
replaying pages one through N are not used by this backend.

Existing jobs retain their admitted producer/runtime. A flat-SDK version-1
cursor or a cursor from another recipe/channel is rejected as
`CHECKPOINT_INVALID` by this producer. Migration is an explicit new scan; old
durable source state is neither rewritten nor silently relabeled. Existing
version-1 source contract and database do not need a migration. A future
provider cursor/algorithm change requires another recipe and compatibility
decision.

## Evidence and limits

Deterministic fixtures exercise isolated child/stdio/module verification,
signature compatibility, cursor restart, poisoned neighbors, credentials,
HTTP refusals, redirects, bounds and missing capability. They do not qualify
the live provider.

Public research against pinned F2 `7dab3e2` received HTTP 200 for its example
creator profile and HTTP 403 for a single signed first-page request. No browser
credentials were read and no media downloaded. This is refused anonymous
capability evidence; it does not prove authentication alone fixes access.
Successful live creator enumeration, protected-session access and actual
creator-page recovery remain qualification work. #167 and release readiness
stay open until their full acceptance is evidenced.

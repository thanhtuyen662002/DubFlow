# Discovery metadata

Standard-library-only anonymous adapters based on the 2026-10-25 discovery
report and the recorded responses in `tests/discovery/samples`. They return
metadata only and never fetch cover images or media, read browser sessions,
persist cookies, mutate job state, or automatically retry a failed request.

From the repository root, use the app-owned Python runtime:

```powershell
& 'D:/Projects/DubFlow/_sandbox/pyruntime/python.exe' -B -m engine.dubflow.discovery douyin trends
& 'D:/Projects/DubFlow/_sandbox/pyruntime/python.exe' -B -m engine.dubflow.discovery bilibili search --query '仙侠' --page 1
& 'D:/Projects/DubFlow/_sandbox/pyruntime/python.exe' -B -m engine.dubflow.discovery bilibili creator --mid 546195 --page 2
```

| Platform | Supported actions |
| --- | --- |
| Douyin | `trends`, `suggest --query Q` |
| Bilibili | `trends`, `suggest --query Q`, `popular`, `ranking`, `search --query Q`, `creator --mid ID` |
| Kuaishou | `trends` |

`ranking` is an additional CLI action for Bilibili's single ranking snapshot.
`--page` is one-based and applies to popular, search and creator lists.
Other platform/action combinations return `UNSUPPORTED`; the host uses its
embedded browser for the deferred Douyin/Kuaishou list capabilities.

Every invocation writes one JSON line. Operational errors also exit 0:

```json
{"ok":true,"items":[],"next_page":null}
{"ok":false,"code":"BLOCKED","message":"Platform requires verification in its browser."}
```

Only argument/usage errors exit nonzero. Codes are `BLOCKED`,
`LOGIN_REQUIRED`, `NETWORK`, `UNSUPPORTED` and `BAD_RESPONSE`. A missing list,
failed application status, captcha, voucher, or login response cannot become a
successful empty page. Preserve the requested page after an error; use
`next_page` only from a successful response. Bilibili search/creator access is
best-effort: anonymous cookies and WBI signing do not guarantee clearance.

Python callers instantiate `Douyin`, `Bilibili` or `Kuaishou` from their
respective modules, optionally supplying a shared `HttpClient`. Methods return
a frozen `DiscoveryPage` with tuple `items` and nullable `next_page`;
`to_dict()` produces the JSON-ready payload without `ok`. Suggestions use
`TrendItem` with ordinal rank and unknown heat. Video and author IDs are strings;
unknown numeric metadata stays `None`, and explicit zero stays zero. Creator
collection statistics are never used as video statistics.

Each client owns an in-memory CookieJar and serialized request queue per
platform, with at least 1.1 seconds between request starts and a 15-second
timeout. Only Kuaishou's recorded homepage redirect to `/new-reco` is followed,
with the same spacing and cookie handling; other redirects are refused.
The original research homepage response was also copied into the sample set
to retain evidence of this final URL. Kuaishou bootstraps the homepage once per adapter session;
Bilibili bootstraps SPI identifiers before search/creator and caches anonymous
nav WBI keys for one hour. Retain the adapter/client when reusing a session.

Offline tests inject `HttpClient(transport, clock=..., sleep=...)`. The
transport takes `(urllib.request.Request, timeout_seconds)` and returns
`HttpResponse(status, headers, wire_bytes)`; gzip/deflate decoding and cookie
handling still run. The tests replay only trimmed response bodies, never the
sample wrapper's duplicate `first_item` or original array counts. The successful
legacy Bilibili search sample supplies the documented shared result schema on
the WBI test route; it does not test or enable a legacy search fallback.

```powershell
& 'D:/Projects/DubFlow/_sandbox/pyruntime/python.exe' -B -m unittest discover -s tests/discovery -p 'test_*.py'
```

# Model/runtime retention v1

Resumable jobs pin exact `(id, version, sha256)` references. Cleanup may evict
unreferenced caches first, but never a pinned or active/mmap package. If an
exact reference is missing, the manager must reacquire that exact hash or
explicitly invalidate the affected stage; another version is never silently
substituted.

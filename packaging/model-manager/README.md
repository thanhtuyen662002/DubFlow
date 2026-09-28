# Model manager retention boundary

The model manager must use the exact `(id, version, sha256)` pins from the
supervisor retention graph. It may reacquire an exact missing package or mark
the dependent stage invalid; it cannot silently substitute another version.

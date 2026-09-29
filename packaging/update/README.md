# Update packaging boundary

The updater crate owns staged app/engine/model switching. Packaging metadata
must provide a verifier and healthcheck; the controller never deletes the only
known-good version and retains versioned directories for pinned jobs.

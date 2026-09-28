# Artifact locator and rebind boundary

Artifact files are durable only after a content hash and byte size have been
validated. `ArtifactLocator` stores a provider-supplied volume identity, root
kind and safe relative path; it never treats an absolute drive letter as the
artifact identity. `resolve_and_verify` can therefore rebind a moved output
folder only when the candidate volume identity, SHA-256 and size all match.

The crate depends on the supervisor storage policy for path validation and
does not open SQLite. The supervisor remains the only durable-state writer.

# Generated fixture placeholders

The catalog references small generated descriptors rather than committing
large media or model blobs. A generator or release harness materializes the
actual media, computes the SHA-256 and updates the catalog evidence. External
assets carry a fetch URI, exact byte size and hash so a benchmark run can
fail closed when the source changes.

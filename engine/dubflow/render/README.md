# Render Backend Boundary

Issue #44 keeps renderer implementations behind the `RenderBackend` protocol
in `engine/dubflow/export`. This namespace is reserved for software and
hardware media adapters; they return validated metadata and write only the
temporary path supplied by the export supervisor. A renderer never publishes a
final artifact or mutates durable job state directly.

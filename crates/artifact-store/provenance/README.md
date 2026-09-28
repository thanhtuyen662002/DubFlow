# Artifact provenance graph

Pure DAG and provenance validation. It does not touch files or SQLite; the
artifact store can use `can_reuse` before opening a cached path and persist the
returned status through the supervisor state lane.

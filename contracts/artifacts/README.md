# Artifact provenance v1

Every reusable artifact carries producer, input, configuration, model, and
contract provenance. The dependency graph is explicit and cycle-free. A
voice-scoped or translation-scoped edit invalidates only that node and its
downstream descendants; a canonical timeline edit invalidates all time-
dependent descendants. Missing or corrupt artifacts deterministically mark
their descendants stale, and a QC `VALID`/PASS status cannot survive an
upstream invalidation.

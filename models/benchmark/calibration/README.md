# Confidence calibration profiles

Calibration profiles are keyed by model ID, model version, dataset revision
and metric definition. Store machine-readable profiles and reports with the
benchmark artifact, not in the product's durable job database. Changing a
threshold or calibration dataset creates a new profile identity and makes
dependent QC reports stale through exact provenance comparison.

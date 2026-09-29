# QC provenance and calibration contract v1

QC is a downstream artifact. A report is current only when every upstream
artifact hash, producer/contract version, model version, calibration profile,
configuration hash and threshold-set hash exactly match the request being
validated. Any mismatch changes the report to STALE; a stale PASS is never
usable as a current success.

Confidence is data rather than an exception. The default policy may emit
WARN/low-confidence findings while Quick Mode continues, and a threshold
change must be tied to a new calibration profile and benchmark evidence.

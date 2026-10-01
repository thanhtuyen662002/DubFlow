# Temporal OCR text tracks v1

OCR observations are associated into temporal tracks with integer-tick ranges
and pixel bounding boxes. Role classification is conservative: uncertain text
is marked `review`, and automatic removal is allowed only for high-confidence
watermark/danmaku decisions without an ASR/OCR conflict. The document carries
conflicts, a hashed debug-overlay descriptor and a machine-readable benchmark
decision so experiments cannot silently become product defaults.

The optional production adapter is described by
`observation-schema-v1.json`. It accepts app-owned detector observations for a
real media hash, including polygons, orientation, moving/karaoke metadata and
ASR provenance. `engine.dubflow.ocr.production.AppOwnedOcrBackend` validates
the document and emits a degraded empty track document when detector output or
the pinned runtime is unavailable. This keeps the canonical subtitle export
usable while making the downgrade and affected ranges machine-readable.

# Temporal OCR text tracks v1

OCR observations are associated into temporal tracks with integer-tick ranges
and pixel bounding boxes. Role classification is conservative: uncertain text
is marked `review`, and automatic removal is allowed only for high-confidence
watermark/danmaku decisions without an ASR/OCR conflict. The document carries
conflicts, a hashed debug-overlay descriptor and a machine-readable benchmark
decision so experiments cannot silently become product defaults.

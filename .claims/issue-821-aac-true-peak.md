# Issue #821 AAC true peak conformance

Owner directive 2026-11-13 08:04 (Release Safety Closure, Stage 1 group 1), Epic #637. The Lồng tiếng export is checked against the −1.5 dBTP ceiling on the final MP4 after AAC; a bounded linear trim (no limiter, no compressor) keeps B2 under it, and the QC reports mastering conformance separately from the render and from the final result.

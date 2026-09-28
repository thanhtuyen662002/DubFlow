# Local-file slice fixtures

`landscape.mp4` and `portrait.mp4` are tiny one-frame H.264/MP4 fixtures used
for the 16:9 and 9:16 paths.  The harness reads only source metadata and
copies the bytes; it does not require a decoder in PR Fast or Integration.

`vfr_nonzero_pts.dfslice` is a deterministic probe fixture.  Its header models
the metadata a ffprobe-backed adapter would return for a variable-frame-rate
source whose presentation timestamps start at tick 900.  Keeping this case as
text makes the integer-timeline assertion portable and avoids coupling CI to a
particular ffprobe build or codec implementation.

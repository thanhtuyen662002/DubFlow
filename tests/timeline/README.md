# Timeline compatibility fixtures

`validate_fixtures.py` uses only the Python standard library and rejects
duplicate keys, numeric wide integers, non-canonical decimal strings, invalid
versions, overlapping mapping segments, invalid rotation metadata and constant
frame spacing. The Rust crate's unit tests exercise the same contract from the
producer side, including exact interpolation, gaps, negative/non-zero PTS,
wide tick round trips and all four reversible rotations.

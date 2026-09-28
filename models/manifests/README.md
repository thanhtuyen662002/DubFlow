# Model/runtime manifest catalog

`schema-v1.json` is the machine-readable contract for every model, portable
runtime, and FFmpeg candidate. Numeric byte and hardware values are decimal
strings so JavaScript and other consumers cannot round values above `2^53`.
The validator also applies the semantic `u64` bound, validates lowercase
SHA-256, and evaluates one-click eligibility.

`catalog-v1.json` contains synthetic, empty-file fixtures only. It records the
fields a real installer must receive; it does not claim that placeholder
artifacts are shipped.

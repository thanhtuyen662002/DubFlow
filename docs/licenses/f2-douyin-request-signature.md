# F2-derived Douyin request signature

The app owns and inventories its Python implementation; no external interpreter,
JavaScript runtime, executable or mutable signer download is required.

- Upstream: <https://github.com/Johnserf-Seed/f2>
- Author in upstream source: JohnserfSeed.
- Reviewed commit: `7dab3e2ffffaa2535834d28fca99dbc2e89fa9d3`.
- Original source: `f2/utils/abogus.py`, version `0.0.3` in its header.
- Original SHA256: `82cc97b63aab2ac80a5c312fe52850ccc74d6fdffe6edaee4e58add14083a3c3`.
- License: Apache-2.0, retained in full in `f2-apache-2.0.txt`.
- Original LF license SHA256: `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`.

`engine/dubflow/download/douyin/request_signature.py` derives only the four
pure signing classes from this exact source. Examples, the executable entry
point, prose docstrings and the `gmssl` dependency were removed. SM3 uses the
owned interpreter's OpenSSL implementation; an unavailable algorithm is an
explicit unsupported capability. Attribution and changes are retained in the
derived file header. No other F2 crawler, token generator, logging, network or
browser code is imported or executed in the product.

The signature output is checked against a fixed vector recorded from the
verified upstream file, with time, random seed, fingerprint and user agent
fixed. SM3 is checked against the standard `abc` digest independently.
These checks establish code compatibility, not provider permission or live
endpoint availability.

The release inventory must include both provider modules, this attribution and
the full Apache license. The runtime factory verifies their approved hashes;
the notice's canonical LF text must also match the original upstream license.
Git's Windows CRLF spelling is permitted only after its actual inventory hash
has been verified; changing license content remains a failure.
The child verifies and executes the module bytes from memory again before any
provider request. Original source bytes and exploratory upstream code stay
outside the shipped product. Any algorithm/provider update requires a new
reviewed pin and new evidence.

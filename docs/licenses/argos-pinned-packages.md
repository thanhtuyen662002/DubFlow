# Pinned Argos package attribution and release status

Issue #196 / Draft #197; publication status: **qualification pending**.

The pinned archive READMEs identify their original OPUS-MT models as CC BY 4.0
and credit Jörg Tiedemann and Santhosh Thottingal, *OPUS-MT — Building open
translation services for the World*, EAMT 2020, Lisbon. This is the package
publisher's attribution statement; library licensing does not establish model
or training-data rights. No distribution approval is implied here.

| Package | Version | Archive SHA256 |
| --- | --- | --- |
| Chinese → English | 1.9 | `62e7af5a3a48b530e47b7b3e5c78c2de79073ecd815750d2bf3ab35b4a67da2d` |
| English → Vietnamese | 1.9 | `86957101aa4099aa9a1a7492e41987d938d3cf0fdaf4fb684c0797a9d567dd16` |

Primary archive/index sources:

- [Official package index](https://github.com/argosopentech/argospm-index/blob/main/index.json)
- [Chinese package archive](https://argos-net.com/v1/translate-zh_en-1_9.argosmodel)
- [Vietnamese package archive](https://argos-net.com/v1/translate-en_vi-1_9.argosmodel)
- [Argos Translate library license](https://github.com/argosopentech/argos-translate/blob/master/LICENSE)
- [CC BY 4.0 terms](https://creativecommons.org/licenses/by/4.0/)

The verified installation retains each README/metadata file; archive and
extracted-tree hashes cover them. The en-vi README title says version 1.0 while
its package metadata says 1.9; retain the original notice and record that
discrepancy rather than silently rewriting attribution. Backend validation uses
the pinned package metadata version.

The archives also contain legacy Stanza data, which the adapter does not load.
They remain inside the archive/tree pin and require their own inventory before
publication. Release must package appropriate notices and attribution for the
model conversion, Argos/runtime libraries and bundled assets, resolve any
additional package/training provenance conditions, and link the license terms.
Weights are downloaded to app-owned storage, never committed to normal Git.

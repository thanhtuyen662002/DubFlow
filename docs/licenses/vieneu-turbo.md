# VieNeu v3 Turbo CPU candidate: distribution inventory

Status: license-approved candidate data; human listening, packaged Windows and
full production qualification remain pending under #166 / Draft #195.
The user rejected the previous VAIS1000 default on 2026-10-07.

## Pinned upstream data

| Component | Immutable upstream identity | Terms |
| --- | --- | --- |
| Turbo fp32 ONNX model/config/tokenizer/heads | [VieNeu model](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo/tree/61b85e3d937fbbacb387714180e8182823512523) | Apache-2.0 |
| MOSS Nano ONNX full decoder and shared data | [OpenMOSS ONNX](https://huggingface.co/OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX/tree/ceff0d0749bfb3fa2d61149794ec6feef0d1e1ae) | Apache-2.0 |
| Official preset speaker embeddings/reference codes | [VieNeu source roster](https://github.com/pnnbao97/VieNeu-TTS/blob/85344322b7258b4e25479b692e8e3396baf9db34/src/vieneu/assets/voices_v3_turbo.json) | Apache-2.0 per upstream model card |
| SDK Python wheel | [vieneu 3.8.3](https://pypi.org/project/vieneu/3.8.3/) SHA-256 `7388d166e65746f5bb075bf8094d324f8131a82393680b712260d6f2f983ef06` | Apache-2.0 |
| Vietnamese frontend | sea-g2p 0.9.1 | Apache-2.0; preserve wheel notices |
| Runtime | ONNX Runtime 1.30.0, tokenizers 0.23.2, NumPy 2.2.6 | Preserve each distribution's notices |

The pinned model card explicitly includes the preset embeddings/reference
codes in its Apache distribution scope. Its maintainer states that speakers
or rightsholders granted rights for training and synthetic speech. Preserve
that card and attribution as upstream evidence; this is not a separate
DubFlow verification of each speaker's underlying consent agreement.
The adapter uses those bundled presets only. Reference enrollment, downloaded
Python model implementations and `trust_remote_code` are not invoked.

`production-vieneu-v1.json` inventories 13 exact files totaling 520,493,843
bytes, including both model cards and the Apache license text. The inventory
digest is `30163bab69ff85db094314385a56510129293f600fc130e53d1c9b3b4b276113`.
The initial candidate preset is Ngọc Huyền; its manifest is an independent
voice/recipe identity and remains pending human selection/quality approval.
The roster is pinned to 25 entries, rather than inferred from an older card.

The PyPI wheel and GitHub source at the same version have one reviewed
`core_utils.py` difference: the wheel lacks later unused remote-code and GPU
sampling helpers. The adapter pins the actual wheel file digest separately;
the ONNX engine, repetition-history and phonemizer files match the reviewed
source. Only the CPU preset path is used, with fixed bounded sampling.

## Release obligations

Preserve model cards, license text, attribution and runtime third-party
notices in the delivered package/inventory. The current requirements install
the complete upstream SDK distribution; its declared transitive packages
must be covered by the package license inventory even though the native
preset entrypoint uses the torch-free subset. Development `--no-deps`
installation proves inference only, not completeness of a shipped runtime.

The historical Sherpa/eSpeak and VAIS1000 adapter remains available to reproduce
old pinned evidence. If those runtimes continue to ship, their existing
notice/source obligations remain under `vietnamese-neural-tts.md`. Switching
the default voice does not waive obligations for bytes still distributed.

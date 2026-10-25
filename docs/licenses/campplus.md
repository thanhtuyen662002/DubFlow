# CAM++ Chinese speaker embeddings

The opt-in experimental speaker stage uses 3D-Speaker's
`iic/speech_campplus_sv_zh-cn_16k-common` model, exported to ONNX by
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx). The model is licensed
under Apache-2.0 and the ONNX artifact is redistributed unchanged.

- Upstream: [3D-Speaker](https://github.com/modelscope/3D-Speaker).
- Model: [ModelScope](https://www.modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common).
- Distribution pins the model and `NOTICE-campplus.md` by size and SHA-256 in
  `models/manifests/production-speaker-campplus-v1.json`. The notice is downloaded
  alongside the model and must accompany redistribution.

Audio clusters describe similar source voices within one job. They do not
identify visual characters or people. Pitch-based casting is an uncertain
heuristic, not a determination of a person's gender. This optional profile is
downloaded only when multi-voice dubbing is enabled.

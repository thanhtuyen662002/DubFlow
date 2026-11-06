# UVR MDX-Net: Kim_Vocal_2

Tách giọng estimates the original voice with the `Kim_Vocal_2` MDX-Net model
(by KimberleyJensen), published in Ultimate Vocal Remover's public model
release. Ultimate Vocal Remover is licensed under the MIT License,
Copyright (c) 2022 Anjok07 and the UVR contributors.

- Application: https://github.com/Anjok07/ultimatevocalremovergui
- Models: https://github.com/TRvlvr/model_repo/releases/tag/all_public_uvr_models
- MDX-Net architecture: https://github.com/kuielab/mdx-net (MIT)

The ONNX file is downloaded unchanged from the upstream release, only when a
user separates a video's voice, and is verified against the size and SHA-256
pinned in `models/manifests/production-separation-mdx-v1.json`. No model
weights are stored in Git. The STFT parameters (7680-point FFT, hop 1024,
3072 bins, 256 frames, compensation 1.009) come from UVR's `model_data.json`.

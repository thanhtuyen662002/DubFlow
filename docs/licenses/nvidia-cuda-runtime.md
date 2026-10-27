# NVIDIA cuBLAS 12 and cuDNN 9 (optional GPU pack)

These libraries are NVIDIA proprietary software, distributed by NVIDIA as the `nvidia-cublas-cu12` 12.8.4.1 and `nvidia-cudnn-cu12` 9.10.2.21 wheels on PyPI. Their license files are `nvidia_cublas_cu12-12.8.4.1.dist-info/License.txt` and `nvidia_cudnn_cu12-9.10.2.21.dist-info/licenses/License.txt`.

**What DubFlow stores:**
- DubFlow does not store these libraries in Git or ship them in its installer.
- The cuDNN 9.10.2.21 dispatcher `cudnn64_9.dll` is already part of the CTranslate2 wheel that DubFlow ships, under that wheel's terms.

**When the libraries are downloaded:** only when the worker finds an NVIDIA driver, and directly from NVIDIA's PyPI files to the user's machine.

**How they are stored:**
- `models/manifests/accelerator-nvidia-cuda12-v1.json` pins the wheels and each extracted library by size and SHA-256.
- The libraries are extracted unmodified.
- Each wheel's license file is installed next to them as `LICENSE-cublas.txt` and `LICENSE-cudnn.txt`.
- The wheels are deleted after extraction.

**How they are used:** only to run CTranslate2/Faster-Whisper ASR on the user's own NVIDIA GPU (ADR-0038).

Before DubFlow itself redistributes these files, for example in an offline installer, NVIDIA's redistribution terms in those license files must be reviewed.

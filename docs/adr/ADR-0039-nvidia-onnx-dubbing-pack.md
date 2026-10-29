# ADR-0039: Optional ONNX Runtime CUDA pack for VieNeu dubbing

Status: Proposed, Issue #323 leaf 2. Extends ADR-0038 (NVIDIA CUDA pack for ASR).

## Context
After ASR moved to the GPU (ADR-0038), dubbing is the longest stage. On the #291 recording it took more than 2 hours of the 4 h 48 min. VieNeu runs through ONNX Runtime 1.30.0 in its own native process (`vieneu_native.py`), and the shipped `onnxruntime` wheel is the CPU build.

Three facts constrain the design:
- **The SDK is pinned.** The pinned VieNeu 3.8.3 SDK creates every session with `providers=["CPUExecutionProvider"]` inside `onnx_runtime_lite.py`. That file is pinned by hash and must not change.
- **The GPU build is CUDA 13.** `onnxruntime-gpu` 1.30.0 on PyPI is built for CUDA 13. It needs cuBLAS 13, cudart 13, cuFFT 12, NVRTC 13 and cuDNN 9 for CUDA 13. Microsoft's separate CUDA 12 feed stops at 1.29.
- **DLL names collide.** cuDNN 9 for CUDA 12 (ASR) and for CUDA 13 (ONNX) share the DLL names `cudnn64_9.dll` and `cudnn_*64_9.dll`. Windows resolves a by-name load to a module already loaded under that name, so the two cannot be loaded in one process.

## Decision
1. **A second pinned pack.** `models/manifests/accelerator-nvidia-onnx-cuda13-v1.json` pins six PyPI wheels by URL, size and SHA-256:
   - `onnxruntime-gpu` 1.30.0;
   - `nvidia-cuda-runtime` 13.4.92;
   - `nvidia-cublas` 13.8.0.4;
   - `nvidia-cufft` 12.4.0.43;
   - `nvidia-cuda-nvrtc` 13.4.92;
   - `nvidia-cudnn-cu13` 9.27.0.42.

   It also pins every member DubFlow uses:
   - 16 DLLs, in load order;
   - the 16 `onnxruntime` package files that `import onnxruntime` and a CUDA session read (the TensorRT provider is left out);
   - 5 license notices.

   The ADR-0038 extraction rules apply unchanged: only pinned members are written, to unique create-only temporary files that are renamed after their hash matches, and the wheels are deleted. Package files go to `<model root>/accelerators/nvidia-onnx-cuda13-v1/site`.
2. **Requirements, checked before any download:**
   - driver reports CUDA 13.0 or newer (driver R580 or newer);
   - compute capability 7.5 or newer (CUDA 13's floor, so RTX 20-series or newer);
   - at least 4 GB of GPU memory.

   ASR keeps its own CUDA 12 requirements, so a GTX 10-series card still runs ASR on the GPU and dubs on the CPU.
3. **Loaded only in the VieNeu process.**
   - **In the worker:** it chooses the device (`select_tts_device`) and verifies the pack by hash. It never loads the pack, because it may already hold the CUDA 12 pack.
   - **What the native process receives:** the manifest path, the manifest's SHA-256 and the pack root.
   - **What the native process does, before anything imports `onnxruntime`:**
     1. It checks the manifest hash.
     2. It opens every pinned DLL and package file with read-only sharing, opening a reparse point as itself, and hashes it.
     3. It keeps every file open for the life of the process.
     4. It preloads the DLLs in manifest order by absolute path.
     5. It puts the pack's `site` folder first on `sys.path`.
   - **After the import:** the imported module must be that folder's `onnxruntime/__init__.py` at version 1.30.0, with `CUDAExecutionProvider` available.
4. **The pinned SDK is not edited.** In that process, `onnxruntime.InferenceSession` is replaced by a subclass that asks for `CUDAExecutionProvider`, then the CPU. The subclass refuses a session that did not land on CUDA. The SDK's source files keep their pinned hashes.
5. **Any failure falls back to the CPU** (`_DeviceProcess`). This covers a probe or download failure, a hash mismatch, a native process that cannot start or did not load CUDA, and a CUDA error, out-of-memory, crash or timeout during a phrase. In each case a new CPU native process takes over, retries that phrase once, and serves the rest of the job. Later voices in a multi-voice job start on the CPU. The job records `ACCELERATOR_FALLBACK:`. A content refusal of one phrase is not a device failure.
6. **GPU speech is its own recipe.** fp32 GPU kernels are not byte-identical to the CPU. GPU synthesis therefore uses runtime `…+onnxruntime-gpu-1.30.0-cuda13` and TTS profile `gpu`, and the B2 generation identity includes the device. GPU and CPU phrases never share a checkpoint or a synthesis-cache entry. A successful GPU job records the informational `TTS_ACCELERATOR: cuda on <GPU>`, and the completion message names the GPU.

## Consequences
- **What NVIDIA users with an RTX 20-series or newer card pay:**
  - about 1.23 GB downloaded once and 1.74 GB on disk, on top of the ASR pack;
  - about 1.7 GB hashed twice per job: once by the worker, and once under lock by each VieNeu native process.
- **Separation is not covered:** Spleeter still runs on the CPU. It runs inside the worker, which already holds CPU ONNX Runtime and possibly the CUDA 12 pack, so moving it needs its own native process.
- **No test needs a GPU.** Hermetic tests cover the manifest, nested package extraction, the CUDA 13 requirements, the native loader (a verified fake `onnxruntime` comes first, sessions get CUDA, and a change, a different manifest or an earlier import is refused), the CPU fallback and the separate GPU identity.
- **Not proven yet:** real speed, VRAM and out-of-memory behaviour are NOT_RUN until a run on NVIDIA hardware (#323 leaf 4).

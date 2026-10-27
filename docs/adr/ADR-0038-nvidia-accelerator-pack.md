# ADR-0038: Optional NVIDIA CUDA pack for ASR

Status: Proposed, Issue #323. Extends ADR-0005 (storage topology) and the model download-on-first-use policy.

## Context
DubFlow runs every model on the CPU. On the #291 recording, ASR alone took about 1 h 50 min of the 4 h 48 min of processing.

The bundled CTranslate2 4.8.2 Windows wheel, used by Faster-Whisper, is already a CUDA build:
- `ctranslate2.dll` delay-loads `nvcuda.dll` and `cublas64_12.dll`.
- The wheel carries NVIDIA's cuDNN 9.10.2.21 dispatcher, `cudnn64_9.dll`.

It lacks only NVIDIA's cuBLAS 12 and cuDNN 9 sub-libraries. Shipping those in the installer would grow every download by about 1.3 GB, for users who mostly have no NVIDIA GPU.

## Decision
1. **Only with a driver.** The worker probes for an NVIDIA GPU before ASR, and only then may it download an optional pack. The probe uses the CUDA driver API from `System32\nvcuda.dll`, loaded by full path through `ctypes`, with no CUDA toolkit or new Python dependency. Without a driver, or on another OS, nothing is downloaded and nothing is logged.
2. **Pinned NVIDIA wheels.** `models/manifests/accelerator-nvidia-cuda12-v1.json` pins NVIDIA's own wheels on `files.pythonhosted.org`, by URL, size and SHA-256:
   - `nvidia-cublas-cu12` 12.8.4.1;
   - `nvidia-cudnn-cu12` 9.10.2.21, the same cuDNN version as the shipped dispatcher, which is byte-identical.

   It also pins every library DubFlow loads from them, by member path, size and SHA-256, plus the two license files. The existing resumable, verified downloader fetches the wheels into the user-owned model root. Only the pinned members are extracted, each atomically and checked against its pin, into `<model root>/accelerators/nvidia-cuda12-v1/bin`. The 1.2 GB of wheels are then deleted.
3. **This is a code-bearing download.** It is the one place where the model root holds native code. Two checks make it acceptable:
   - Every library is re-hashed against the signed release's manifest before each job loads it. A mismatch never loads; the pack is restored from freshly verified wheels or the job uses the CPU.
   - Libraries are preloaded by absolute path in a fixed order, so CTranslate2's by-name loads resolve to exactly these modules and never to anything found on `PATH`.
4. **Requirements before downloading.** The GPU is used only when:
   - the driver reports CUDA 12.0 or newer;
   - device 0 has compute capability 6.1 or higher, the int8 GEMM floor;
   - it has at least 2 GB of memory;
   - CTranslate2 sees the device and supports a compute type, preferring `int8_float16`, then `int8`, `float16`, `float32`.

   The first three are checked before any download.
5. **Any failure falls back to the CPU.** A probe, download, verification, load or compute-type failure makes the job run ASR on the CPU exactly as before, with an `ACCELERATOR_FALLBACK:` warning. A CUDA error or out-of-memory while constructing the model or decoding a chunk retries that chunk on the CPU, and every later chunk stays on the CPU. Content failures (`ChunkAsrError`) are not retried. A worker option `accelerator: "cpu"` skips the probe.
6. **Identity does not change.** The chunk identity, transcript schema and evidence schemas are unchanged. GPU and CPU chunks are interchangeable checkpoints, so a job can resume across devices. The device used is recorded as an `ASR_ACCELERATOR:` warning.

## Consequences
- **What NVIDIA users pay:** about 1.3 GB downloaded once and 1.8 GB on disk, plus about 8 s of hashing before each ASR stage.
- **No test needs a GPU:** a fake driver, a fixture pack and fake Whisper models cover the probe, the pins, extraction, tamper refusal, device selection and fallback.
- **Not proven yet:** real speed, VRAM and out-of-memory behaviour are NOT_RUN until a qualification run on NVIDIA hardware (#323 leaf 4).
- **Out of scope:** TTS (VieNeu) and separation (Spleeter) stay on the CPU. Moving them needs an `onnxruntime-gpu` runtime variant, which is a separate leaf.

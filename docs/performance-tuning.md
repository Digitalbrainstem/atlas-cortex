# Atlas Cortex — Performance Tuning Guide

This document captures hardware-specific tuning decisions, benchmark data,
and configuration rationale for the Atlas Cortex voice pipeline.

---

## GPU Allocation Strategy

Per the design docs (`voice-engine.md`, `context-management.md`), the
multi-GPU layout is:

| GPU | Hardware | Services | Purpose |
|-----|----------|----------|---------|
| GPU 0 (largest) | AMD RX 7900 XT (20 GB) | `atlas-ollama`, `atlas-whisper` | LLM + STT (sequential, no contention) |
| GPU 1 (CUDA)    | NVIDIA RTX 4060 (8 GB)  | `atlas-orpheus` | Orpheus TTS via vLLM + SNAC |

Single-GPU systems fall back to time-multiplexed sharing (Ollama
auto-unload). CPU-only systems run STT/TTS on CPU.

---

## TTS Pipeline: Orpheus via vLLM + SNAC

### Architecture (Single Container)

```
text → atlas-orpheus (vLLM + SNAC on NVIDIA CUDA, port 5005)
           ↓ vLLM /v1/completions (internal, localhost:8000)
       Token generation (vLLM, FP16/FP8)
           ↓ <custom_token_*> stream
       SNAC decode (CUDA) → 24kHz 16-bit PCM WAV stream
```

The Orpheus container embeds both the LLM inference engine (vLLM) and the
SNAC audio decoder in a single image. No external llama.cpp needed.
Model weights auto-download from HuggingFace on first run (~6GB).

### Previous Architecture (Deprecated)

The old two-container setup used llama.cpp (Vulkan) for inference and
Orpheus-FastAPI (Intel XPU) for SNAC decoding. This was replaced by the
single vLLM container for simplicity and CUDA performance.

### Model Quantization: Q4_K_M vs Q8_0

Benchmarked on Intel Arc B580 Vulkan (300 tokens):

| Quantization | Size | VRAM | tok/s | Quality |
|-------------|------|------|-------|---------|
| Q8_0 | 4.0 GB | 3.9 GB | 26.3 | Reference |
| **Q4_K_M** | **2.2 GB** | **2.5 GB** | **48.7** | **Comparable** |
| Q4_K_M + flash | 2.2 GB | 2.5 GB | 48.7 | No additional gain |
| Q4_K_M + KV Q4 | 2.2 GB | 2.4 GB | 48.8 | Negligible gain |

**Decision:** Default to Q4_K_M. The 85% speed improvement far outweighs
any minor quality difference. SNAC decoding is identical — only the
token generation precision changes. Audio quality is structurally the same
(WAV format, sample rate, bit depth all unchanged).

To switch back to Q8 for higher quality:
```bash
# In docker/.env
ORPHEUS_MODEL_NAME=Orpheus-3b-FT-Q8_0.gguf
```

### SNAC Decode Overhead

Measured by comparing direct llama.cpp calls vs full Orpheus-FastAPI E2E:

| Layer | Time | % of Total |
|-------|------|-----------|
| llama.cpp inference | 14,124 ms | 96.6% |
| SNAC decode + Python + file I/O | 374 ms | 2.6% |
| HTTP/networking | ~125 ms | 0.8% |

**Conclusion:** The SNAC/Python layer is not a bottleneck. Optimizations
must target the inference backend or model quantization.

### Combined Improvement

| Configuration | tok/s | 300-token time | vs Baseline |
|--------------|-------|----------------|-------------|
| SYCL Q8 (original) | 21.2 | 14.2s | baseline |
| Vulkan Q8 | 26.3 | 11.4s | +24% |
| **Vulkan Q4_K_M (production)** | **43.2** | **6.9s** | **+104%** |

---

## STT Pipeline: Faster-Whisper

### Current Configuration

Faster-Whisper runs on CPU with `distil-large-v3` model (~3 GB RAM).
Per the design docs, it should run on GPU 1 alongside TTS.

### GPU Acceleration Options

With Q4_K_M Orpheus using only 2.5 GB of the Intel Arc B580's 12 GB,
**8.4 GB VRAM is free** — more than enough for Whisper.

Options under evaluation:
- **OpenVINO backend** for faster-whisper (native Intel GPU support)
- **CTranslate2 with Intel GPU** acceleration
- **Whisper.cpp** with Vulkan backend ← **chosen, deployed**

### Deployment: whisper.cpp Vulkan

Using `ghcr.io/ggml-org/whisper.cpp:main-vulkan` with `large-v3-turbo-q5_0`:

| Metric | CPU (faster-whisper) | GPU Vulkan (whisper.cpp) |
|--------|---------------------|--------------------------|
| Model | distil-large-v3 | large-v3-turbo-q5_0 |
| Size | ~3 GB RAM | ~574 MB VRAM |
| Cold inference | ~8-12s | ~2.4s |
| Warm inference | ~3-5s | **~1.15s** |
| Backend | CTranslate2 CPU | Vulkan Intel Arc |

Whisper.cpp uses HTTP API at `/inference` (multipart POST), not Wyoming
protocol. The cortex `whisper_cpp.py` client handles the conversion.

### VRAM Budget (NVIDIA RTX 4060 = 8 GB)

| Service | VRAM | Model |
|---------|------|-------|
| vLLM Orpheus TTS (FP16) | ~6.0 GB | orpheus-tts-0.1-finetune-prod |
| SNAC decoder (CUDA) | 0.3 GB | snac_24khz |
| Compute buffers | 0.5 GB | — |
| **Total used** | **~6.8 GB** | — |
| **Free** | **~1.2 GB** | Tight — use FP8 for headroom |

With FP8 quantization (`VLLM_QUANTIZATION=fp8`):

| Service | VRAM | Model |
|---------|------|-------|
| vLLM Orpheus TTS (FP8) | ~3.0 GB | orpheus-tts-0.1-finetune-prod |
| SNAC decoder (CUDA) | 0.3 GB | snac_24khz |
| Compute buffers | 0.5 GB | — |
| **Total used** | **~3.8 GB** | — |
| **Free** | **~4.2 GB** | Comfortable margin |

### Whisper STT VRAM Budget (AMD RX 7900 XT = 20 GB)

| Service | VRAM | Model |
|---------|------|-------|
| Ollama LLM (qwen2.5:7b) | ~5.5 GB | — |
| whisper.cpp STT | ~0.6 GB | large-v3-turbo-q5_0 |
| **Total used** | **~6.1 GB** | — |
| **Free** | **~13.9 GB** | Plenty of headroom |

---

## LLM Pipeline: Ollama on AMD

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| GPU | AMD RX 7900 XT (20 GB) | Largest GPU = LLM only |
| Model | qwen2.5:7b (~5.5 GB) | Best balance of quality/speed |
| OLLAMA_KEEP_ALIVE | 0 | Model stays loaded forever |
| Speed | ~98 tok/s generation | Adequate for conversational use |

---

## Docker Compose Configuration

### GPU Override Files

| File | Use Case |
|------|----------|
| `docker-compose.gpu-amd.yml` | AMD LLM/STT + NVIDIA RTX 4060 TTS (Derek's setup) |
| `docker-compose.gpu-intel.yml` | Intel-only (single GPU, no Orpheus — use Kokoro) |
| `docker-compose.gpu-nvidia.yml` | All-NVIDIA systems |

### Key Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ORPHEUS_MODEL` | `canopylabs/orpheus-tts-0.1-finetune-prod` | HuggingFace model for vLLM |
| `VLLM_GPU_UTIL` | `0.85` | GPU memory fraction for vLLM |
| `VLLM_QUANTIZATION` | (empty) | Set to `fp8` for lower VRAM |
| `GGML_VK_VISIBLE_DEVICES` | `0` (in AMD override) | Target AMD GPU for Whisper Vulkan |
| `OLLAMA_KEEP_ALIVE` | `0` | Keep LLM loaded forever |

---

## Hardware-Specific Notes

### Intel Arc B580

- **Vulkan > SYCL** for llama.cpp inference (25% faster)
- Flash attention hurts SYCL performance (75% slower!)
- Flash attention has no effect on Vulkan performance
- `GGML_VK_VISIBLE_DEVICES` needed when AMD GPU is also present
- PyTorch XPU works for SNAC decode (torch 2.10+xpu)
- `render` group doesn't exist on Unraid — only `video` needed
- KV cache quantization (Q4/Q8) has negligible impact on speed

### AMD RX 7900 XT

- ROCm image (`ollama/ollama:rocm`) required
- No `curl` in ROCm image — use `bash /dev/tcp` for healthchecks
- Device nodes: `/dev/kfd`, `/dev/dri/card1`, `/dev/dri/renderD129`

### Unraid 7.1.4

- No `render` group — omit from `group_add`
- DRI devices are world-accessible (`rwxrwxrwx`)
- No Python on host — parse JSON in containers or remotely

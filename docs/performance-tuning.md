# Atlas Cortex — Performance Tuning Guide

This document captures hardware-specific tuning decisions, benchmark data,
and configuration rationale for the Atlas Cortex voice pipeline.

---

## GPU Allocation Strategy

Per the design docs (`voice-engine.md`, `context-management.md`), the
multi-GPU layout is:

| GPU | Hardware | Services | Purpose |
|-----|----------|----------|---------|
| GPU 0 (largest) | AMD RX 7900 XT (20 GB) | `atlas-ollama` | LLM only — zero model-switch latency |
| GPU 1 (second)  | Intel Arc B580 (12 GB)  | `atlas-llama-voice`, `atlas-orpheus`, `atlas-whisper` | Voice: TTS inference, SNAC decode, STT |

Single-GPU systems fall back to time-multiplexed sharing (Ollama
auto-unload). CPU-only systems run STT/TTS on CPU.

---

## TTS Pipeline: Orpheus via Orpheus-FastAPI

### Architecture

```
text → atlas-orpheus (FastAPI + SNAC on Intel XPU, port 5005)
           ↓ /v1/completions
       atlas-llama-voice (llama.cpp Vulkan, port 5006)
           ↓ <custom_token_*> stream
       SNAC decode (Intel XPU) → 24kHz 16-bit PCM WAV
```

### Backend Selection: Vulkan vs SYCL

Benchmarked on Intel Arc B580 with Orpheus 3B Q8_0 (300 tokens):

| Backend | Image | tok/s | Notes |
|---------|-------|-------|-------|
| SYCL | `llama.cpp:full-intel` | 21.2 | Intel's oneAPI/SYCL stack |
| **Vulkan** | **`llama.cpp:full-vulkan`** | **26.3** | **+24% — chosen default** |
| Vulkan + flash-attn | `llama.cpp:full-vulkan` | 26.3 | No gain on Vulkan |

**Decision:** Use Vulkan. Intel's SYCL llama.cpp implementation is
immature; flash attention on SYCL was catastrophic (4.9 tok/s). Vulkan
consistently outperforms SYCL on Arc GPUs as of early 2026.

When Vulkan sees multiple GPUs, use `GGML_VK_VISIBLE_DEVICES=1` in the
GPU override file to target the Intel card specifically.

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

### VRAM Budget (Intel Arc B580 = 12 GB)

| Service | VRAM | Model |
|---------|------|-------|
| llama.cpp Orpheus TTS | 2.5 GB | Q4_K_M |
| SNAC decoder (XPU) | 0.3 GB | — |
| whisper.cpp STT | 0.6 GB | large-v3-turbo-q5_0 |
| Compute buffers | 0.3 GB | — |
| **Total used** | **~3.7 GB** | — |
| **Free** | **~8.3 GB** | Available for future services |

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
| `docker-compose.gpu-amd.yml` | AMD LLM + Intel Voice (Derek's setup) |
| `docker-compose.gpu-intel.yml` | Intel-only (single GPU, shared) |
| `docker-compose.gpu-nvidia.yml` | NVIDIA LLM + Intel/other Voice |

### Key Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ORPHEUS_MODEL_NAME` | `Orpheus-3b-FT-Q4_K_M.gguf` | Orpheus GGUF model file |
| `ORPHEUS_FASTAPI_BUILD_CTX` | GitHub URL | Override with local path for dev |
| `GGML_VK_VISIBLE_DEVICES` | `1` (in AMD override) | Target Intel GPU for Vulkan |
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

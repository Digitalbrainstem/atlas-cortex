# Orpheus TTS — single container (llama.cpp CUDA + SNAC + FastAPI)
# Targets: NVIDIA RTX 4060 (8GB), CUDA 12.x
# GGUF Q4_K_M model = ~2.2GB VRAM, leaves plenty of room
# Uses llama.cpp server-cuda image directly (has CUDA + ggml backends built in)

FROM ghcr.io/ggml-org/llama.cpp:server-cuda

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System dependencies + Python 3.11
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-dev python3.11-venv python3-pip \
    curl && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 && \
    rm -rf /var/lib/apt/lists/*

# Python deps — SNAC for audio decoding (CPU), aiohttp for llama.cpp API
RUN pip install --no-cache-dir \
    torch==2.5.1+cpu --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir \
    snac==1.2.1 \
    fastapi>=0.115.0 \
    "uvicorn[standard]>=0.32.0" \
    aiohttp>=3.10.0 \
    numpy \
    pydantic>=2.0

WORKDIR /app
COPY server.py .

# Pre-download SNAC model at build time (~40MB)
RUN python -c "from snac import SNAC; SNAC.from_pretrained('hubertsiuzdak/snac_24khz')"

# Model directory — mount a volume with the GGUF file
VOLUME /models

HEALTHCHECK --interval=30s --timeout=10s --retries=5 --start-period=120s \
    CMD curl -sf http://localhost:5005/health || exit 1

EXPOSE 5005

ENTRYPOINT []
CMD ["python", "server.py"]
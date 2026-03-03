# ── Stage 1: Build admin UI ──────────────────────────────────────
FROM node:20-slim AS admin-build
WORKDIR /build
COPY admin/package*.json ./
RUN npm ci --ignore-scripts
COPY admin/ ./
RUN npm run build

# ── Stage 2: Atlas Cortex runtime ────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# System deps for mDNS satellite discovery and SSH provisioning
RUN apt-get update && apt-get install -y --no-install-recommends \
    avahi-utils libnss-mdns curl \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY cortex/ ./cortex/

# Copy pre-built admin UI from build stage
COPY --from=admin-build /build/dist/ ./admin/dist/

# Copy satellite agent source (for provisioning to push to satellites)
COPY satellite/ ./satellite/

# Create data directory
RUN mkdir -p /data

ENV CORTEX_DATA_DIR=/data
ENV CORTEX_HOST=0.0.0.0
ENV CORTEX_PORT=5100

# Default service discovery (host networking — services publish ports)
ENV PIPER_HOST=localhost
ENV PIPER_PORT=10200
ENV STT_BACKEND=whisper_cpp
ENV STT_HOST=localhost
ENV STT_PORT=10300
ENV LLM_URL=http://localhost:11434
ENV ORPHEUS_FASTAPI_URL=http://localhost:5005
ENV TTS_PROVIDER=orpheus

EXPOSE 5100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -sf http://localhost:5100/health || exit 1

CMD ["python", "-m", "cortex.server"]

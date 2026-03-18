# 🚀 Atlas Cortex — Quick Start Guide

Get Atlas Cortex running in under 5 minutes. Choose the path that fits your setup.

---

## Installation Options

### Server Only (no CLI)
```bash
pip install atlas-cortex
atlas-server  # starts the server
```

### Server + CLI Agent
```bash
pip install atlas-cortex[cli]
atlas chat    # interactive chat
atlas agent "build something"  # autonomous agent
```

### Everything
```bash
pip install atlas-cortex[all]  # server + cli + media + vector search
```

---

## Path A: Docker (Recommended)

### Prerequisites
- Docker and Docker Compose
- 8GB+ RAM (16GB recommended)
- GPU optional but recommended for voice and LLM performance

### Pull and Run
```bash
docker pull ghcr.io/betanu701/atlas-cortex:latest
docker run -d --name atlas -p 5100:5100 -v atlas-data:/data ghcr.io/betanu701/atlas-cortex
```

### With GPU (NVIDIA)
```bash
docker pull ghcr.io/betanu701/atlas-cortex:latest-nvidia
docker run -d --gpus all --name atlas -p 5100:5100 -v atlas-data:/data ghcr.io/betanu701/atlas-cortex:latest-nvidia
```

### Docker Compose
```bash
# 1. Clone the repository
git clone https://github.com/Betanu701/atlas-cortex.git
cd atlas-cortex

# 2. Copy the example environment file
cp .env.example .env    # edit .env to set your secrets

# 3. Start the stack
docker compose -f docker/docker-compose.yml up -d

# 4. Open the web UI
#    http://localhost:5100
```

### GPU Compose Overrides
```bash
# NVIDIA (CUDA)
docker compose -f docker/docker-compose.yml \
               -f docker/docker-compose.gpu-nvidia.yml up -d

# AMD (ROCm / Vulkan)
docker compose -f docker/docker-compose.yml \
               -f docker/docker-compose.gpu-amd.yml up -d

# Intel (oneAPI)
docker compose -f docker/docker-compose.yml \
               -f docker/docker-compose.gpu-intel.yml up -d
```

---

## Path B: Bare Metal

### Prerequisites
- Python 3.11+
- [Ollama](https://ollama.com) (or any OpenAI-compatible LLM backend)

### Steps

```bash
# 1. Clone and set up
git clone https://github.com/Betanu701/atlas-cortex.git
cd atlas-cortex

# 2. Create a virtual environment
python3 -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the interactive installer (detects hardware, pulls models)
python -m cortex.install

# 5. Start the server
python -m cortex.server

# 6. Open http://localhost:5100
```

### Admin Panel

```bash
# Build the admin panel (requires Node.js 18+)
cd admin && npm install && npx vite build && cd ..
# Then restart the server — admin panel is at http://localhost:5100/admin/
# Default login: admin / atlas-admin  (change immediately)
```

---

## Path C: Satellite Speaker (Raspberry Pi)

### Option 1: Install Script

```bash
# On the Raspberry Pi
curl -sL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/install.sh | bash
```

### Option 2: Manual Install

```bash
git clone https://github.com/Betanu701/atlas-cortex.git
cd atlas-cortex/satellite
pip install -r requirements.txt
python -m atlas_satellite --server <atlas-server-ip>:5100
```

The satellite registers with Atlas automatically and appears in **Admin → Satellites**.

---

## What's Next?

| Task | Command / URL |
|------|---------------|
| Discover network services (HA, Nextcloud, etc.) | `python -m cortex.discover` |
| Connect to Open WebUI | Add `http://<host>:5100/v1` as an OpenAI connection |
| Configure environment variables | See [docs/configuration.md](configuration.md) |
| Read the full architecture | See [docs/phases.md](phases.md) |
| Run the test suite | `python -m pytest tests/ -q` |

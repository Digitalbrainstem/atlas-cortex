FROM python:3.12-slim

WORKDIR /app

# System dependencies for zeroconf (mDNS) and asyncssh
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
    avahi-utils libnss-mdns && \
    rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY cortex/ cortex/
COPY admin/dist/ admin/dist/

# Data directory (SQLite DB, keys, etc.)
RUN mkdir -p /data
ENV ATLAS_DATA_DIR=/data

EXPOSE 5100

CMD ["uvicorn", "cortex.server:app", "--host", "0.0.0.0", "--port", "5100"]

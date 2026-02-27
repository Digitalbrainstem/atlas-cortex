FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY cortex/ ./cortex/

# Create data directory
RUN mkdir -p /data

ENV CORTEX_DATA_DIR=/data
ENV CORTEX_HOST=0.0.0.0
ENV CORTEX_PORT=5100

EXPOSE 5100

CMD ["python", "-m", "cortex.server"]

# Stage 1: Build native Go WAA signer daemon
FROM golang:1.27-trixie AS signer-builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    clang \
    libc++-dev \
    libc++abi-dev \
    build-essential \
    file \
    && rm -rf /var/lib/apt/lists/*

COPY waa-signer/ waa-signer/
WORKDIR /build/waa-signer
RUN if [ ! -f "bin/waa-signer" ] || ! file bin/waa-signer | grep -q "ELF"; then \
        chmod +x build.sh && ./build.sh; \
    fi

# Stage 2: Runtime Python environment
# Keep the runtime on the same Debian release as the native signer build.
FROM python:3.11-slim-trixie

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AISTUDIO_DATA_DIR=/app/data \
    AISTUDIO_CONFIG_FILE=/app/data/config.yaml

# Install minimal system dependencies for HTTPS and healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and default configuration template
# Copy precompiled native WAA signer service from builder stage
COPY --from=signer-builder /build/waa-signer/bin/waa-signer /app/waa-signer/bin/waa-signer
RUN chmod +x /app/waa-signer/bin/waa-signer

# Copy application code and default configuration template
COPY src/ src/
COPY main.py .
COPY config.yaml /app/defaults/config.yaml
COPY config.yaml /app/config.yaml
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
# Create necessary directories
RUN mkdir -p /app/data /tmp

# Set permissions
RUN chmod +x /app/main.py /app/docker-entrypoint.sh

# Expose API server port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/v1beta/models || exit 1

# Container entrypoint and default command
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python3", "main.py", "server", "--port", "8080"]

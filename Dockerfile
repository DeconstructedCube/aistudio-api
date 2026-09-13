# Use Debian 12 (bookworm) explicitly so package names stay stable.
FROM python:3.11-slim-bookworm

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies and Chromium for headless CDP browser automation
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    fonts-liberation \
    fonts-noto-color-emoji \
    fonts-wqy-zenhei \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/
COPY main.py .

# Create necessary directories
RUN mkdir -p /app/data /tmp

# Set permissions
RUN chmod +x /app/main.py

# Expose ports
# 8080: API server
# 9222: Browser debug port
EXPOSE 8080 9222

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/v1beta/models || exit 1

# Default command
CMD ["python3", "main.py", "server", "--port", "8080", "--browser-port", "9222"]

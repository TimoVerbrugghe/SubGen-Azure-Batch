# SubGen-Azure-Batch Dockerfile
# Lightweight image using Azure Batch Transcription API (no CUDA/GPU required)
# https://github.com/TimoVerbrugghe/subgen-azure-batch

FROM python:3.11-slim

LABEL maintainer="SubGen-Azure-Batch"
LABEL description="Cloud-based subtitle generator using Azure Batch Transcription API"
LABEL version="1.0.0"

WORKDIR /app

# Install system dependencies (FFmpeg for audio extraction)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash subgen \
    && chown -R subgen:subgen /app

USER subgen

# Default port
EXPOSE 9000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SUBGEN_AZURE_BATCH_VERSION=1.0.0 \
    SUBGEN_AZURE_BATCH_PORT=9000 \
    SUBGEN_AZURE_BATCH_HOST=0.0.0.0

# Run with Uvicorn
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]

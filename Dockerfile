# GhostStream - Open Source Transcoding Service
# Multi-stage build for smaller image size and faster builds
# ============================================================================

# Build stage - install dependencies
FROM python:3.11-slim as builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment and install dependencies
COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip wheel && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ============================================================================
# Production base image
# ============================================================================
FROM python:3.11-slim as base

# Labels for container metadata
LABEL org.opencontainers.image.title="GhostStream" \
      org.opencontainers.image.description="Enterprise Transcoding Service" \
      org.opencontainers.image.vendor="GhostStream" \
      org.opencontainers.image.source="https://github.com/ghoststream/ghoststream"

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONOPTIMIZE=2 \
    PATH="/opt/venv/bin:$PATH" \
    GHOSTSTREAM_ENV=production

WORKDIR /app

# Install runtime dependencies only (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean \
    && rm -rf /tmp/* /var/tmp/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY ghoststream/ ./ghoststream/
COPY ghoststream.yaml .

# Create non-root user for security
RUN groupadd -r ghoststream && useradd -r -g ghoststream ghoststream \
    && mkdir -p /app/transcode_temp /app/logs \
    && chown -R ghoststream:ghoststream /app

# Switch to non-root user
USER ghoststream

# Expose port
EXPOSE 8765

# Health check with curl (more reliable than Python in containers)
HEALTHCHECK --interval=15s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8765/api/health || exit 1

# Use tini as init system for proper signal handling
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "ghoststream"]


# ============================================================================
# NVIDIA GPU Support
# ============================================================================
FROM nvidia/cuda:12.2.0-runtime-ubuntu22.04 as nvidia

LABEL org.opencontainers.image.title="GhostStream NVIDIA" \
      org.opencontainers.image.description="Enterprise Transcoding Service with NVIDIA GPU" \
      org.opencontainers.image.vendor="GhostStream"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONOPTIMIZE=2 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    GHOSTSTREAM_ENV=production

WORKDIR /app

# Install Python 3.11, ffmpeg with NVENC, and runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    ffmpeg \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy application
COPY ghoststream/ ./ghoststream/
COPY ghoststream.yaml .

# Create non-root user
RUN groupadd -r ghoststream && useradd -r -g ghoststream ghoststream \
    && mkdir -p /app/transcode_temp /app/logs \
    && chown -R ghoststream:ghoststream /app

USER ghoststream

EXPOSE 8765

HEALTHCHECK --interval=15s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8765/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "ghoststream"]


# ============================================================================
# Intel QSV Support
# ============================================================================
FROM python:3.11-slim as intel

LABEL org.opencontainers.image.title="GhostStream Intel QSV" \
      org.opencontainers.image.description="Enterprise Transcoding Service with Intel QSV" \
      org.opencontainers.image.vendor="GhostStream"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONOPTIMIZE=2 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH" \
    GHOSTSTREAM_ENV=production

WORKDIR /app

# Install ffmpeg with QSV/VAAPI support and runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    intel-media-va-driver-non-free \
    libmfx1 \
    vainfo \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

COPY ghoststream/ ./ghoststream/
COPY ghoststream.yaml .

# Create non-root user
RUN groupadd -r ghoststream && useradd -r -g ghoststream ghoststream \
    && mkdir -p /app/transcode_temp /app/logs \
    && chown -R ghoststream:ghoststream /app

USER ghoststream

EXPOSE 8765

HEALTHCHECK --interval=15s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8765/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "ghoststream"]

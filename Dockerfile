# ============================================================
# tg_file_viewer Dockerfile — multi-stage build
#
# Stage 1: Build Vue 3 + Vite frontend (pnpm)
# Stage 2: Python 3.11 runtime (uv)
# ============================================================

# ── Stage 1: Frontend Build ───────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

RUN corepack enable && corepack prepare pnpm@latest --activate

# Install deps (layer cache: only re-run when lockfile changes)
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Copy sources & build
COPY frontend/ ./
RUN pnpm build

# ── Stage 2: Python Runtime ───────────────────────────────
FROM python:3.11-slim
WORKDIR /app

# System libs required by Pillow (image decoding)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    libpng16-16 \
    libwebp7 \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Python deps (layer cache: only re-run when lockfile changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# Copy application code (respects .dockerignore)
COPY . .

# Copy frontend dist from Stage 1
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Data directory (HF Persistent Storage mount point)
RUN mkdir -p /data

# ── Environment ──────────────────────────────────────────
ENV TG_DATA_DIR=/data
ENV TG_DB_PATH=/data/db.sqlite
ENV TG_LOG_FILE=/data/app.log
# HF Spaces default port; override with env for local/dev
ENV PORT=7860

EXPOSE 7860

# ── Healthcheck ──────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-7860}/api/health')" || exit 1

# ── Start ────────────────────────────────────────────────
CMD ["sh", "-c", "uv run uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"]

FROM python:3.14-slim

# Keep Python output unbuffered and disable pip's cache to shrink layers
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Make uv copy packages into the venv (safer in read-only layers) and avoid
# auto-downloading Python runtimes because a system interpreter already exists
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install uv

COPY . /app

WORKDIR /app/Server

RUN uv sync --locked --no-dev

EXPOSE 8080

ENV PYTHONPATH=/app/Server/src

# ENTRYPOINT allows override via docker run arguments
# Default: stdio transport (Docker MCP Gateway compatible)
# For local HTTP, publish only to 127.0.0.1 and supply the per-launch token.
# For remote hosting, use docker-compose.remote.yml with HTTPS and no backend port publication.
ENTRYPOINT ["uv", "run", "mcp-for-unity"]
CMD []

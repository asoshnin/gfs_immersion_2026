# Google for Startups Immersion 2026 — Knowledge Hub
# Cloud Run container image (free tier: 2M requests/month, 360K vCPU-seconds)
#
# Build context: THE REPOSITORY ROOT (this directory).
# The retrieval core is vendored as ./crawl, so the repo is self-sufficient.
# The SQLite index is NOT committed; stage it into ./data before building.
# Build with:
#   gcloud builds submit --tag "$IMAGE" .        # run from the repo root
# Sprint 09 / design §11: fastembed + onnxruntime + sqlite-vec are runtime
# dependencies; the embedder is lazy-loaded on the first dense search.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    HOST=0.0.0.0 \
    GFS_DB_PATH=/app/data/gfs_immersion_2026.sqlite

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Runtime dependencies (httpx + CPU retrieval stack)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the ONNX embedder into the image (audit DOM4-02 / live regression 2026-11-05).
# Without this the container downloads ~35 MB from HuggingFace on the first dense
# query. Shared Cloud Run egress IPs get HTTP 429 rate-limited, the load fails
# closed, and the service silently serves lexical_only (dense MRR 0.734 -> 0.394).
ENV FASTEMBED_CACHE_PATH=/app/.fastembed_cache \
    HF_HOME=/app/.hf_home
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')" && \
    test -n "$(ls -A /app/.fastembed_cache)" && \
    echo "embedder baked: $(du -sh /app/.fastembed_cache | cut -f1)"

# Bake the Sprint 18 CPU reranker into the image the same way (audit DOM-32).
# The 22.6 MB int8 MiniLM cross-encoder is a BUILD ARTIFACT, not source: it is
# fetched here so it is never committed to the repo (publish boundary intact)
# and the runtime container needs no network. The size cap is enforced at build
# time, so an oversized artifact fails the build rather than silently no-oping.
#
# ORDER MATTERS: this must run BEFORE `HF_HUB_OFFLINE=1` below. The downloader
# uses huggingface_hub, which honors that flag and raises OfflineModeIsEnabled.
# crawl/ and scripts/ are copied further down, so the copy is staged here first.
COPY crawl/ crawl/
COPY scripts/download_reranker.py scripts/download_reranker.py
RUN python scripts/download_reranker.py \
        --model Xenova/ms-marco-MiniLM-L-6-v2 \
        --output /app/crawl/models/reranker \
        --max-mb 150 && \
    echo "reranker baked: $(du -sh /app/crawl/models/reranker | cut -f1)"

# Only NOW go offline: the build may download, the runtime must not.
ENV HF_HUB_OFFLINE=1

# Non-root user
RUN groupadd -g 10001 gfsgroup && \
    useradd -u 10001 -g gfsgroup -s /bin/sh -d /app gfsuser

# Application (published hub)
COPY serve.py .
COPY site/ site/
# Versioned system prompt (Sprint 10 / FR-5). serve.py hashes this file at
# import and reports the digest as prompt_version; without it the image
# silently falls back to the inline emergency prompt.
COPY prompts/ prompts/
# FTS5 database is STAGED into ./data/ before build (see runbook §7)
COPY data/ data/

RUN chown -R gfsuser:gfsgroup /app

USER gfsuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8080/api/health || exit 1

ENTRYPOINT ["python", "serve.py", "--host", "0.0.0.0", "--port", "8080"]

# READ-ONLY Lighter MM collector for Cloud Run Worker Pools
# Multi-stage: build with uv, slim runtime, non-root.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=cloud \
    STRUCTURED_LOGGING=true \
    LIGHTER_MM_NO_DASHBOARD=1 \
    TMP_DIR=/tmp/lighter-mm \
    RUN_TARGET_HOURS=72 \
    BOOK_SAMPLE_INTERVAL_SECONDS=5 \
    PARQUET_ROTATION_MINUTES=15 \
    GCS_UPLOAD_INTERVAL_MINUTES=15

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY pyproject.toml README.md ./

RUN mkdir -p /tmp/lighter-mm && chown -R appuser:appuser /tmp/lighter-mm /app
USER appuser

# Cloud Run Worker Pool runs the container entrypoint (no HTTP port required).
# Official deploy: gcloud run worker-pools deploy ... --image ...
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

ENTRYPOINT ["lighter-mm"]
CMD ["collect"]

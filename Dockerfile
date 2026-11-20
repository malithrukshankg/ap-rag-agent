# Production image (architecture §12). Single uvicorn worker: SQLite allows one writer and the
# idempotency guarantees rely on it (§0.3).
#
# Dependencies come only from requirements.lock with --require-hashes (§7 pin/lock). The project
# itself is not pip-installed: it runs from /app/src via PYTHONPATH, so no unpinned build backend
# (hatchling) is fetched at build time.
FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock

COPY src ./src
COPY config ./config
COPY fixtures ./fixtures

# Fail the build if the corpus does not ingest (registry mismatch, bad headings, etc.). The index
# shares its SQLite file with run state, so the real index is rebuilt by the app at startup into
# /data/ap.db (orchestration/runtime.py); this step only proves the baked-in corpus is valid.
RUN AP_STORAGE__SQLITE_PATH=/tmp/ingest-check.db python -m ap_agent.cli ingest > /dev/null \
    && rm -f /tmp/ingest-check.db*

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown app:app /data
USER app

# /data is the EBS mount on AWS (architecture §12): run state, audit events and JSON logs.
ENV AP_STORAGE__SQLITE_PATH=/data/ap.db \
    AP_LOGGING__PATH=/data/logs/ap.jsonl
VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "ap_agent.api.app:app", "--workers", "1", "--host", "0.0.0.0", "--port", "8000"]

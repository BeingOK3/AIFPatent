ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b
FROM ${PYTHON_IMAGE}

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt /tmp/requirements.txt
RUN python -m pip install --index-url "${PIP_INDEX_URL}" --requirement /tmp/requirements.txt

RUN groupadd --gid 10001 aifpatent \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin aifpatent \
    && mkdir -p \
        /app/data/aifpatent \
        /app/data/langgraph \
        /app/logs \
        /app/workspace/cache \
        /app/workspace/idea-runs \
        /app/workspace/uploads \
    && chown -R 10001:10001 /app

COPY --chown=10001:10001 backend /app/backend
COPY --chown=10001:10001 frontend /app/frontend
COPY --chown=10001:10001 config /app/config

USER 10001:10001

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/api/health', timeout=3).read()"]

CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]

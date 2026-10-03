FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY requirements-production.lock ./requirements-production.lock
RUN python -m pip install --require-hashes -r requirements-production.lock \
    && rm requirements-production.lock

COPY --chown=app:app agents/ ./agents/
COPY --chown=app:app graph/ ./graph/
COPY --chown=app:app mqtt/ ./mqtt/
COPY --chown=app:app rag/ ./rag/
COPY --chown=app:app api.py config.py logger.py storage.py ./

USER app:app
EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/ready', timeout=3)" || exit 1

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]

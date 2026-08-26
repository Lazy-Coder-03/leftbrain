FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app
COPY pyproject.toml README.md LICENSE CHANGELOG.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[server,files,postgres]"

# Non-root
RUN useradd -m leftbrain && chown -R leftbrain /app
USER leftbrain

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz')" || exit 1

# Auth: LEFTBRAIN_API_KEY (single token) and/or a key store via LEFTBRAIN_KEYS_URL / DATABASE_URL
# (postgres://...) or LEFTBRAIN_KEYS_DB (sqlite path). LEFTBRAIN_SERVE_FILES=1 exposes file tools.
CMD ["leftbrain-serve"]

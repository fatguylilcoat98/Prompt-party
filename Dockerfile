FROM python:3.11-slim

# Prompt Party runs as ONE process: SQLite + the in-process SSE hub
# require a single worker. Scale by machine, not by worker count.

RUN useradd --create-home --uid 1000 promptparty
WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY web ./web
COPY config ./config
RUN pip install --no-cache-dir .

RUN mkdir -p /app/data /app/media /app/exports \
    && chown -R promptparty:promptparty /app

USER promptparty
VOLUME ["/app/data", "/app/media", "/app/exports"]
EXPOSE 8710

ENV PROMPT_PARTY_HOST=0.0.0.0 \
    PROMPT_PARTY_PORT=8710 \
    PROMPT_PARTY_ENVIRONMENT=production

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8710/api/health',timeout=4).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8710", "--workers", "1", \
     "--no-access-log", "--log-level", "info"]

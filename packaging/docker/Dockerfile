# Optional development image. Everything here also runs as a normal process: see scripts/dev.
FROM python:3.13-slim
RUN pip install --no-cache-dir uv==0.13.7 && useradd --create-home --uid 10001 amsg
WORKDIR /app
COPY services/api/pyproject.toml services/api/uv.lock ./
COPY services/api/src ./src
COPY services/api/migrations ./migrations
RUN uv sync --frozen --no-dev && mkdir /data && chown amsg:amsg /data
USER amsg
ENV AMSG_DATA_DIR=/data AMSG_HOST=0.0.0.0 AMSG_PORT=8710 PATH=/app/.venv/bin:$PATH
HEALTHCHECK --interval=5s --timeout=3s --retries=20 CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8710/health/live')"
EXPOSE 8710
CMD ["sh", "-c", "exec amsg serve --provider ${AMSG_PROVIDER:-fake} --dev"]

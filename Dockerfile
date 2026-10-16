FROM python:3.15-slim-bookworm@sha256:122756f3fe72c4f5d3d71897888204ee85774bc79de952555b8a474e1d0a5d53 AS dependencies
WORKDIR /build
COPY requirements.lock ./
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r requirements.lock

FROM python:3.15-slim-bookworm@sha256:122756f3fe72c4f5d3d71897888204ee85774bc79de952555b8a474e1d0a5d53
ARG SOURCE_REVISION=local
LABEL org.opencontainers.image.title="Distributed Observability Platform" \
      org.opencontainers.image.source="https://github.com/jorgefprietol/distributed-observability-platform" \
      org.opencontainers.image.revision=$SOURCE_REVISION \
      org.opencontainers.image.licenses="MIT"
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd -g 10001 platform && useradd -u 10001 -g platform -M -s /usr/sbin/nologin platform \
    && mkdir -p /app /logs /data && chown -R platform:platform /app /logs /data
COPY --from=dependencies /opt/venv /opt/venv
WORKDIR /app
COPY --chown=platform:platform platform_app ./platform_app
COPY --chown=platform:platform scripts ./scripts
COPY --chown=platform:platform observability ./observability
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=10 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=2)"
CMD ["uvicorn", "platform_app.app:factory", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

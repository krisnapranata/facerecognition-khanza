# SIMRS Face Recognition — image produksi (CPU, tanpa GPU).
# Satu image dipakai tiga peran (lihat deploy/entrypoint.sh): web | vision | worker.

# ---- tahap build: kompilasi mysqlclient menjadi wheel ----
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip wheel --wheel-dir /wheels -r /tmp/requirements.txt

# ---- tahap runtime ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_VAR_DIR=/data/var \
    MEDIA_PRIVATE_ROOT=/data/media_private \
    STATIC_ROOT=/data/static
# libmariadb3: runtime mysqlclient; libglib2.0-0 & libgomp1: OpenCV headless & ONNX Runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmariadb3 libglib2.0-0 libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin app
COPY --from=build /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/* && rm -rf /wheels

WORKDIR /app
COPY --chown=app:app . /app
RUN mkdir -p /data/var /data/media_private /data/static && chown -R app:app /data \
    && chmod 0755 /app/deploy/entrypoint.sh
USER app

EXPOSE 8000 8001 8765
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["web"]

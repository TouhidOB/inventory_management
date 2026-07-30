# StockWhisk — production image
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime libs: libpq for postgres, curl for the compose healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install -r requirements.txt

# App source
COPY . .

# Non-root runtime user; owns the volumes it writes (static/media).
RUN adduser --system --group --home /app app \
    && mkdir -p /app/staticfiles /app/media \
    && chown -R app:app /app
USER app

# Collected at container start (entrypoint) so it picks up mounted env/volumes.
EXPOSE 8000
ENTRYPOINT ["/app/deploy/docker/entrypoint.sh"]
CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py", "config.wsgi:application"]

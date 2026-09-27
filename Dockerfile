# syntax=docker/dockerfile:1
#
# Production image for astro-ingest: the app is installed into the image and runs on its own (no bind-mounted
# code, no reload). dev/Dockerfile is only for the long-lived dev container. Published to
# ghcr.io/cfmorrell/astro-ingest by .github/workflows/docker-publish.yml; install and mounts: docs/DEPLOY.md.
#
# Mounts (defaults baked in below; override the env vars only if the container paths differ):
#   /astro    the Astronomy share, READ-WRITE: frames are filed here (ASTRO_ROOT = ASTRO_NAS)
#   /staging  STAGING_DIR: frames read once from the ASIAIR, until filed (its own share, not the Astronomy share)
#   /cache    CACHE_DIR: disposable preview renders (appdata)
# App state (answers, batches, logs) lives on the share in /astro/Z95-ClaudeReferences/ingest (CLAUDE.md decision 4).
# The ASIAIR is found on the home network and reached over SMB; nothing needs mounting for it.

FROM python:3.12-slim-bookworm

# tini: a proper PID 1 (signals, zombies); tzdata: zoneinfo for night dates (decision 11)
RUN apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends tini tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY astro_ingest/ astro_ingest/
RUN pip install --no-cache-dir . && rm -rf /root/.cache

ENV ASTRO_ROOT=/astro \
    ASTRO_NAS=/astro \
    STATE_DIR=/astro/Z95-ClaudeReferences/ingest \
    STAGING_DIR=/staging \
    CACHE_DIR=/cache \
    TZ=America/New_York \
    ALLOW_DEVICE_DELETE=0 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# UnRAID's nobody:users, so everything the app writes on the shares is owned like the rest of the array.
# /cache and /staging exist (owned by that user) so the app still works if one of them isn't mounted.
RUN mkdir -p /cache /staging && chown 99:100 /cache /staging
USER 99:100
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health?device=0', timeout=8)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["astro-ingest", "serve", "--host", "0.0.0.0", "--port", "8000"]

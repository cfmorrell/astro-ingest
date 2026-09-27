#!/bin/bash
# Run the published astro-ingest image on UnRAID without the template (docs/DEPLOY.md). Run from the UnRAID console.
# ALLOW_DEVICE_DELETE=1 lets Clean up delete from the ASIAIR: set it once your backups are in place.
set -euo pipefail
NAME=astro-ingest
IMAGE=${IMAGE:-ghcr.io/cfmorrell/astro-ingest:latest}
PORT=${PORT:-8091}
ALLOW_DEVICE_DELETE=${ALLOW_DEVICE_DELETE:-0}

mkdir -p /mnt/user/docker_appdata/astro-ingest/cache
chown -R 99:100 /mnt/user/docker_appdata/astro-ingest     # the container runs as nobody:users
docker pull "$IMAGE"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped \
  -e TZ=America/New_York \
  -e ALLOW_DEVICE_DELETE="$ALLOW_DEVICE_DELETE" \
  -v /mnt/user/Astronomy:/astro \
  -v /mnt/user/astro-ingest-staging:/staging \
  -v /mnt/user/docker_appdata/astro-ingest/cache:/cache \
  -p "$PORT":8000 \
  "$IMAGE"
echo "Started: http://$(hostname):$PORT"

#!/bin/bash
# (Re)build and start the astro-ingest dev container on UnRAID. Run from the UnRAID console.
set -euo pipefail
BASE=/mnt/user/docker_appdata/astro-ingest-dev
NAME=astro-ingest-dev
# App config (ASTRO_ROOT, STATE_DIR, ASIAIR_*, TZ, ...) comes from the repo's .env; seed it from .env.example.
ENV_FILE="$BASE/src/.env"
[ -f "$ENV_FILE" ] || cp "$BASE/src/.env.example" "$ENV_FILE"
docker build -t ${NAME} "$BASE/src/dev"
docker rm -f ${NAME} >/dev/null 2>&1 || true
docker run -d --name ${NAME} --restart unless-stopped \
  --env-file "$ENV_FILE" \
  --shm-size=1g \
  -v "$BASE/src":/workspace \
  -v "$BASE/home":/home/dev \
  -v /mnt/user/Astronomy:/astro:ro \
  -v /mnt/user/astro-sandbox:/astro-sandbox \
  -v /mnt/user/astro-ingest-staging:/staging \
  -e STAGING_DIR=/staging \
  -v /mnt/remotes/ASIAIR:/asiair:ro,rslave \
  -p 8090:8000 \
  ${NAME}
echo "Started. Shell in with:  docker exec -it ${NAME} bash"

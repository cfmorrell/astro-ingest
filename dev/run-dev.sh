#!/bin/bash
# (Re)build and start the astro-ingest dev container on UnRAID. Run from the UnRAID console.
set -euo pipefail
BASE=/mnt/user/docker_appdata/astro-ingest-dev
NAME=astro-ingest-dev
docker build -t ${NAME} "$BASE/src/dev"
docker rm -f ${NAME} >/dev/null 2>&1 || true
docker run -d --name ${NAME} --restart unless-stopped \
  -e TZ=America/New_York \
  -v "$BASE/src":/workspace \
  -v "$BASE/home":/home/dev \
  -v /mnt/user/Astronomy:/astro:ro \
  -v /mnt/user/astro-sandbox:/astro-sandbox \
  -v /mnt/remotes/ASIAIR:/asiair:ro,rslave \
  -p 8090:8000 \
  ${NAME}
echo "Started. Shell in with:  docker exec -it ${NAME} bash"

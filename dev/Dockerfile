# Dev container for astro-ingest. Runs as uid 99 / gid 100 (UnRAID nobody:users) so files it
# creates on the shares have the same ownership as everything else on the array.
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
        git openssh-client curl ca-certificates ripgrep less nano procps tini tzdata vim tmux\
    && rm -rf /var/lib/apt/lists/*
RUN useradd -u 99 -g 100 -m -d /home/dev -s /bin/bash dev
ENV PATH=/home/dev/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER dev
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["sleep","infinity"]
# Claude Code is installed at first run into /home/dev (a persisted volume), not baked into the
# image, so its auto-updates and login survive image rebuilds. See docs/SETUP.md step 5.

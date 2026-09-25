# Dev container for astro-ingest. Runs as uid 99 / gid 100 (UnRAID nobody:users) so files it
# creates on the shares have the same ownership as everything else on the array.
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
        git openssh-client curl ca-certificates ripgrep less nano procps tini tzdata vim tmux\
    && rm -rf /var/lib/apt/lists/*
# Headless Chromium for Claude to view the web UI (dev/screenshot.py). Browsers and their system libraries live
# in the image at a fixed path; the Playwright version here must match the one pinned in pyproject's dev extras.
ARG PLAYWRIGHT_VERSION=1.63.0
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}" \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /opt/ms-playwright \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -u 99 -g 100 -m -d /home/dev -s /bin/bash dev
# Login shells (bash -l, UnRAID's console button) reset PATH in /etc/profile; profile.d runs after that,
# so ~/.local/bin (where Claude Code is installed) stays on PATH in every kind of shell.
RUN echo 'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac' \
        > /etc/profile.d/local-bin.sh
ENV PATH=/home/dev/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER dev
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["sleep","infinity"]
# Claude Code is installed at first run into /home/dev (a persisted volume), not baked into the
# image, so its auto-updates and login survive image rebuilds. See docs/SETUP.md step 5.

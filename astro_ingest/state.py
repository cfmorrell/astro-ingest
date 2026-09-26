"""Small JSON state files in STATE_DIR (answers, frame-quality stats), written atomically.

Phase 4 moves decisions and operations into SQLite; these stay plain JSON so a Claude session can read them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from astro_ingest.config import Config


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write_json(cfg: Config, path: Path, data) -> None:
    """Write via `<name>.part` then rename, inside STATE_DIR/CACHE_DIR only (config.check_writable)."""
    cfg.check_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    os.replace(part, path)  # app state is meant to be updated in place; share data never is


def write_bytes(cfg: Config, path: Path, data: bytes) -> None:
    cfg.check_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)

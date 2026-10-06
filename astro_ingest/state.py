"""Small JSON state files in STATE_DIR (answers, frame-quality stats), written atomically.

Phase 4 moves decisions and operations into SQLite; these stay plain JSON so a Claude session can read them.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from astro_ingest.config import Config


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def _part(path: Path) -> Path:
    # a temporary name of its own for every write: two requests writing the same file at once (two windows, or a
    # session save racing Start over) must not rename each other's .part away (found by the ZAP scan, 2026-10-06)
    return path.with_name(f"{path.name}.{os.getpid()}-{uuid.uuid4().hex[:12]}.part")


def write_json(cfg: Config, path: Path, data) -> None:
    """Write via `<name>.<unique>.part` then rename, inside STATE_DIR/CACHE_DIR only (config.check_writable)."""
    cfg.check_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = _part(path)
    try:
        part.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        os.replace(part, path)  # app state is meant to be updated in place; share data never is
    finally:
        part.unlink(missing_ok=True)


def write_bytes(cfg: Config, path: Path, data: bytes) -> None:
    cfg.check_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = _part(path)
    try:
        part.write_bytes(data)
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)

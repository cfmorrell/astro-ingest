"""The only code that writes to the Astronomy share.

Rules (CLAUDE.md): never overwrite (a clash is skipped and reported), never delete inside the share (a damaged copy
is retired to `_to_delete/` in its own folder), write `<name>.part` then rename, never create dotfiles
(.DS_Store, ._*), verify every copy by checksum, and only one writer at a time (STATE_DIR/ingest.lock).
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import socket
import time
from pathlib import Path
from typing import Callable

from astro_ingest.config import Config

CHUNK = 4 << 20
STALE_LOCK_S = 12 * 3600


class LockHeld(Exception):
    pass


class WriteLock:
    """STATE_DIR/ingest.lock, created with O_EXCL (works on the shfs mount, unlike flock). Records who holds it;
    a lock whose process is gone (same host) or that is older than 12 h is treated as stale and replaced."""

    def __init__(self, cfg: Config, holder: str):
        self.path = Path(cfg.state_dir) / "ingest.lock"
        self.cfg, self.holder = cfg, holder

    def __enter__(self):
        self.cfg.check_writable(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if self._stale():
                    self.path.unlink(missing_ok=True)
                    continue
                raise LockHeld(f"another writer holds {self.path}: {self.path.read_text().strip()}") from None
            with os.fdopen(fd, "w") as f:
                json.dump({"holder": self.holder, "pid": os.getpid(), "host": socket.gethostname(),
                           "since": time.time()}, f)
            return self
        raise LockHeld(f"could not take {self.path}")

    def _stale(self) -> bool:
        try:
            info = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return True
        if time.time() - info.get("since", 0) > STALE_LOCK_S:
            return True
        if info.get("host") == socket.gethostname():
            try:
                os.kill(info["pid"], 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
        return False

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _check_name(rel: str) -> None:
    for part in rel.split("/"):
        if not part or part in (".", "..") or part.startswith("."):
            raise ValueError(f"refusing to write a dotfile or odd path: {rel}")


def hash_file(path: Path) -> str:
    h = hashlib.blake2b()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def count_files(folder: Path) -> int:
    """Regular files directly in `folder` (ignoring .part leftovers), 0 if it doesn't exist."""
    try:
        return sum(1 for e in os.scandir(folder) if e.is_file(follow_symlinks=False) and not e.name.endswith(".part"))
    except FileNotFoundError:
        return 0


def copy_verified(cfg: Config, staged: Path, dst_rel: str, size: int, blake2b: str,
                  on_bytes: Callable[[int], None] | None = None) -> tuple[str, str]:
    """Copy a staged file to ASTRO_ROOT/dst_rel, verified against its staging hash.

    Returns (status, detail): "done", "already-there" (same size and hash: a harmless rerun), "clash" (something
    else is there: never overwritten) or "failed".
    """
    _check_name(dst_rel)
    dst = Path(cfg.astro_root) / dst_rel
    cfg.check_writable(dst)
    if os.path.lexists(dst):
        if dst.is_file() and not dst.is_symlink() and dst.stat().st_size == size and hash_file(dst) == blake2b:
            return "already-there", "identical file already in place"
        return "clash", "a different file already has this name; not overwritten"
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    part.unlink(missing_ok=True)  # a leftover from an interrupted run
    try:
        with open(staged, "rb") as src, open(part, "xb") as out:
            while chunk := src.read(CHUNK):
                out.write(chunk)
                if on_bytes:
                    on_bytes(len(chunk))
            out.flush()
            os.fsync(out.fileno())
        # verify what actually landed on disk, not what was in memory
        h = hash_file(part)
        if h != blake2b or part.stat().st_size != size:
            part.unlink(missing_ok=True)
            return "failed", "the copy on disk doesn't match the staging checksum"
        _rename_no_clobber(part, dst)
    except FileExistsError:
        part.unlink(missing_ok=True)
        return "clash", "a file appeared at the destination during the copy; not overwritten"
    except OSError as exc:
        part.unlink(missing_ok=True)
        return "failed", f"{type(exc).__name__}: {exc}"
    if dst.stat().st_size != size:
        return "failed", "size changed after rename"
    return "done", "copied and verified"


def _rename_no_clobber(part: Path, dst: Path) -> None:
    """Atomic and never overwrites: hard-link the .part to its final name (fails if the name exists), then remove the
    .part. Shares without hard links fall back to check-then-rename, safe under the single-writer lock."""
    try:
        os.link(part, dst)
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EXDEV, errno.EMLINK, errno.EOPNOTSUPP):
            raise
        if os.path.lexists(dst):
            raise FileExistsError(dst) from exc
        os.rename(part, dst)
        return
    part.unlink()


def retire(cfg: Config, rel: str) -> tuple[str, str]:
    """Move ASTRO_ROOT/rel into `_to_delete/` in its own folder (never delete, never overwrite)."""
    _check_name(rel)
    src = Path(cfg.astro_root) / rel
    if not os.path.lexists(src):
        return "skipped", "not in the write root (nothing to retire there)"
    dst = src.parent / "_to_delete" / src.name
    cfg.check_writable(dst)
    if os.path.lexists(dst):
        return "clash", f"{dst.parent.name}/{dst.name} already exists; not overwritten"
    dst.parent.mkdir(exist_ok=True)
    os.rename(src, dst)
    return "done", f"retired to {dst.parent.name}/"

"""Staging: read each selected frame from the capture device exactly once, then work from the local copy.

Over Wi-Fi the ASIAIR delivers ~10 MB/s, so every extra read of a 52 MB frame costs ~5 s. Staging streams each
selected frame into STAGING_DIR (its own UnRAID share in production, outside the Astronomy share), computing BLAKE2b
on the way in, writes it atomically (.part, fsync, rename) and re-reads it to confirm the write. Scoring, previews
and the copy onto the NAS (phase 4b) then read the staged copy at disk speed through StagedSource.

The device is only ever read. The manifest (STATE_DIR/staging.json) records what was staged, with the source size
and mtime it was read at (phase 6 re-checks those before deleting anything from the device) and its hash (phase 4b
verifies the NAS copy against it).
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from astro_ingest import state
from astro_ingest.config import Config
from astro_ingest.core import planner as P
from astro_ingest.sources.base import Source, SourceEntry

MANIFEST = "staging.json"
CHUNK = 4 << 20
# What the Select step offers and the Stage step reads: frames the plan will (or may, pending a decision or the
# quality review that follows staging) ingest. Quality-rejected frames are included: scoring happens after staging.
STAGEABLE = (P.COPY, P.APPEND, P.PENDING, P.REJECTED)


def device_slug(device: dict | None, local: bool) -> str:
    """Folder name under STAGING_DIR for this device ('ASIAIR-Color', or 'local' for a local-folder source)."""
    if local or not device:
        return "local"
    if device.get("slug"):
        return device["slug"]    # fixed at first connect: renaming a device never moves its data
    name = device.get("nickname") or f"{device.get('label', 'device')}-{device.get('host', '')}"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "device"


class StagingStore:
    """STATE_DIR/staging.json: {"files": {slug|rel: {size, mtime, blake2b, staged_at}}, "rate": {...}}."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.path = Path(cfg.state_dir) / MANIFEST
        data = state.read_json(self.path, {})
        self.files: dict[str, dict] = data.get("files", {})
        self.rate: dict | None = data.get("rate")

    @staticmethod
    def key(slug: str, rel: str) -> str:
        return f"{slug}|{rel}"

    def staged_path(self, slug: str, rel: str) -> Path:
        parts = [p for p in rel.split("/") if p]
        if any(p in (".", "..") for p in parts):
            raise ValueError(f"bad path: {rel}")
        return Path(self.cfg.staging_dir, slug, *parts)

    def get(self, slug: str, entry: SourceEntry) -> dict | None:
        """The manifest record if this exact source file (same size and mtime) is staged and still on disk."""
        rec = self.files.get(self.key(slug, entry.rel))
        if not rec or rec["size"] != entry.size or int(rec["mtime"]) != int(entry.mtime):
            return None
        p = self.staged_path(slug, entry.rel)
        return rec if p.is_file() and p.stat().st_size == rec["size"] else None

    def seen(self, slug: str, entry: SourceEntry) -> bool:
        """This exact source file was staged at some point (even if its staged copy has since been cleared)."""
        rec = self.files.get(self.key(slug, entry.rel))
        return bool(rec) and rec["size"] == entry.size and int(rec["mtime"]) == int(entry.mtime)

    def save(self) -> None:
        state.write_json(self.cfg, self.path, {"version": 1, "files": self.files, "rate": self.rate})


def _hash_file(path: Path) -> str:
    h = hashlib.blake2b()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


class StagingError(Exception):
    pass


def stage_one(cfg: Config, source: Source, store: StagingStore, slug: str, entry: SourceEntry,
              on_bytes: Callable[[int], None] | None = None) -> dict:
    """Stream one file from the source into staging; verify; record. Returns the manifest record."""
    dst = store.staged_path(slug, entry.rel)
    cfg.check_writable(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    h = hashlib.blake2b()
    n = 0
    with source.open_read(entry.rel) as src, open(part, "wb") as out:
        while chunk := src.read(CHUNK):
            h.update(chunk)
            out.write(chunk)
            n += len(chunk)
            if on_bytes:
                on_bytes(len(chunk))
        out.flush()
        os.fsync(out.fileno())
    digest = h.hexdigest()
    if n != entry.size:
        part.unlink()
        raise StagingError(f"{entry.rel}: read {n} bytes, the device listed {entry.size}")
    os.replace(part, dst)  # staging is our own scratch space: replacing a stale copy of the same file is fine
    if _hash_file(dst) != digest:
        dst.unlink()
        raise StagingError(f"{entry.rel}: the staged copy doesn't match what was read (disk problem?)")
    rec = {"size": entry.size, "mtime": entry.mtime, "blake2b": digest, "staged_at": time.time()}
    store.files[store.key(slug, entry.rel)] = rec
    return rec


def run_staging(cfg: Config, source: Source, slug: str, entries: list[SourceEntry], measure_rate: bool,
                progress: Callable[..., None]) -> dict:
    """Stage every entry not already staged. Stops at the first read error (device gone): run again to resume."""
    store = StagingStore(cfg)
    todo = [e for e in entries if store.get(slug, e) is None]
    total = sum(e.size for e in todo)
    done_bytes, started = 0, time.monotonic()
    staged = 0

    def on_bytes(k: int) -> None:
        nonlocal done_bytes
        done_bytes += k

    def report(n_done: int, message: str) -> None:
        elapsed = time.monotonic() - started
        rate = done_bytes / elapsed / 1e6 if elapsed > 1 and done_bytes else None
        eta = (total - done_bytes) / 1e6 / rate if rate else None
        progress(done_bytes / total * 100.0 if total else 100.0, message, files_done=n_done, files_total=len(todo),
                 bytes_done=done_bytes, bytes_total=total, mb_s=round(rate, 2) if rate else None,
                 eta_s=round(eta) if eta is not None else None)

    for n, e in enumerate(todo, 1):
        report(n - 1, f"staging {e.rel} ({n}/{len(todo)})")
        try:
            stage_one(cfg, source, store, slug, e, on_bytes)
            staged += 1
        except StagingError:
            store.save()
            raise
        except OSError as exc:
            store.save()
            raise StagingError(f"reading {e.rel} failed ({type(exc).__name__}: {exc}); "
                               f"{staged} file(s) staged so far; run Stage again to continue") from exc
        if n % 10 == 0:
            store.save()
    report(staged, f"staged {staged} file(s)")
    seconds = time.monotonic() - started
    if measure_rate and done_bytes > 50e6 and seconds > 0:
        store.rate = {"mb_s": round(done_bytes / seconds / 1e6, 2), "measured_at": time.time(),
                      "bytes": done_bytes, "seconds": round(seconds, 1)}
    store.save()
    return {"staged": staged, "already_staged": len(entries) - len(todo), "bytes": done_bytes,
            "seconds": round(seconds, 1), "mb_s": round(done_bytes / seconds / 1e6, 2) if seconds and done_bytes else None}


class StagedSource:
    """The device source, but reading the staged copy of any file that has one (after staging, nothing goes back
    over Wi-Fi). Listing always comes from the device."""

    def __init__(self, source: Source, cfg: Config, slug: str, local: bool):
        self.source, self.cfg, self.slug, self.local = source, cfg, slug, local
        self.label = source.label
        self.layout = getattr(source, "layout", "asiair")          # "folder": any layout (sources/upload.py)
        self.deletable = getattr(source, "deletable", True)
        if hasattr(source, "header_for"):
            self.header_for = source.header_for
        self.store = StagingStore(cfg)
        self._entries: dict[str, SourceEntry] = {}

    def walk(self) -> Iterator[SourceEntry]:
        for e in self.source.walk():
            self._entries[e.rel] = e
            yield e

    def reload(self) -> None:
        self.store = StagingStore(self.cfg)

    def staged(self, rel: str) -> bool:
        e = self._entries.get(rel)
        return bool(e and self.store.get(self.slug, e))

    def is_fast(self, rel: str) -> bool:
        """Readable without going over the device's link: staged, or the source is a local folder."""
        return self.local or self.staged(rel)

    def open_read(self, rel: str) -> BinaryIO:
        if self.staged(rel):
            return open(self.store.staged_path(self.slug, rel), "rb")
        return self.source.open_read(rel)

    # Clean up talks to the device itself, never to staging
    def stat(self, rel: str):
        return self.source.stat(rel)

    def delete(self, rel: str) -> None:
        self.source.delete(rel)

    def rmdir(self, rel: str) -> None:
        self.source.rmdir(rel)

    def listdir(self, rel: str) -> list[str]:
        return self.source.listdir(rel)


def needs_staging(store: StagingStore, slug: str, entry: SourceEntry, action: str) -> bool:
    """Does the Stage step have to read this frame (again)?

    A frame going to the NAS needs a staged copy on disk. A quality-rejected frame only needed reading once to be
    scored: after its staged copy is cleared it must not be pulled over Wi-Fi again (unless Chris keeps it, which
    turns it back into a copy that needs its file)."""
    if action not in STAGEABLE or store.get(slug, entry):
        return False
    return not (action == P.REJECTED and store.seen(slug, entry))

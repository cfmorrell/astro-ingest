"""A folder on the computer running the browser (e.g. a NINA session on the capture PC), as a capture source.

The app runs on the server and can't see that computer's disk, so the browser does the reading: the user picks a
folder, the page finds every FITS file under it and sends a manifest (paths, sizes, times, and each file's raw header
blocks), and Stage uploads the selected frames into staging, checksummed on the way in (upload_file). From there it's
the usual flow. Frames on the user's computer are never deleted: Clean up doesn't apply (`deletable` is False).
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import time
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

from astro_ingest import state
from astro_ingest.config import Config
from astro_ingest.core.fits import HeaderError, read_fits_header
from astro_ingest.sources.base import DeleteRefused, SourceEntry

UPLOADS = "uploads"
MAX_HEADER_BYTES = 2880 * 36


def slug_for(name: str) -> str:
    return "upload-" + (re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "folder")


def safe_rel(rel: str) -> str:
    """A relative POSIX path inside the folder (no absolute paths, no '..', no backslashes)."""
    rel = rel.replace("\\", "/").strip("/")
    parts = PurePosixPath(rel).parts
    if not rel or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"bad path: {rel!r}")
    return "/".join(parts)


def manifest_path(cfg: Config, slug: str) -> Path:
    return Path(cfg.state_dir) / UPLOADS / f"{slug}.json"


def save_manifest(cfg: Config, name: str, files: list[dict]) -> dict:
    """Record what the browser found: {rel: {size, mtime, header}} (the header parsed from its raw blocks)."""
    slug = slug_for(name)
    out = {}
    for f in files:
        rel = safe_rel(f["rel"])
        try:
            header = read_fits_header(io.BytesIO(f.get("header_raw", "").encode("latin-1")[:MAX_HEADER_BYTES]
                                                 .ljust(2880, b" ")))
        except HeaderError:
            header = None
        out[rel] = {"size": int(f["size"]), "mtime": float(f["mtime"]), "header": header}
    record = {"name": name, "slug": slug, "created": time.time(), "files": out}
    state.write_json(cfg, manifest_path(cfg, slug), record)
    return record


class UploadSource:
    layout = "folder"     # scan: any layout, frames from their headers (core/inference.py)
    deletable = False     # never delete files on the user's computer
    local = False

    def __init__(self, cfg: Config, slug: str):
        self.cfg, self.slug = cfg, slug
        data = state.read_json(manifest_path(cfg, slug), None)
        if data is None:
            raise FileNotFoundError(f"no folder manifest {slug}: pick the folder again")
        self.name = data["name"]
        self.files: dict[str, dict] = data["files"]
        self.label = f"upload:{self.name}"

    def walk(self) -> Iterator[SourceEntry]:
        for rel in sorted(self.files):
            f = self.files[rel]
            yield SourceEntry(rel, f["size"], f["mtime"])

    def header_for(self, rel: str) -> dict[str, str]:
        h = self.files[rel]["header"]
        if h is None:
            raise HeaderError("no readable FITS header")
        return h

    def staged_path(self, rel: str) -> Path:
        return Path(self.cfg.staging_dir, self.slug, *PurePosixPath(rel).parts)

    def open_read(self, rel: str) -> BinaryIO:
        p = self.staged_path(rel)
        if not p.is_file():
            raise OSError(f"{rel} isn't uploaded yet")
        return open(p, "rb")

    def stat(self, rel: str) -> SourceEntry | None:
        f = self.files.get(rel)
        return SourceEntry(rel, f["size"], f["mtime"]) if f else None

    def delete(self, rel: str) -> None:
        raise DeleteRefused("files on your computer are never deleted by astro-ingest")

    def rmdir(self, rel: str) -> None:
        raise DeleteRefused("files on your computer are never deleted by astro-ingest")

    def listdir(self, rel: str) -> list[str]:
        return []

    def close(self) -> None:
        pass


def begin_upload(cfg: Config, source: UploadSource, rel: str) -> tuple[str, Path, Path, dict]:
    """(rel, part path, final path, manifest entry) for one frame about to be uploaded into staging."""
    rel = safe_rel(rel)
    want = source.files.get(rel)
    if want is None:
        raise KeyError(rel)
    dst = source.staged_path(rel)
    cfg.check_writable(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    return rel, dst.with_name(dst.name + ".part"), dst, want


def finish_upload(cfg: Config, source: UploadSource, rel: str, part: Path, dst: Path, want: dict, n: int,
                  digest: str) -> dict:
    """Check the size against the folder's listing, rename into place, and record it like any staged frame."""
    from astro_ingest.staging import StagingStore
    if n != want["size"]:
        part.unlink(missing_ok=True)
        raise ValueError(f"{rel}: received {n} bytes, the folder listed {want['size']}")
    os.replace(part, dst)
    store = StagingStore(cfg)
    store.files[store.key(source.slug, rel)] = {"size": n, "mtime": want["mtime"], "blake2b": digest,
                                                "staged_at": time.time()}
    store.save()
    return {"rel": rel, "size": n, "blake2b": digest}


def upload_file(cfg: Config, source: UploadSource, rel: str, stream, chunk: int = 4 << 20) -> dict:
    """Write one uploaded frame into staging (`.part`, BLAKE2b on the way in, size checked against the manifest, then
    renamed) and record it in the staging manifest, exactly as staging from a device does."""
    rel, part, dst, want = begin_upload(cfg, source, rel)
    h, n = hashlib.blake2b(), 0
    with open(part, "wb") as out:
        while data := stream.read(chunk):
            h.update(data)
            n += len(data)
            out.write(data)
        out.flush()
        os.fsync(out.fileno())
    return finish_upload(cfg, source, rel, part, dst, want, n, h.hexdigest())


def load(cfg: Config, slug: str) -> UploadSource:
    return UploadSource(cfg, slug)


def device_record(name: str) -> dict:
    """How a folder appears among the recent devices (Connect)."""
    return {"kind": "upload", "label": "Folder on this computer", "host": f"upload:{name}", "share": "",
            "server_guid": "", "name": None, "folders": [], "slug": slug_for(name)}


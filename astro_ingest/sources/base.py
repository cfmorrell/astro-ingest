"""The Source interface: list, read and (for the Clean up step only) delete files on the capture device."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, Iterator, Protocol

from astro_ingest.core import asiair


@dataclass(frozen=True)
class SourceEntry:
    rel: str      # POSIX path relative to the share root, e.g. "Plan/Light/M 13/Light_M 13_....fit"
    size: int
    mtime: float  # seconds since the epoch


class Source(Protocol):
    label: str  # shown in the UI and logs, e.g. "local:/astro-sandbox/_asiair-sample" or "smb://192.168.1.43/EMMC Images"

    def walk(self) -> Iterator[SourceEntry]:
        """Every regular file, in a stable order, including dotfiles and OS junk (the scan decides what they are).
        Never follows or lists symlinks."""
        ...

    def open_read(self, rel: str) -> BinaryIO:
        ...

    def stat(self, rel: str) -> SourceEntry | None:
        """The file's current size and mtime, or None if it's gone."""
        ...

    def delete(self, rel: str) -> None:
        """Delete one file. Only cleanup.py calls this, for paths in an approved clean-up snapshot."""
        ...

    def rmdir(self, rel: str) -> None:
        """Remove one empty folder."""
        ...

    def listdir(self, rel: str) -> list[str]:
        """Names in a folder (empty list if it doesn't exist)."""
        ...


class DeleteRefused(Exception):
    pass


def check_deletable(rel: str) -> None:
    """Only files under the capture folders (Autorun/, Plan/) can ever be deleted: never Live, Preview, Video, log,
    GuidingDarkLibrary or anything unknown (decision 8), and never an odd path."""
    parts = PurePosixPath(rel).parts
    if not rel or rel.startswith("/") or any(p in ("", ".", "..") for p in parts) or "\\" in rel:
        raise DeleteRefused(f"odd path: {rel!r}")
    if asiair.folder_category(rel) != "handled" or len(PurePosixPath(asiair.split_storage(rel)[1]).parts) < 2:
        raise DeleteRefused(f"outside the capture folders: {rel}")


# Folders the ASIAIR creates itself: never removed even when empty (object folders under Light/ may go)
STRUCTURAL = {"Autorun", "Plan", "Autorun/Light", "Autorun/Flat", "Autorun/Dark", "Autorun/Bias", "Plan/Light",
              "Plan/Flat", "Plan/Dark", "Plan/Bias"}


def check_removable_dir(rel: str) -> None:
    check_deletable(rel)
    inner = asiair.split_storage(rel)[1]     # the same rules on the SD card and a USB drive
    if inner in STRUCTURAL or inner.count("/") != 2 or not inner.split("/")[1] == "Light":
        raise DeleteRefused(f"not an object folder: {rel}")

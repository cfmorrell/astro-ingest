"""The Source interface: list and read files on the capture device (delete arrives with cleanup, phase 6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Iterator, Protocol


@dataclass(frozen=True)
class SourceEntry:
    rel: str      # POSIX path relative to the share root, e.g. "Plan/Light/M 13/Light_M 13_....fit"
    size: int
    mtime: float  # seconds since the epoch


class Source(Protocol):
    label: str  # shown in the UI and logs, e.g. "local:/astro-sandbox/_asiair-sample" or "smb://192.168.1.43/EMMC Images"

    def walk(self) -> Iterator[SourceEntry]:
        """Every regular file, in a stable order. Skips symlinks, dotfiles and OS junk."""
        ...

    def open_read(self, rel: str) -> BinaryIO:
        ...

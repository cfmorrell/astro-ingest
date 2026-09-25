"""A local directory as the source: the sandbox sample, a scratch copy, or a kernel SMB mount."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO, Iterator

from astro_ingest.sources.base import SourceEntry


class LocalDirSource:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.label = f"local:{self.root}"

    def walk(self) -> Iterator[SourceEntry]:
        for dirpath, dirnames, filenames in os.walk(self.root):  # never follows symlinked dirs
            dirnames.sort()
            for name in sorted(filenames):
                path = Path(dirpath, name)
                st = path.lstat()
                if not path.is_file() or path.is_symlink():
                    continue
                yield SourceEntry(path.relative_to(self.root).as_posix(), st.st_size, st.st_mtime)

    def open_read(self, rel: str) -> BinaryIO:
        return open(self._path(rel), "rb")

    def _path(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"path escapes the source root: {rel}")
        return path

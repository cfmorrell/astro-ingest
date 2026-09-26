"""A local directory as the source: the sandbox sample, a scratch copy, or a kernel SMB mount."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO, Iterator

from astro_ingest.sources.base import SourceEntry, check_deletable, check_removable_dir


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

    def stat(self, rel: str) -> SourceEntry | None:
        path = self._path(rel)
        if not path.is_file() or path.is_symlink():
            return None
        st = path.lstat()
        return SourceEntry(rel, st.st_size, st.st_mtime)

    def delete(self, rel: str) -> None:
        check_deletable(rel)
        os.remove(self._path(rel))

    def rmdir(self, rel: str) -> None:
        check_removable_dir(rel)
        os.rmdir(self._path(rel))

    def listdir(self, rel: str) -> list[str]:
        try:
            return sorted(os.listdir(self._path(rel)))
        except FileNotFoundError:
            return []

    def _path(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"path escapes the source root: {rel}")
        return path

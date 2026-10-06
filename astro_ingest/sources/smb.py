"""The capture device's SMB share as a Source (guest access, read-only in this phase).

Only listing, stat and reading exist here; there is no write or delete method at all. Deleting from the device
arrives in phase 6, behind checksum-verified copies and Chris's approval.
"""

from __future__ import annotations

import threading
from typing import BinaryIO, Iterator

from astro_ingest.sources.base import SourceEntry, check_deletable, check_removable_dir

_config_lock = threading.Lock()
_configured = False


def _configure() -> None:
    """Guest sessions have no signing key, so SMB3's secure-negotiate check must be off (a process-wide smbclient
    setting; the app only ever talks to capture devices over SMB)."""
    global _configured
    with _config_lock:
        if not _configured:
            import smbclient
            smbclient.ClientConfig(require_secure_negotiate=False, skip_dfs=True)
            _configured = True


# "not there" as different SMB servers say it (Samba: OBJECT_NAME_NOT_FOUND; others: NO_SUCH_FILE, ...)
_NOT_FOUND = {0xC0000034, 0xC000003A, 0xC000000F}


def _not_found(e: OSError) -> bool:
    return isinstance(e, FileNotFoundError) or (getattr(e, "ntstatus", None) or 0) & 0xFFFFFFFF in _NOT_FOUND


class SmbSource:
    def __init__(self, host: str, share: str, timeout: float = 15.0, port: int = 445):
        import smbclient
        _configure()
        self.host, self.share, self.port = host, share, port  # port: 445 on the ASIAIR; tests use a high port
        self.label = f"smb://{host}/{share}"
        self._root = rf"\\{host}\{share}"
        self._cache: dict = {}  # this source's own connection cache
        smbclient.register_session(host, username="guest", password="", auth_protocol="ntlm", require_signing=False,
                                   connection_timeout=timeout, connection_cache=self._cache, port=port)

    def _path(self, rel: str) -> str:
        parts = [p for p in rel.split("/") if p]
        if any(p in (".", "..") for p in parts):
            raise ValueError(f"path escapes the share: {rel}")
        return "\\".join([self._root, *parts])

    def walk(self, start: str = "") -> Iterator[SourceEntry]:
        """Every file under `start` (the share's top by default); paths stay relative to the share."""
        import smbclient
        stack = [start.strip("/")]
        while stack:
            rel_dir = stack.pop()
            entries = sorted(smbclient.scandir(self._path(rel_dir), connection_cache=self._cache, port=self.port),
                             key=lambda e: e.name)
            subdirs = []
            for e in entries:
                if e.name in (".", ".."):
                    continue
                rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
                if e.is_symlink():
                    continue  # never follow links (none expected on the ASIAIR)
                if e.is_dir():
                    subdirs.append(rel)
                elif e.is_file():
                    st = smbclient.lstat(e.path, connection_cache=self._cache, port=self.port)  # = e.stat(), on our port
                    yield SourceEntry(rel, st.st_size, st.st_mtime)
            stack.extend(reversed(subdirs))  # depth-first in name order, like LocalDirSource

    def open_read(self, rel: str) -> BinaryIO:
        import smbclient
        # share_access "rw": never lock a file the device itself might be writing or reading
        return smbclient.open_file(self._path(rel), mode="rb", share_access="rw", connection_cache=self._cache,
                                   port=self.port)

    def stat(self, rel: str) -> SourceEntry | None:
        import smbclient
        try:
            st = smbclient.stat(self._path(rel), connection_cache=self._cache, port=self.port)
        except OSError as e:
            if _not_found(e):
                return None
            raise
        return SourceEntry(rel, st.st_size, st.st_mtime)

    def delete(self, rel: str) -> None:
        import smbclient
        check_deletable(rel)
        smbclient.remove(self._path(rel), connection_cache=self._cache, port=self.port)

    def rmdir(self, rel: str) -> None:
        import smbclient
        check_removable_dir(rel)
        smbclient.rmdir(self._path(rel), connection_cache=self._cache, port=self.port)

    def listdir(self, rel: str) -> list[str]:
        import smbclient
        try:
            return sorted(smbclient.listdir(self._path(rel), connection_cache=self._cache, port=self.port))
        except OSError as e:
            if _not_found(e):
                return []
            raise

    def close(self) -> None:
        import smbclient
        smbclient.reset_connection_cache(fail_on_error=False, connection_cache=self._cache)


# ---------------------------------------------------------------- an ASIAIR's storages, as one source

STORAGE_SHARES = ("EMMC Images", "TF Images", "Udisk Images")   # internal, SD card, USB drive (asiair.STORAGES)
_CAPTURE = {"Autorun", "Plan"}


class AsiairSource:
    """An ASIAIR with all its storages: the internal storage ("EMMC Images", paths as they always were), the SD card
    ("TF Images") and a USB drive ("Udisk Images"), each a separate share. On the SD card and USB drive the capture
    folders sit in an ASIAIR/ folder (older units save there); their paths get the share's name in front:
    "TF Images/Plan/Light/...". A share that's missing, empty, or has no Autorun/Plan is skipped.
    The label is the internal storage's, so batches and checks recorded before SD-card support still match."""

    def __init__(self, host: str, timeout: float = 15.0, port: int = 445):
        self.host = host
        self.label = f"smb://{host}/EMMC Images"
        self.storages: dict[str, tuple[SmbSource, str]] = {}   # prefix ("" = internal) -> (share, capture root)
        for share in STORAGE_SHARES:
            try:
                src = SmbSource(host, share, timeout=timeout, port=port)
                root = self._capture_root(src) if share != "EMMC Images" else ""
            except Exception:   # no such share, no card in, refused
                if share == "EMMC Images":
                    raise       # the device itself isn't answering: don't wait on the other shares too
                continue
            if root is None:
                src.close()
                continue
            self.storages["" if share == "EMMC Images" else share] = (src, root)
        if "" not in self.storages:
            raise OSError(f"no EMMC Images share on {host}")

    @staticmethod
    def _capture_root(src: SmbSource) -> str | None:
        top = set(src.listdir(""))
        if top & _CAPTURE:
            return ""
        if "ASIAIR" in top and set(src.listdir("ASIAIR")) & _CAPTURE:
            return "ASIAIR"
        return None

    def present(self) -> list[str]:
        """The storages with capture folders: ["EMMC Images", "TF Images", ...]."""
        return [p or "EMMC Images" for p in self.storages]

    def _route(self, rel: str) -> tuple[SmbSource, str]:
        first, _, rest = rel.partition("/")
        if first in self.storages and first:
            src, root = self.storages[first]
            return src, f"{root}/{rest}" if root else rest
        return self.storages[""][0], rel

    def walk(self) -> Iterator[SourceEntry]:
        for prefix, (src, root) in self.storages.items():
            for e in src.walk(root):
                inner = e.rel[len(root) + 1:] if root else e.rel
                yield SourceEntry(f"{prefix}/{inner}" if prefix else inner, e.size, e.mtime)

    def open_read(self, rel: str) -> BinaryIO:
        src, path = self._route(rel)
        return src.open_read(path)

    def stat(self, rel: str) -> SourceEntry | None:
        src, path = self._route(rel)
        e = src.stat(path)
        return SourceEntry(rel, e.size, e.mtime) if e else None

    def delete(self, rel: str) -> None:
        check_deletable(rel)
        src, path = self._route(rel)
        import smbclient
        smbclient.remove(src._path(path), connection_cache=src._cache, port=src.port)

    def rmdir(self, rel: str) -> None:
        check_removable_dir(rel)
        src, path = self._route(rel)
        import smbclient
        smbclient.rmdir(src._path(path), connection_cache=src._cache, port=src.port)

    def listdir(self, rel: str) -> list[str]:
        src, path = self._route(rel)
        return src.listdir(path)

    def close(self) -> None:
        for src, _ in self.storages.values():
            src.close()

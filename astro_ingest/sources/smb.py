"""The capture device's SMB share as a Source (guest access, read-only in this phase).

Only listing, stat and reading exist here; there is no write or delete method at all. Deleting from the device
arrives in phase 6, behind checksum-verified copies and Chris's approval.
"""

from __future__ import annotations

import threading
from typing import BinaryIO, Iterator

from astro_ingest.sources.base import SourceEntry

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


class SmbSource:
    def __init__(self, host: str, share: str, timeout: float = 15.0):
        import smbclient
        _configure()
        self.host, self.share = host, share
        self.label = f"smb://{host}/{share}"
        self._root = rf"\\{host}\{share}"
        self._cache: dict = {}  # this source's own connection cache
        smbclient.register_session(host, username="guest", password="", auth_protocol="ntlm", require_signing=False,
                                   connection_timeout=timeout, connection_cache=self._cache)

    def _path(self, rel: str) -> str:
        parts = [p for p in rel.split("/") if p]
        if any(p in (".", "..") for p in parts):
            raise ValueError(f"path escapes the share: {rel}")
        return "\\".join([self._root, *parts])

    def walk(self) -> Iterator[SourceEntry]:
        import smbclient
        stack = [""]
        while stack:
            rel_dir = stack.pop()
            entries = sorted(smbclient.scandir(self._path(rel_dir), connection_cache=self._cache),
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
                    st = e.stat()
                    yield SourceEntry(rel, st.st_size, st.st_mtime)
            stack.extend(reversed(subdirs))  # depth-first in name order, like LocalDirSource

    def open_read(self, rel: str) -> BinaryIO:
        import smbclient
        # share_access "rw": never lock a file the device itself might be writing or reading
        return smbclient.open_file(self._path(rel), mode="rb", share_access="rw", connection_cache=self._cache)

    def close(self) -> None:
        import smbclient
        smbclient.reset_connection_cache(fail_on_error=False, connection_cache=self._cache)

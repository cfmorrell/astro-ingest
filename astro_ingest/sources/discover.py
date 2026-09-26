"""Find the ASIAIR on the home network.

1. If a last-known address is given, try it first (the ASIAIR usually keeps its DHCP lease).
2. Otherwise probe every host in ASIAIR_SUBNET for SMB (TCP 445) in parallel, with a short timeout.
3. A host that lets a guest connect to the "EMMC Images" share is an ASIAIR.

Read-only by construction: an SMB tree connect to the share, then disconnect. No file is listed, opened or changed.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

SMB_PORT = 445


@dataclass
class Discovery:
    found: list[str]                 # hosts exporting the share (normally one)
    smb_hosts: list[str]             # every host that answered on 445
    scanned: int                     # hosts probed
    seconds: float
    errors: dict[str, str] = field(default_factory=dict)  # host -> why the share check failed


def port_open(host: str, port: int = SMB_PORT, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def has_share(host: str, share: str, timeout: float = 5.0) -> bool:
    """True if `share` on `host` accepts a guest connection. Raises on connection/auth problems (reported)."""
    from smbprotocol.connection import Connection
    from smbprotocol.exceptions import SMBResponseException
    from smbprotocol.session import Session
    from smbprotocol.tree import TreeConnect

    # The ASIAIR allows guest access only. A guest session has no signing key, so signing must not be required and
    # SMB3's secure-negotiate check (which needs that key) has to be skipped for the tree connect.
    conn = Connection(uuid.uuid4(), host, SMB_PORT, require_signing=False)
    conn.connect(timeout=timeout)
    try:
        session = Session(conn, username="guest", password="", require_encryption=False, auth_protocol="ntlm")
        session.connect()
        try:
            tree = TreeConnect(session, rf"\\{host}\{share}")
            try:
                tree.connect(require_secure_negotiate=False)
            except SMBResponseException:
                return False  # no such share (or not allowed): not an ASIAIR
            tree.disconnect()
            return True
        finally:
            session.disconnect()
    finally:
        conn.disconnect(True)


def discover(subnet: str, share: str, hint: str | None = None, workers: int = 64, port_timeout: float = 0.5,
             probe: Callable[[str], bool] | None = None, check: Callable[[str, str], bool] = has_share) -> Discovery:
    """Look for hosts exporting `share`. `probe`/`check` are injectable for tests."""
    probe = probe or (lambda h: port_open(h, timeout=port_timeout))
    started = time.monotonic()
    errors: dict[str, str] = {}

    def confirmed(host: str) -> bool:
        try:
            return check(host, share)
        except Exception as exc:  # unreachable mid-check, auth refused, ...: not it, but say why
            errors[host] = f"{type(exc).__name__}: {exc}"
            return False

    if hint and probe(hint) and confirmed(hint):
        return Discovery([hint], [hint], 1, round(time.monotonic() - started, 2), errors)

    hosts = [str(h) for h in ipaddress.ip_network(subnet, strict=False).hosts()]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        smb_hosts = [h for h, ok in zip(hosts, pool.map(probe, hosts)) if ok]
    found = [h for h in smb_hosts if confirmed(h)]
    return Discovery(found, smb_hosts, len(hosts), round(time.monotonic() - started, 2), errors)

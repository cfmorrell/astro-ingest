"""Capture devices the app can ingest from, how to recognize them on the network, and which one Chris picked.

A device is recognized by an SMB share that a guest can open *and* the folders at its top level: the ASIAIR and
the Seestar both call their share "EMMC Images", so the share name alone can't tell them apart.

Which unit is "Chris's" is remembered by **address + the name he gives it** (decided 2026-09-25): every ASIAIR
reports the same identity on the network (SMB server GUID bytes spell "asiair", NetBIOS name "ASIAIR", MAC
00:00:00:00:00:00), so nothing it sends distinguishes units. Each ASIAIR should have a DHCP reservation.

Only the ASIAIR is supported today; a Seestar entry goes in KINDS once its layout is confirmed on a real device.
Identification is read-only: a guest session, a tree connect, and one listing of the share's top level.
"""

from __future__ import annotations

import socket
import uuid
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class DeviceKind:
    kind: str                  # "asiair"
    label: str                 # shown to Chris
    share: str                 # SMB share name
    markers: frozenset[str]    # top-level folders that identify it (any one is enough)


KINDS: tuple[DeviceKind, ...] = (
    DeviceKind("asiair", "ZWO ASIAIR", "EMMC Images", frozenset({"Autorun", "Plan", "Live"})),
    # Seestar S50: to add, with its share name and top-level folders (e.g. "MyWorks"), once checked on the device.
)


@dataclass
class Device:
    kind: str
    label: str
    host: str
    share: str
    server_guid: str                                  # informational only: the same on every ASIAIR
    name: str | None = None                          # hostname from reverse DNS, if the router knows it
    folders: list[str] = field(default_factory=list)  # top-level folders seen on the share

    @property
    def display(self) -> str:
        return f"{self.label} {self.name or ''} at {self.host}".replace("  ", " ")

    def to_dict(self) -> dict:
        return asdict(self)


def classify(folders: list[str], share: str) -> DeviceKind | None:
    """The device kind whose share and marker folders match what was seen, if any."""
    names = set(folders)
    for k in KINDS:
        if k.share == share and names & k.markers:
            return k
    return None


def _reverse_dns(host: str) -> str | None:
    try:
        name = socket.gethostbyaddr(host)[0]
    except OSError:
        return None
    return name.split(".")[0] if name and name != host else None


def identify(host: str, timeout: float = 5.0) -> list[Device]:
    """Every known device kind served by `host` (normally zero or one). Raises on connection/auth problems."""
    from smbprotocol.connection import Connection
    from smbprotocol.exceptions import SMBResponseException
    from smbprotocol.file_info import FileInformationClass
    from smbprotocol.open import (CreateDisposition, CreateOptions, DirectoryAccessMask, FileAttributes,
                                  ImpersonationLevel, Open, ShareAccess)
    from smbprotocol.session import Session
    from smbprotocol.tree import TreeConnect

    # Guest only: a guest session has no signing key, so signing must not be required and SMB3's secure-negotiate
    # check (which needs that key) is skipped for the tree connect.
    conn = Connection(uuid.uuid4(), host, 445, require_signing=False)
    conn.connect(timeout=timeout)
    found: list[Device] = []
    try:
        session = Session(conn, username="guest", password="", require_encryption=False, auth_protocol="ntlm")
        session.connect()
        try:
            for share in sorted({k.share for k in KINDS}):
                tree = TreeConnect(session, rf"\\{host}\{share}")
                try:
                    tree.connect(require_secure_negotiate=False)
                except SMBResponseException:
                    continue  # no such share here
                try:
                    root = Open(tree, "")
                    root.create(ImpersonationLevel.Impersonation,
                                DirectoryAccessMask.FILE_LIST_DIRECTORY | DirectoryAccessMask.FILE_READ_ATTRIBUTES,
                                FileAttributes.FILE_ATTRIBUTE_DIRECTORY,
                                ShareAccess.FILE_SHARE_READ | ShareAccess.FILE_SHARE_WRITE,
                                CreateDisposition.FILE_OPEN, CreateOptions.FILE_DIRECTORY_FILE)
                    try:
                        entries = root.query_directory("*", FileInformationClass.FILE_NAMES_INFORMATION)
                    finally:
                        root.close()
                    folders = sorted(e["file_name"].get_value().decode("utf-16-le") for e in entries)
                    folders = [f for f in folders if f not in (".", "..")]
                finally:
                    tree.disconnect()
                kind = classify(folders, share)
                if kind:
                    found.append(Device(kind.kind, kind.label, host, share, str(conn.server_guid),
                                        _reverse_dns(host), folders))
        finally:
            session.disconnect()
    finally:
        conn.disconnect(True)
    return found


# ---------------------------------------------------------------- which device to use

@dataclass
class Choice:
    status: str                # "remembered" | "only-one" | "ask" | "none"
    device: Device | None      # the device to use (None when asking or nothing found)
    candidates: list[Device]
    message: str


def describe(remembered: dict) -> str:
    """'Z61 rig (ZWO ASIAIR at 192.168.1.43)', or without the name if Chris hasn't given one."""
    where = f"{remembered.get('label', 'device')} at {remembered.get('host')}"
    return f"{remembered['nickname']} ({where})" if remembered.get("nickname") else where


def is_remembered(d: Device, remembered: dict | None) -> bool:
    return bool(remembered) and d.kind == remembered.get("kind") and d.host == remembered.get("host")


def choose(found: list[Device], remembered: dict | None) -> Choice:
    """Pick the device to ingest from, by the remembered address. Never switches silently to a device Chris didn't
    pick: if his remembered device isn't answering and another one is, he is asked."""
    for d in found:
        if is_remembered(d, remembered):
            return Choice("remembered", d, found, f"Using {describe(remembered)}.")
    if not found:
        who = describe(remembered) if remembered else "a capture device"
        return Choice("none", None, [], f"Couldn't find {who} on the network. Is it powered on?")
    if len(found) == 1 and not remembered:
        return Choice("only-one", found[0], found, f"Found one device: {found[0].display}.")
    if remembered:
        return Choice("ask", None, found, f"{describe(remembered)} isn't answering, but {len(found)} other "
                      "device(s) are. Pick one to use (it may just have a new address).")
    return Choice("ask", None, found, f"Found {len(found)} devices. Pick the one to ingest from.")

"""Find capture devices (today: ZWO ASIAIR) on the home network.

1. Try the addresses we already know (the remembered device's address, ASIAIR_HOST) first: if the remembered
   device answers there, no scan is needed.
2. Otherwise probe every host in the subnet for SMB (TCP 445) in parallel, with a short timeout.
3. Identify each SMB host (devices.identify): a guest-openable share plus the device's own top-level folders.

Read-only by construction: guest sessions, tree connects and one top-level listing per share. No file is opened.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from astro_ingest.sources.devices import Device, identify, is_remembered

SMB_PORT = 445


@dataclass
class Discovery:
    devices: list[Device]            # every recognized device found
    smb_hosts: list[str]             # every host that answered on 445
    scanned: int                     # hosts probed
    seconds: float
    errors: dict[str, str] = field(default_factory=dict)  # host -> why identification failed


def port_open(host: str, port: int = SMB_PORT, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def discover(subnet: str, hints: list[str] | None = None, remembered: dict | None = None, full: bool = False,
             workers: int = 64, port_timeout: float = 0.5, probe: Callable[[str], bool] | None = None,
             ident: Callable[[str], list[Device]] = identify) -> Discovery:
    """Find devices. With a `remembered` device and not `full`, stop as soon as it answers at its address.
    `probe`/`ident` are injectable for tests."""
    probe = probe or (lambda h: port_open(h, timeout=port_timeout))
    started = time.monotonic()
    errors: dict[str, str] = {}

    def identified(host: str) -> list[Device]:
        try:
            return ident(host)
        except Exception as exc:  # dropped mid-check, auth refused, ...: not usable, but say why
            errors[host] = f"{type(exc).__name__}: {exc}"
            return []

    hints = [h for h in dict.fromkeys(hints or []) if h]
    if remembered and not full:
        for h in hints:
            if probe(h):
                devs = identified(h)
                if any(is_remembered(d, remembered) for d in devs):
                    return Discovery(devs, [h], len(hints), round(time.monotonic() - started, 2), errors)

    hosts = [str(h) for h in ipaddress.ip_network(subnet, strict=False).hosts()]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        smb_hosts = [h for h, ok in zip(hosts, pool.map(probe, hosts)) if ok]
    devices = [d for h in smb_hosts for d in identified(h)]
    return Discovery(devices, smb_hosts, len(hosts), round(time.monotonic() - started, 2), errors)

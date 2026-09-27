"""Which home network to search for capture devices (Chris, 2026-09-27: "use whatever the user's home network is. If
we can't figure that out automatically, we should prompt").

In order:
1. ASIAIR_SUBNET, if set (an override for unusual setups);
2. the network of the browser using the app: it's on the home network, while the app itself may run in a Docker
   container that only sees Docker's internal bridge (172.17.0.0/16 on UnRAID);
3. the server's own networks, when it has a real one (host networking in production);
4. the networks of devices connected to before.
None of these -> None, and the page asks.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
from pathlib import Path

DOCKER = ipaddress.ip_network("172.16.0.0/12")    # Docker's default bridge pool: never a home network here
MAX_HOSTS = 1024                                   # search at most a /22 (a /24 takes ~30 s)


def _private_v4(addr: str) -> ipaddress.IPv4Address | None:
    try:
        a = ipaddress.ip_address(addr.strip())
    except ValueError:
        return None
    if a.version != 4 or not a.is_private or a.is_loopback or a.is_link_local:
        return None
    return a


def around(addr: str) -> str | None:
    """The /24 containing a private IPv4 address outside Docker's pool: '192.168.1.3' -> '192.168.1.0/24'."""
    a = _private_v4(addr)
    if a is None or a in DOCKER:
        return None
    return str(ipaddress.ip_network(f"{a}/24", strict=False))


def server_networks(route_file: str = "/proc/net/route") -> list[str]:
    """The server's own directly connected networks (Linux), skipping Docker's bridge and anything bigger than a /22."""
    out = []
    try:
        lines = Path(route_file).read_text().splitlines()[1:]
    except OSError:
        return out
    for line in lines:
        f = line.split()
        if len(f) < 8 or f[1] == "00000000":
            continue   # the default route
        net = socket.inet_ntoa(struct.pack("<L", int(f[1], 16)))
        mask = socket.inet_ntoa(struct.pack("<L", int(f[7], 16)))
        try:
            n = ipaddress.ip_network(f"{net}/{mask}")
        except ValueError:
            continue
        if n.is_private and not n.overlaps(DOCKER) and not n.is_loopback and n.num_addresses <= MAX_HOSTS:
            out.append(str(n))
    return out


def valid_subnet(text: str) -> str:
    """A searchable network from what someone typed ('192.168.50.0/24', '192.168.50.7' -> its /24). Raises ValueError."""
    text = text.strip()
    if "/" not in text:
        text = f"{text}/24"
    n = ipaddress.ip_network(text, strict=False)
    if n.version != 4 or not n.is_private:
        raise ValueError(f"{text} isn't a private IPv4 network")
    if n.num_addresses > MAX_HOSTS:
        raise ValueError(f"{n} has {n.num_addresses} addresses; search at most a /22")
    return str(n)


def detect(setting: str | None, client_hosts: list[str], recent_hosts: list[str],
           route_file: str = "/proc/net/route") -> dict | None:
    """{"subnet", "source", "detail"} for the network to search, or None when it can't be worked out."""
    if setting:
        return {"subnet": valid_subnet(setting), "source": "setting", "detail": "ASIAIR_SUBNET"}
    for h in client_hosts:
        s = around(h)
        if s:
            return {"subnet": s, "source": "browser", "detail": h.strip()}
    for s in server_networks(route_file):
        return {"subnet": s, "source": "server", "detail": s}
    for h in recent_hosts:
        s = around(h)
        if s:
            return {"subnet": s, "source": "history", "detail": h}
    return None

"""Which home network to search: the setting, the browser's address, the server's own networks, recent devices."""

import pytest

from astro_ingest.sources import network

HEADER = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
DEFAULT = "eth0\t00000000\t010011AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
DOCKER = "eth0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"      # 172.17.0.0/16
LAN = "br0\t0001A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"          # 192.168.1.0/24


def test_detect_order(tmp_path):
    docker = tmp_path / "route-docker"
    docker.write_text(HEADER + DEFAULT + DOCKER)
    lan = tmp_path / "route-lan"
    lan.write_text(HEADER + DEFAULT + DOCKER + LAN)
    assert network.server_networks(str(docker)) == []                      # Docker's bridge isn't a home network
    assert network.server_networks(str(lan)) == ["192.168.1.0/24"]
    assert network.detect(None, ["192.168.1.3"], [], str(docker)) == \
        {"subnet": "192.168.1.0/24", "source": "browser", "detail": "192.168.1.3"}
    assert network.detect(None, ["127.0.0.1", "172.17.0.1"], [], str(lan))["source"] == "server"
    assert network.detect(None, ["172.17.0.1"], ["192.168.50.9"], str(docker)) == \
        {"subnet": "192.168.50.0/24", "source": "history", "detail": "192.168.50.9"}
    assert network.detect(None, ["127.0.0.1"], [], str(docker)) is None      # the page asks
    assert network.detect("10.0.0.0/24", ["192.168.1.3"], [], str(lan))["source"] == "setting"


def test_valid_subnet():
    assert network.valid_subnet(" 192.168.50.7 ") == "192.168.50.0/24"
    assert network.valid_subnet("10.1.0.0/22") == "10.1.0.0/22"
    for bad in ("8.8.8.0/24", "10.0.0.0/16", "nonsense"):
        with pytest.raises(ValueError):
            network.valid_subnet(bad)

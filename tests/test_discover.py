"""Discovery logic against a fake network: no sockets, no SMB."""

from astro_ingest.sources.discover import discover

SHARE = "EMMC Images"


def fake_network(smb_hosts, asiair_hosts, broken=()):
    probed, checked = [], []

    def probe(host):
        probed.append(host)
        return host in smb_hosts

    def check(host, share):
        checked.append(host)
        if host in broken:
            raise ConnectionResetError("peer went away")
        return share == SHARE and host in asiair_hosts

    return probe, check, probed, checked


def test_finds_the_asiair_among_other_smb_hosts():
    probe, check, probed, checked = fake_network({"192.168.1.4", "192.168.1.43", "192.168.1.60"}, {"192.168.1.43"})
    d = discover("192.168.1.0/24", SHARE, probe=probe, check=check)
    assert d.found == ["192.168.1.43"]
    assert sorted(d.smb_hosts) == ["192.168.1.4", "192.168.1.43", "192.168.1.60"]
    assert d.scanned == 254 and len(probed) == 254
    assert sorted(checked) == sorted(d.smb_hosts)          # the share check only runs on hosts with SMB open


def test_last_known_address_short_circuits_the_scan():
    probe, check, probed, _ = fake_network({"192.168.1.43"}, {"192.168.1.43"})
    d = discover("192.168.1.0/24", SHARE, hint="192.168.1.43", probe=probe, check=check)
    assert d.found == ["192.168.1.43"] and probed == ["192.168.1.43"] and d.scanned == 1


def test_stale_hint_falls_back_to_a_scan():
    probe, check, _, _ = fake_network({"192.168.1.50"}, {"192.168.1.50"})   # the ASIAIR got a new lease
    d = discover("192.168.1.0/24", SHARE, hint="192.168.1.43", probe=probe, check=check)
    assert d.found == ["192.168.1.50"]


def test_nothing_found_and_errors_reported():
    probe, check, _, _ = fake_network({"192.168.1.4", "192.168.1.9"}, set(), broken={"192.168.1.9"})
    d = discover("192.168.1.0/24", SHARE, probe=probe, check=check)
    assert d.found == [] and "ConnectionResetError" in d.errors["192.168.1.9"]

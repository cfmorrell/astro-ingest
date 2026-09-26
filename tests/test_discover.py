"""Discovery and device choice against a fake network: no sockets, no SMB."""

from astro_ingest.sources.devices import Device, choose, classify
from astro_ingest.sources.discover import discover

ASIAIR_FOLDERS = ["Autorun", "GuidingDarkLibrary", "Live", "Plan", "Preview", "Video", "log"]


def asiair(host, guid, name=None):
    return Device("asiair", "ZWO ASIAIR", host, "EMMC Images", guid, name, ASIAIR_FOLDERS)


def fake_network(devices_by_host, smb_only=(), broken=()):
    """devices_by_host: {host: [Device]}; smb_only: hosts with SMB but no device (e.g. the NAS)."""
    probed, identified = [], []

    def probe(host):
        probed.append(host)
        return host in devices_by_host or host in smb_only or host in broken

    def ident(host):
        identified.append(host)
        if host in broken:
            raise ConnectionResetError("peer went away")
        return devices_by_host.get(host, [])

    return probe, ident, probed, identified


# ---------------------------------------------------------------- discovery

def test_finds_every_device_and_skips_other_smb_hosts():
    a, b = asiair("192.168.1.43", "g1"), asiair("192.168.1.77", "g2")
    probe, ident, probed, identified = fake_network({a.host: [a], b.host: [b]}, smb_only={"192.168.1.4"})
    d = discover("192.168.1.0/24", probe=probe, ident=ident)
    assert [x.host for x in d.devices] == ["192.168.1.43", "192.168.1.77"]
    assert sorted(d.smb_hosts) == ["192.168.1.4", "192.168.1.43", "192.168.1.77"]
    assert d.scanned == 254 and len(probed) == 254 and sorted(identified) == sorted(d.smb_hosts)


REMEMBERED = {"kind": "asiair", "host": "192.168.1.43", "label": "ZWO ASIAIR", "nickname": "Z61 rig"}


def test_remembered_device_at_its_address_skips_the_scan():
    a = asiair("192.168.1.43", "g")
    probe, ident, probed, _ = fake_network({a.host: [a], "192.168.1.77": [asiair("192.168.1.77", "g")]})
    d = discover("192.168.1.0/24", hints=["192.168.1.43"], remembered=REMEMBERED, probe=probe, ident=ident)
    assert [x.host for x in d.devices] == ["192.168.1.43"] and probed == ["192.168.1.43"]


def test_full_scan_even_when_remembered_answers():
    a, b = asiair("192.168.1.43", "g1"), asiair("192.168.1.77", "g2")
    probe, ident, _, _ = fake_network({a.host: [a], b.host: [b]})
    d = discover("192.168.1.0/24", hints=["192.168.1.43"], remembered=REMEMBERED, full=True, probe=probe, ident=ident)
    assert len(d.devices) == 2


def test_identification_errors_are_reported():
    probe, ident, _, _ = fake_network({}, smb_only={"192.168.1.4"}, broken={"192.168.1.9"})
    d = discover("192.168.1.0/24", probe=probe, ident=ident)
    assert d.devices == [] and "ConnectionResetError" in d.errors["192.168.1.9"]


# ---------------------------------------------------------------- recognizing devices

def test_classify_by_share_and_folders():
    assert classify(ASIAIR_FOLDERS, "EMMC Images").kind == "asiair"
    assert classify(["MyWorks"], "EMMC Images") is None        # same share name, not an ASIAIR (e.g. a Seestar)
    assert classify(ASIAIR_FOLDERS, "Other") is None


# ---------------------------------------------------------------- choosing one

def test_one_device_nothing_remembered_is_used():
    a = asiair("192.168.1.43", "g1")
    c = choose([a], None)
    assert (c.status, c.device) == ("only-one", a)


def test_several_devices_means_ask():
    c = choose([asiair("192.168.1.43", "g1"), asiair("192.168.1.77", "g2")], None)
    assert c.status == "ask" and c.device is None and len(c.candidates) == 2


def test_remembered_device_is_used_among_several():
    mine = asiair("192.168.1.43", "g")   # every ASIAIR reports the same server id: identity is the address
    c = choose([asiair("192.168.1.77", "g"), mine], REMEMBERED)
    assert (c.status, c.device) == ("remembered", mine) and "Z61 rig" in c.message


def test_never_switches_silently_to_another_device():
    other = asiair("192.168.1.77", "g")
    c = choose([other], REMEMBERED)
    assert c.status == "ask" and c.device is None
    assert "Z61 rig (ZWO ASIAIR at 192.168.1.43) isn't answering" in c.message


def test_nothing_found():
    assert choose([], None).status == "none"
    assert "Z61 rig" in choose([], REMEMBERED).message

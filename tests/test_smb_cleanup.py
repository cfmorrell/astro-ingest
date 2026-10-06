"""Clean up through the real SmbSource code path, against a throwaway SMB server (impacket) on a high port.

Nothing here touches a real device: the "device" is a tmp folder served as "EMMC Images" on 127.0.0.1. The server
runs from its own venv (dev/smb-test-server.py; SMBTEST_PYTHON or .venv-smbtest), and the test is skipped without it.
"""

import dataclasses
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
from test_batch import make

from astro_ingest import cleanup, service
from astro_ingest.sources.base import DeleteRefused, check_deletable

REPO = Path(__file__).resolve().parent.parent
SERVER_PY = os.environ.get("SMBTEST_PYTHON") or str(REPO / ".venv-smbtest" / "bin" / "python")
LATER = time.time() + 3600
HEART = "HeartNebula-IC1805/2026-09-23-HeartNebula-2600MC-Z61"


@pytest.fixture
def smb_device(tmp_path):
    """(cfg, air folder, port): the test world's ASIAIR folder, served over SMB."""
    if not Path(SERVER_PY).exists():
        pytest.skip("no SMB test server venv (python3 -m venv .venv-smbtest && .venv-smbtest/bin/pip install impacket)")
    cfg, air, root = make(tmp_path)
    tf = tmp_path / "tf"                                                # an SD card, laid out like an older ASIAIR's
    (tf / "ASIAIR" / "Plan" / "Light").mkdir(parents=True)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = subprocess.Popen([SERVER_PY, str(REPO / "dev" / "smb-test-server.py"), str(air), str(port), f"TF Images={tf}"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        assert srv.stdout.readline().strip() == "ready"
        for _ in range(50):                                             # wait until it accepts connections
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        yield cfg, air, port
    finally:
        srv.terminate()
        srv.wait(timeout=10)


def test_verify_and_delete_over_smb(smb_device, tmp_path):
    from astro_ingest.sources.smb import SmbSource
    cfg, air, port = smb_device
    lights = sorted((air / "Plan/Light/HeartNebula").glob("*.fit"))
    for f in lights:                                                  # all four Heart lights already on the NAS
        dst = tmp_path / "nas" / HEART / "lights" / f.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, dst)
    src = SmbSource("127.0.0.1", "EMMC Images", timeout=5, port=port)
    try:
        p = service.scan_and_plan(cfg, src)
        assert not p.source.local
        assert cleanup.verify(cfg, p)["verified"] == 4                  # read over SMB, compared with the NAS copies
        verified = [c.rel for c in cleanup.preview(cfg, p).candidates if c.group == "verified"]
        assert len(verified) == 4

        with pytest.raises(DeleteRefused):                               # a real device: switched off by default
            cleanup.approve(cfg, p, verified)

        on = dataclasses.replace(cfg, allow_device_delete=True)
        cid = cleanup.approve(on, p, verified)
        res = cleanup.run(on, p, cid, now=LATER)
        assert res["deleted"] == 8 and not res["failed"] and not res["unexpected_missing"], res
        assert res["pruned"] == ["Plan/Light/HeartNebula"] and not (air / "Plan/Light/HeartNebula").exists()
        assert (air / "Plan/Light/SoulNebula").is_dir() and len(list((air / "Plan/Light/SoulNebula").glob("*.fit"))) == 5
        assert src.stat(verified[0]) is None and src.listdir("Plan/Light/HeartNebula") == []
        with pytest.raises(DeleteRefused):
            src.delete("Live/anything.fit")
    finally:
        src.close()


def test_an_asiair_with_an_sd_card(smb_device, tmp_path):
    # older ASIAIRs save to the SD card ("TF Images" share, under ASIAIR/); its frames join the internal storage's
    from fitsgen import asiair_frame

    from astro_ingest.core.scan import scan
    from astro_ingest.sources.base import check_removable_dir
    from astro_ingest.sources.smb import AsiairSource
    cfg, air, port = smb_device
    tf = tmp_path / "tf"
    f = asiair_frame(tf / "ASIAIR", "Plan/Light/IC 1805", "Light", "20251004-213000", obj="IC 1805", camera="2600MM",
                     filter_="H")
    (tf / ".DS_Store").write_bytes(b"x")                                # outside ASIAIR/: not part of the capture area
    src = AsiairSource("127.0.0.1", timeout=5, port=port)
    try:
        assert src.present() == ["EMMC Images", "TF Images"]            # no USB drive share: skipped
        rel = f"TF Images/Plan/Light/IC 1805/{f.name}"
        rels = [e.rel for e in src.walk()]
        assert rel in rels and not any(r.startswith("TF Images/.DS_Store") for r in rels)
        assert any(r.startswith("Plan/Light/HeartNebula/") for r in rels)                 # internal paths unchanged
        assert src.stat(rel).size == f.stat().st_size
        with src.open_read(rel) as fh:
            assert fh.read() == f.read_bytes()
        frame = next(x for x in scan(src, cfg.tz).handled() if x.rel == rel)
        assert frame.kind == "Light" and frame.name.camera == "2600MM" and frame.thumb is not None
    finally:
        src.close()
    check_deletable(rel)                                                # the same rules on the SD card
    check_removable_dir("TF Images/Plan/Light/IC 1805")
    for bad in ("TF Images/Live/x.fit", "TF Images/Plan/Light", "TF Images/x.fit", "Udisk Images/log/a.txt"):
        with pytest.raises(DeleteRefused):
            check_removable_dir(bad) if bad.endswith("Light") else check_deletable(bad)

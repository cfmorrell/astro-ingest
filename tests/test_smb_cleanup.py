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
from astro_ingest.sources.base import DeleteRefused

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
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = subprocess.Popen([SERVER_PY, str(REPO / "dev" / "smb-test-server.py"), str(air), str(port)],
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

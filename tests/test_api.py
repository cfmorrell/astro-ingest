from fastapi.testclient import TestClient
from fitsgen import asiair_frame

from astro_ingest.api import create_app
from astro_ingest.config import VERSION, Config


def make_app(tmp_path, with_source=True):
    air, nas, root = tmp_path / "asiair", tmp_path / "nas", tmp_path / "sandbox"
    for d in (nas, root / "Z95-ClaudeReferences"):
        d.mkdir(parents=True)
    (root / "Z95-ClaudeReferences" / "targets.csv").write_text(
        "folder,name,messier,ngc,ic,other\nSoulNebula-IC1848,SoulNebula,,,1848,\n")
    if with_source:
        for i in range(4):
            asiair_frame(air, "Plan/Light/SoulNebula", "Light", f"20260923-21{i}420", obj="SoulNebula", angle=3,
                         seq=i + 1)
    cfg = Config.from_env({"ASTRO_ROOT": str(root), "ASTRO_NAS": str(nas), "STATE_DIR": str(root / "state"),
                           "TZ": "America/New_York", "ASIAIR_ROOT": str(air)})
    return TestClient(create_app(cfg)), air, root


def test_health_and_static(tmp_path):
    client, _, _ = make_app(tmp_path)
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["version"] == VERSION and h["source_online"] is True
    page = client.get("/")
    assert page.status_code == 200 and "astro-ingest" in page.text
    assert client.get("/styles.css").status_code == 200 and client.get("/app.js").status_code == 200


def test_plan_and_rescan(tmp_path):
    client, air, _ = make_app(tmp_path)
    plan = client.get("/api/plan").json()
    assert plan["summary"]["copy_files"] == 4 and plan["sessions"][0]["rel"].startswith("SoulNebula-IC1848/")
    assert "scanned_at" in plan
    asiair_frame(air, "Plan/Light/SoulNebula", "Light", "20260923-221420", obj="SoulNebula", angle=3, seq=5)
    assert client.get("/api/plan").json()["summary"]["copy_files"] == 4        # cached until a rescan
    assert client.post("/api/scan").json()["summary"]["copy_files"] == 5


def test_thumbnails_only(tmp_path):
    client, air, _ = make_app(tmp_path)
    thumb = next(air.rglob("*_thn.jpg")).relative_to(air).as_posix()
    r = client.get("/api/thumb", params={"rel": thumb})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    fit = thumb.replace("_thn.jpg", ".fit")
    assert client.get("/api/thumb", params={"rel": fit}).status_code == 400
    assert client.get("/api/thumb", params={"rel": "../x_thn.jpg"}).status_code == 400
    assert client.get("/api/thumb", params={"rel": "/etc/x_thn.jpg"}).status_code == 400
    assert client.get("/api/thumb", params={"rel": "Plan/missing_thn.jpg"}).status_code == 404


def test_offline_source(tmp_path):
    client, _, _ = make_app(tmp_path, with_source=False)
    assert client.get("/health").json()["source_online"] is False
    assert client.get("/api/plan").status_code == 503


def test_nothing_written(tmp_path):
    client, air, root = make_app(tmp_path)
    before = sorted(p for p in tmp_path.rglob("*"))
    client.get("/api/plan")
    client.post("/api/scan")
    assert sorted(p for p in tmp_path.rglob("*")) == before

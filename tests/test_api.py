from fastapi.testclient import TestClient
from fitsgen import asiair_frame, star_field

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
                         seq=i + 1, data=star_field(seed=i))
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


def test_previews(tmp_path):
    client, air, root = make_app(tmp_path)
    rel = next(air.rglob("*.fit")).relative_to(air).as_posix()
    r = client.get("/api/preview", params={"rel": rel, "size": 320})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert list((root / "state" / "cache" / "previews").glob("*-320.png"))   # cached in CACHE_DIR
    assert client.get("/api/preview", params={"rel": rel, "size": 123}).status_code == 400
    assert client.get("/api/preview", params={"rel": "../x.fit"}).status_code == 400
    assert client.get("/api/preview", params={"rel": "Plan/missing.fit"}).status_code == 404
    thumb = next(air.rglob("*_thn.jpg")).relative_to(air).as_posix()
    assert client.get("/api/preview", params={"rel": thumb}).status_code == 404   # not a frame


def test_answers_endpoint(tmp_path):
    client, air, root = make_app(tmp_path)
    rel = next(air.rglob("*.fit")).relative_to(air).as_posix()
    plan = client.post("/api/answers", json={f"keep:{rel}": "reject", "quality-sigma": "3.5"}).json()
    assert plan["sigma"] == 3.5
    assert next(i for i in plan["items"] if i["src"] == rel)["action"] == "rejected"
    assert client.post("/api/answers", json={"target-abc": "new:X"}).status_code == 400   # not in this phase
    assert client.post("/api/answers", json={"quality-sigma": "99"}).status_code == 400


def test_quality_job(tmp_path):
    import time
    client, _, _ = make_app(tmp_path)
    job_id = client.post("/api/quality/run").json()["job_id"]
    for _ in range(300):
        snap = client.get(f"/jobs/{job_id}").json()
        if snap["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.02)
    assert snap["status"] == "succeeded", snap
    assert snap["result"]["scored"] == 4
    assert "scoring" in client.get(f"/jobs/{job_id}/log").text
    assert any(j["id"] == job_id for j in client.get("/jobs").json()["jobs"])


def test_offline_source(tmp_path):
    client, _, _ = make_app(tmp_path, with_source=False)
    assert client.get("/health").json()["source_online"] is False
    assert client.get("/api/plan").status_code == 503


def test_only_state_and_cache_written(tmp_path):
    client, air, root = make_app(tmp_path)
    before = sorted(p for p in tmp_path.rglob("*"))
    client.get("/api/plan")
    client.post("/api/scan")
    rel = next(air.rglob("*.fit")).relative_to(air).as_posix()
    client.get("/api/preview", params={"rel": rel})
    client.post("/api/answers", json={f"keep:{rel}": "reject"})
    new = [p for p in tmp_path.rglob("*") if p not in before]
    assert new and all(p.is_relative_to(root / "state") for p in new)


def test_device_find_and_select(tmp_path, monkeypatch):
    from astro_ingest.sources import discover as disc
    from astro_ingest.sources.devices import Device

    folders = ["Autorun", "Live", "Plan"]
    found = [Device("asiair", "ZWO ASIAIR", "192.168.1.43", "EMMC Images", "g", None, folders),
             Device("asiair", "ZWO ASIAIR", "192.168.1.77", "EMMC Images", "g", None, folders)]
    monkeypatch.setattr(disc, "discover", lambda *a, **k: disc.Discovery(found, ["192.168.1.43", "192.168.1.77"], 254, 2.4))
    client, _, root = make_app(tmp_path)
    assert client.get("/api/devices").json()["remembered"] is None

    res = client.post("/api/devices/find", json={"full": False}).json()
    assert res["choice"]["status"] == "ask" and len(res["found"]) == 2          # two ASIAIRs: Chris picks
    assert client.get("/api/devices").json()["remembered"] is None               # nothing chosen for him

    res = client.post("/api/devices/select", json={"host": "192.168.1.77", "nickname": "ASIAIR Mono"}).json()
    assert res["remembered"]["host"] == "192.168.1.77" and res["remembered"]["nickname"] == "ASIAIR Mono"
    assert (root / "state" / "devices.json").is_file()
    assert client.post("/api/devices/select", json={"host": "10.0.0.1"}).status_code == 404

    monkeypatch.setattr(disc, "discover", lambda *a, **k: disc.Discovery(found[:1], ["192.168.1.43"], 254, 2.4))
    res = client.post("/api/devices/find", json={"full": True}).json()
    assert res["choice"]["status"] == "ask" and "ASIAIR Mono" in res["choice"]["message"]   # never a silent switch


def test_answering_decisions(tmp_path):
    client, air, _ = make_app(tmp_path)
    for i in range(4):   # an object with no target in targets.csv -> a "target" decision
        asiair_frame(air, "Plan/Light/NGC 4565", "Light", f"20260427-21{i}000", obj="NGC 4565", seq=i + 1,
                     FOCALLEN=1384, data=star_field(seed=i))
    plan = client.post("/api/scan") and client.get("/api/plan").json()
    d = next(x for x in plan["decisions"] if x["kind"] == "target")
    assert d["resolved"] is None

    for bad in ("new:needle galaxy", "new:Needle'sGalaxy", "new:", "some-other-value"):
        assert client.post("/api/answers", json={d["id"]: bad}).status_code == 400
    assert client.post("/api/answers", json={"target-doesnotexist": "skip"}).status_code == 400

    plan = client.post("/api/answers", json={d["id"]: "new:NeedleGalaxy-NGC4565"}).json()
    d2 = next(x for x in plan["decisions"] if x["id"] == d["id"])
    assert d2["answer"] == "new:NeedleGalaxy-NGC4565"
    assert any(s["rel"].startswith("NeedleGalaxy-NGC4565/2026-04-27-NeedleGalaxy-2600MC-RC6") for s in plan["sessions"])

    plan = client.post("/api/answers", json={d["id"]: "skip"}).json()
    assert next(x for x in plan["decisions"] if x["id"] == d["id"])["answer"] == "skip"
    plan = client.post("/api/answers", json={d["id"]: None}).json()           # reset
    assert next(x for x in plan["decisions"] if x["id"] == d["id"])["resolved"] is None



def test_log_files_are_served_by_name_only(tmp_path):
    client, air, root = make_app(tmp_path)
    logs = root / "state" / "logs"
    (logs / "jobs").mkdir(parents=True)
    (logs / "copy-20260927-101010.log").write_text("OK\tcopy\n")
    (logs / "jobs" / "abc123.log").write_text("=== job ===\n")
    (root / "state" / "answers.json").write_text("{}")
    r = client.get("/api/logs/copy-20260927-101010.log")
    assert r.status_code == 200 and r.text == "OK\tcopy\n" and r.headers["content-type"].startswith("text/plain")
    assert client.get("/api/logs/jobs/abc123.log").text == "=== job ===\n"
    for bad in ("/api/logs/..%2Fanswers.json", "/api/logs/answers.json", "/api/logs/nothing-here.log"):
        assert client.get(bad).status_code == 404, bad

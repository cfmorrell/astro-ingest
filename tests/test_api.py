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


def test_devices_search_connect_rename_forget(tmp_path, monkeypatch):
    from astro_ingest.sources import devices as devmod
    from astro_ingest.sources import discover as disc
    from astro_ingest.sources.devices import Device

    folders = ["Autorun", "Live", "Plan"]
    found = [Device("asiair", "ZWO ASIAIR", "192.168.1.43", "EMMC Images", "g", None, folders),
             Device("asiair", "ZWO ASIAIR", "192.168.1.77", "EMMC Images", "g", None, folders)]
    searched = []
    monkeypatch.setattr(disc, "discover", lambda subnet, *a, **k: searched.append(subnet) or disc.Discovery(
        found, ["192.168.1.43", "192.168.1.77"], 254, 2.4))
    client, _, root = make_app(tmp_path)
    fwd = {"X-Forwarded-For": "192.168.1.3"}                          # the browser, on the home network
    d = client.get("/api/devices", headers=fwd).json()
    assert d["remembered"] is None and d["recent"] == []
    assert d["network"] == {"subnet": "192.168.1.0/24", "source": "browser", "detail": "192.168.1.3"}

    res = client.post("/api/devices/find", json={}, headers=fwd).json()
    assert searched == ["192.168.1.0/24"] and len(res["found"]) == 2 and res["remembered"] is None  # nothing picked
    one = [found[0]]
    monkeypatch.setattr(disc, "discover", lambda *a, **k: disc.Discovery(one, ["192.168.1.43"], 254, 2.4))
    res = client.post("/api/devices/find", json={"subnet": "192.168.1.0/24"}).json()
    assert len(res["found"]) == 1 and res["remembered"] is None       # even when there's only one
    assert client.post("/api/devices/find", json={"subnet": "8.8.8.0/24"}).status_code == 400

    res = client.post("/api/devices/select", json={"host": "192.168.1.43", "nickname": "ASIAIR Color"}).json()
    assert res["remembered"]["nickname"] == "ASIAIR Color" and res["remembered"]["slug"] == "ASIAIR-Color"
    assert [r["host"] for r in res["recent"]] == ["192.168.1.43"]

    res = client.post("/api/devices/rename", json={"host": "192.168.1.43", "nickname": "Big rig"}).json()
    assert res["remembered"]["nickname"] == "Big rig" and res["remembered"]["slug"] == "ASIAIR-Color"   # data stays put

    res = client.post("/api/devices/find", json={"subnet": "192.168.1.0/24"}).json()
    assert res["remembered"] is None and res["recent"][0]["nickname"] == "Big rig"   # a search disconnects

    monkeypatch.setattr(disc, "port_open", lambda host, **k: host == "192.168.1.43")
    monkeypatch.setattr(devmod, "identify", lambda host, **k: [d for d in found if d.host == host])
    res = client.post("/api/devices/connect", json={"host": "192.168.1.43"}).json()
    assert res["remembered"]["host"] == "192.168.1.43" and res["remembered"]["nickname"] == "Big rig"
    r = client.post("/api/devices/connect", json={"host": "192.168.1.77"})
    assert r.status_code == 404 and "192.168.1.77" in r.json()["detail"]
    assert client.get("/api/devices").json()["remembered"]["host"] == "192.168.1.43"   # never a silent switch
    assert client.post("/api/devices/connect", json={"host": "not-an-ip"}).status_code == 400

    res = client.post("/api/devices/forget", json={"host": "192.168.1.43"}).json()
    assert res["recent"] == [] and res["remembered"] is None


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


def test_start_over(tmp_path):
    import json

    client, air, root = make_app(tmp_path)
    state_dir = root / "state"
    client.get("/api/plan")
    stage = client.post("/api/stage/run").json()
    for _ in range(200):
        if client.get(f"/jobs/{stage['job_id']}").json()["status"] in ("succeeded", "failed"):
            break
    staging = tmp_path / "sandbox" / "_staging"
    assert any(staging.rglob("*.fit"))
    (state_dir / "cache" / "previews").mkdir(parents=True, exist_ok=True)
    (state_dir / "cache" / "previews" / "x.png").write_bytes(b"png")
    frame = next(p.relative_to(air).as_posix() for p in air.rglob("*.fit"))
    client.post("/api/answers", json={f"exclude:{frame}": "1", "quality-sigma": "5"})
    client.put("/api/session", json={"data": {"step": "review", "passed": ["connect", "scan"]}, "client": "w1"})
    (state_dir / "answers.json").write_text(json.dumps(json.loads((state_dir / "answers.json").read_text()) | {"target-x": "skip"}))

    pv = client.get("/api/start-over").json()
    assert pv["staged"]["files"] >= 1 and pv["left_out"] == 1 and pv["decision_answers"] == 1 and not pv["blocked"]
    res = client.post("/api/start-over", json={"forget_answers": False}).json()
    answers = json.loads((state_dir / "answers.json").read_text())
    assert answers == {"quality-sigma": "5", "target-x": "skip"}               # choices gone; σ and answers kept
    assert not any(staging.rglob("*.fit")) and not (state_dir / "cache" / "previews").exists()
    assert client.get("/api/session").json()["data"] == {}
    assert (tmp_path / "sandbox" / "Z95-ClaudeReferences" / "targets.csv").is_file()   # the share is untouched
    assert res["log"].endswith(".log")

    client.post("/api/start-over", json={})                                     # default: answers cleared too
    assert json.loads((state_dir / "answers.json").read_text()) == {"quality-sigma": "5"}


def test_start_over_refuses_a_staging_dir_that_is_the_share(tmp_path):
    import dataclasses

    import pytest

    from astro_ingest import startover
    client, air, root = make_app(tmp_path)
    cfg = Config.from_env({"ASTRO_ROOT": str(root), "ASTRO_NAS": str(tmp_path / "nas"), "STATE_DIR": str(root / "state"),
                           "TZ": "America/New_York", "ASIAIR_ROOT": str(air)})
    for bad in (root, root.parent, root / "state"):
        with pytest.raises(RuntimeError, match="refusing"):
            startover.run(dataclasses.replace(cfg, staging_dir=bad))
    assert (root / "Z95-ClaudeReferences" / "targets.csv").is_file()


def test_start_over_never_deletes_the_last_copy_of_a_frame(tmp_path):
    from astro_ingest import startover
    client, air, root = make_app(tmp_path)
    client.get("/api/plan")
    job = client.post("/api/stage/run").json()["job_id"]
    for _ in range(200):
        if client.get(f"/jobs/{job}").json()["status"] in ("succeeded", "failed"):
            break
    cfg = Config.from_env({"ASTRO_ROOT": str(root), "ASTRO_NAS": str(tmp_path / "nas"), "STATE_DIR": str(root / "state"),
                           "TZ": "America/New_York", "ASIAIR_ROOT": str(air)})
    staged = sorted((tmp_path / "sandbox" / "_staging" / "local").rglob("*.fit"))
    assert len(staged) == 4
    gone = air / staged[0].relative_to(tmp_path / "sandbox" / "_staging" / "local")
    gone.unlink()                                          # deleted from the device (e.g. released on Clean up)

    pv = startover.preview(cfg)["staged"]
    assert (pv["files"], pv["kept_gone"], pv["kept_unchecked"]) == (3, 1, 0)
    unreachable = startover.staged_files(cfg, opener=lambda c, s: None)
    assert len(unreachable["unchecked"]) == 4 and unreachable["unreachable"] == ["local"]

    res = startover.run(cfg)
    assert staged[0].is_file() and not any(p.is_file() for p in staged[1:])      # only the last copy stays
    assert res["kept_staged"] == [f"local/{gone.relative_to(air).as_posix()}"]
    assert "KEPT" in open(res["log"]).read()


def test_health_without_the_device(tmp_path):
    # the container's healthcheck: never waits on the (often switched off) ASIAIR
    client, air, root = make_app(tmp_path)
    assert client.get("/health", params={"device": "0"}).json() == {"status": "ok", "version": VERSION}


def test_a_folder_on_this_computer_end_to_end(tmp_path):
    # a NINA session on the capture PC: the page sends a manifest, then uploads; nothing on that computer is deleted
    import time

    from fitsgen import nina_frame

    nina, nas, root = tmp_path / "nina", tmp_path / "nas", tmp_path / "sandbox"
    for d in (nas, root / "Z95-ClaudeReferences"):
        d.mkdir(parents=True)
    (root / "Z95-ClaudeReferences" / "targets.csv").write_text(
        "folder,name,messier,ngc,ic,other\nSoulNebula-IC1848,SoulNebula,,,1848,\n")
    for i in range(4):
        nina_frame(nina, "SoulNebula", "light", f"2026-10-04T21:{10 + 5 * i:02d}:00", seq=i + 1)
    nina_frame(nina, "SoulNebula", "flat", "2026-10-05T06:30:10", exposure_s=2.5)
    cfg = Config.from_env({"ASTRO_ROOT": str(root), "ASTRO_NAS": str(nas), "STATE_DIR": str(root / "state"),
                           "TZ": "America/New_York", "STAGING_DIR": str(tmp_path / "staging")})
    client = TestClient(create_app(cfg))
    files = []
    for p in sorted(nina.rglob("*.fits")):
        raw = p.read_bytes()
        files.append({"rel": p.relative_to(nina).as_posix(), "size": len(raw), "mtime": p.stat().st_mtime,
                      "header_raw": raw[:raw.index(b"END" + b" " * 77) + 80].decode("latin-1")})
    res = client.post("/api/upload/manifest", json={"name": "NINA-2026-10-04", "files": files}).json()
    assert res["remembered"]["kind"] == "upload" and "5 FITS file(s)" in res["message"]
    plan = client.get("/api/plan").json()
    items = {i["src"]: i for i in plan["items"]}
    assert len(items) == 5 and all(i["action"] == "copy" and not i["staged"] for i in items.values())
    assert client.post("/api/stage/run").status_code == 409                 # the page uploads instead
    for f in files:
        data = (nina / f["rel"]).read_bytes()
        bad = client.put("/api/upload/file", params={"rel": f["rel"]}, content=data[:-10])
        assert bad.status_code == 400                                        # a short upload is refused
        assert client.put("/api/upload/file", params={"rel": f["rel"]}, content=data).status_code == 200
    assert client.put("/api/upload/file", params={"rel": "../escape.fits"}, content=b"x").status_code == 404
    plan = client.get("/api/plan").json()
    assert all(i["staged"] for i in plan["items"])
    job = client.post("/api/copy/run").json()["job_id"]
    for _ in range(300):
        snap = client.get(f"/jobs/{job}").json()
        if snap["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert snap["result"]["copied"] == 5, snap
    session = next((root / "SoulNebula-IC1848").glob("2026-10-04-SoulNebula-2600MC*"))
    assert len(list(session.glob("lights-L/*.fits"))) == 4 and len(list(session.glob("flats*/*.fits"))) == 1   # FILTER L
    clean = client.get("/api/cleanup/preview").json()
    assert clean["device_delete_allowed"] is False                            # nothing on that computer is deleted
    assert all(not g["selectable"] for g in clean["groups"])


def test_concurrent_state_writes_dont_collide(tmp_path):
    # found by the ZAP scan: a session save racing Start over renamed the other's .part away (500)
    import threading

    from astro_ingest import state
    cfg = Config.from_env({"ASTRO_ROOT": str(tmp_path), "ASTRO_NAS": str(tmp_path), "STATE_DIR": str(tmp_path / "s"),
                           "TZ": "America/New_York"})
    errors = []

    def writer(n):
        try:
            for i in range(200):
                state.write_json(cfg, tmp_path / "s" / "session.json", {"w": n, "i": i})
        except Exception as e:      # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and state.read_json(tmp_path / "s" / "session.json", None)["i"] == 199
    assert not list((tmp_path / "s").glob("*.part"))

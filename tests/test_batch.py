"""Copy batches end to end on tmp trees: stage -> score -> preview -> approve -> run -> re-plan."""

import hashlib

from fitsgen import asiair_frame, star_field
from jobwait import wait_job

from astro_ingest import analysis, batch, db, service, staging
from astro_ingest.config import Config
from astro_ingest.core import planner as P

SOUL = "SoulNebula-IC1848/2026-09-23-SoulNebula-2600MC-Z61"


def make(tmp_path):
    air, nas, root = tmp_path / "asiair", tmp_path / "nas", tmp_path / "sandbox"
    (root / "Z95-ClaudeReferences").mkdir(parents=True)
    (root / "Z95-ClaudeReferences" / "targets.csv").write_text(
        "folder,name,messier,ngc,ic,other\nSoulNebula-IC1848,SoulNebula,,,1848,\nHeartNebula-IC1805,HeartNebula,,,1805,\n")
    nas.mkdir()
    stamps = ["20260923-211420", "20260923-221420", "20260923-231420", "20260924-001420", "20260924-011420"]
    for i, t in enumerate(stamps):   # five Soul lights; the last is a twilight frame
        asiair_frame(air, "Plan/Light/SoulNebula", "Light", t, obj="SoulNebula", angle=3, seq=i + 1,
                     data=star_field(seed=i, background=6000 if i == 4 else 500, peak=600 if i == 4 else 3000))
    for i, t in enumerate(stamps[:4]):   # four Heart lights the same night, same scope and angle
        asiair_frame(air, "Plan/Light/HeartNebula", "Light", t.replace("2114", "2116"), obj="HeartNebula", angle=3,
                     seq=i + 1, data=star_field(seed=20 + i))
    for i in range(2):                   # flats shared by both sessions
        asiair_frame(air, "Autorun/Flat", "Flat", f"20260924-06330{i}", exposure_s=6.1, angle=185, seq=i + 1,
                     data=star_field(seed=40 + i, stars=0))
    cfg = Config.from_env({"ASTRO_ROOT": str(root), "ASTRO_NAS": str(nas), "STATE_DIR": str(root / "state"),
                           "TZ": "America/New_York", "ASIAIR_ROOT": str(air), "STAGING_DIR": str(tmp_path / "staging")})
    # near-identical synthetic frames make tiny groups over-sensitive; at the maximum sensitivity only the frame with
    # no stars at all (always flagged) is rejected. Flagging itself is tested in test_quality.py.
    service.set_answers(cfg, {"quality-sigma": "10"})
    return cfg, air, root


def stage_and_score(cfg):
    p = service.scan_and_plan(cfg, service.open_source(cfg))
    entries = {f.rel: f.entry for f in p.scan.frames}
    store = staging.StagingStore(cfg)
    todo = [entries[i.src] for i in p.plan.items if staging.needs_staging(store, p.source.slug, entries[i.src], i.action)
            if i.src in entries]
    staging.run_staging(cfg, p.source.source, p.source.slug, todo, False, lambda *a, **k: None)
    p.source.reload()
    p = service.replan(cfg, p)
    analysis.run_scoring(cfg, analysis.targets_by_group(cfg, p.source, p.scan, p.index, p.plan), lambda *a, **_: None)
    return service.replan(cfg, p)


def test_batch_end_to_end(tmp_path):
    cfg, air, root = make(tmp_path)
    p = stage_and_score(cfg)
    items = {i.src: i for i in p.plan.items}
    twilight = next(s for s in items if "SoulNebula" in s and s.endswith("_0005.fit"))
    assert items[twilight].action == P.REJECTED

    pv = batch.preview(p)
    flat_ops = [o for o in pv.copy_ops if "/flats/" in o["dst"]]
    assert len(flat_ops) == 4                                   # 2 flats x 2 sessions
    assert len(pv.copy_ops) == 4 + 4 + 4                        # 4 Soul lights (1 rejected), 4 Heart, 4 flat copies
    assert {d["folder"] for d in pv.destinations} == {SOUL, "HeartNebula-IC1805/2026-09-23-HeartNebula-2600MC-Z61"}
    assert any(twilight.rsplit("/", 1)[1] in c for c in pv.clear_after)   # rejected: staged copy cleared at the end

    batch_id = batch.approve(cfg, p, pv)
    assert (cfg.state_dir / "batches" / f"{batch_id}.tsv").is_file()
    result = batch.run(cfg, batch_id, lambda *a, **k: None)
    assert (result["copied"], result["already_there"], result["failed"], result["clashes"]) == (12, 0, [], [])
    assert result["count_problems"] == []

    for o in db.operations(cfg, batch_id):                      # every copy matches its staging hash
        data = (root / o["dst"]).read_bytes()
        assert o["status"] == "done" and hashlib.blake2b(data).hexdigest() == o["blake2b"]
    assert not list(root.rglob("*.part")) and not [x for x in root.rglob(".*") if x.name != ".part"]
    assert not list((tmp_path / "staging").rglob("*.fit"))      # filed and rejected copies cleared
    assert "OK\tcopy" in (cfg.state_dir / "logs" / f"copy-{batch_id}.log").read_text()
    assert db.batch(cfg, batch_id)["status"] == "done"

    # after re-indexing, everything filed is already on the NAS; the rejected frame stays rejected and isn't re-read
    p = service.reindex(cfg, p)
    items = {i.src: i for i in p.plan.items}
    assert sum(1 for i in items.values() if i.action == P.ALREADY) == 4 + 4 + 2
    assert items[twilight].action == P.REJECTED
    store = staging.StagingStore(cfg)
    entry = next(f.entry for f in p.scan.frames if f.rel == twilight)
    assert not staging.needs_staging(store, p.source.slug, entry, items[twilight].action)
    assert batch.preview(p).copy_ops == []                      # a second approval copies nothing


def test_resume_after_interruption(tmp_path):
    cfg, air, root = make(tmp_path)
    p = stage_and_score(cfg)
    batch_id = batch.approve(cfg, p, batch.preview(p))
    ops = db.operations(cfg, batch_id)
    first = next(o for o in ops if o["kind"] == "copy")
    # simulate a crash after one copy landed but before it was recorded, plus a half-written .part of the next
    (root / first["dst"]).parent.mkdir(parents=True, exist_ok=True)
    (root / first["dst"]).write_bytes(open(first["staged"], "rb").read())
    second = [o for o in ops if o["kind"] == "copy"][1]
    (root / second["dst"]).parent.mkdir(parents=True, exist_ok=True)
    (root / (second["dst"] + ".part")).write_bytes(b"half")
    result = batch.run(cfg, batch_id, lambda *a, **k: None)
    assert result["already_there"] == 1 and result["copied"] == len([o for o in ops if o["kind"] == "copy"]) - 1
    assert not list(root.rglob("*.part"))


def test_copy_through_the_api(tmp_path):

    from fastapi.testclient import TestClient

    from astro_ingest.api import create_app
    cfg, air, root = make(tmp_path)
    stage_and_score(cfg)
    client = TestClient(create_app(cfg))
    pv = client.get("/api/copy/preview").json()
    assert pv["copies"] == 12 and pv["unfinished_batch"] is None and len(pv["destinations"]) == 2
    run = client.post("/api/copy/run").json()
    snap = wait_job(client, run["job_id"])
    assert snap["status"] == "succeeded" and snap["result"]["copied"] == 12, snap
    assert snap["stats"]["files_done"] == 12
    assert client.get("/api/copy/preview").json()["copies"] == 0              # re-indexed: nothing left
    assert client.post("/api/copy/run").status_code == 400
    assert client.get("/api/batches").json()["batches"][0]["status"] == "done"


def _damaged_heart_frame(root, air):
    """A truncated copy of the first Heart light already in the write root, where the NAS session is."""
    good = sorted((air / "Plan/Light/HeartNebula").glob("*.fit"))[0]
    heart = "HeartNebula-IC1805/2026-09-23-HeartNebula-2600MC-Z61"
    bad = root / heart / "lights" / good.name
    bad.parent.mkdir(parents=True)
    bad.write_bytes(good.read_bytes()[:3000])
    return good, bad


def test_replacing_a_damaged_copy_counts_the_retired_file(tmp_path):
    cfg, air, root = make(tmp_path)
    good, bad = _damaged_heart_frame(root, air)
    p = stage_and_score(cfg)
    res = batch.run(cfg, batch.approve(cfg, p, batch.preview(p)), lambda *a, **k: None)
    assert [r["status"] for r in res["retired"]] == ["done"] and not res["count_problems"], res
    assert bad.read_bytes() == good.read_bytes() and (bad.parent / "_to_delete" / good.name).stat().st_size == 3000
    assert db.batches(cfg)[0]["status"] == "done"


def test_a_good_copy_is_never_retired(tmp_path):
    # the damaged file was already replaced (by hand, or an earlier run): nothing is moved, the copy is "already there"
    cfg, air, root = make(tmp_path)
    good, bad = _damaged_heart_frame(root, air)
    p = stage_and_score(cfg)
    bid = batch.approve(cfg, p, batch.preview(p))
    bad.write_bytes(good.read_bytes())
    res = batch.run(cfg, bid, lambda *a, **k: None)
    assert [r["status"] for r in res["retired"]] == ["skipped"] and not res["count_problems"], res
    assert not (bad.parent / "_to_delete").exists() and db.batches(cfg)[0]["status"] == "done"

"""The Catalog step on tmp trees: copy a batch, then PROJECT_INFO, targets.csv, index links and notes."""

import os

from fitsgen import asiair_frame
from test_batch import SOUL, make, stage_and_score

from astro_ingest import batch, catalog, db, fsops, service
from astro_ingest.config import Config

HEART = "HeartNebula-IC1805/2026-09-23-HeartNebula-2600MC-Z61"


def copied(tmp_path):
    cfg, air, root = make(tmp_path)
    p = stage_and_score(cfg)
    bid = batch.approve(cfg, p, batch.preview(p))
    batch.run(cfg, bid, lambda *a, **k: None)
    service.reindex(cfg, p)
    return cfg, root, bid


def test_catalog_after_a_copy(tmp_path):
    cfg, root, bid = copied(tmp_path)
    pv = catalog.preview(cfg)
    assert pv.batches == [bid] and pv.sessions == [HEART, SOUL]
    kinds = {(c.kind, c.path): c.status for c in pv.changes}
    assert kinds[("project-info", f"{SOUL}/PROJECT_INFO.txt")] == "create"
    assert kinds[("link-add", "102-ByICNumber/IC1848-SoulNebula")] == "add"
    assert kinds[("link-add", f"103-ByDate/{SOUL.split('/')[1]}")] == "add"
    assert kinds[("index-md", "ZZ_TARGET_INDEX.md")] == "create"
    assert not (root / SOUL / "PROJECT_INFO.txt").exists()          # preview writes nothing

    result = catalog.run(cfg, pv)
    info = (root / SOUL / "PROJECT_INFO.txt").read_text()
    assert "4 lights" in info and "Flat" in info and "SoulNebula-IC1848" in info
    link = root / "103-ByDate" / SOUL.split("/")[1]
    assert link.is_symlink() and os.readlink(link) == f"../{SOUL}" and link.resolve() == (root / SOUL).resolve()
    assert "`SoulNebula-IC1848`" in (root / "ZZ_TARGET_INDEX.md").read_text()
    log = (cfg.state_dir / "logs").glob("catalog-*.log")
    assert "OK\tproject-info" in next(log).read_text()
    assert result["writes"] == len([c for c in pv.changes if c.status != "unchanged"])
    assert db.uncatalogued_batches(cfg) == []
    assert not list(root.rglob("*.part")) and not list(root.rglob(".DS_Store"))

    again = catalog.preview(cfg)                                       # nothing left to do
    assert again.batches == [] and again.summary()["writes"] == 0


def test_new_target_row_and_sibling_notes(tmp_path):
    cfg, root, _ = copied(tmp_path)
    catalog.run(cfg, catalog.preview(cfg))
    # a second night of Soul and a brand-new target, copied by hand into the sandbox as if by a batch
    s2 = "SoulNebula-IC1848/2026-09-25-SoulNebula-2600MC-Z61"
    new = "NeedleGalaxy-NGC4565/2026-09-25-NeedleGalaxy-2600MC-Z61"
    ops = []
    for rel, obj in ((s2, "SoulNebula"), (new, "NGC 4565")):
        f = asiair_frame(root, f"{rel}/lights", "Light", "20260925-221000", obj=obj, angle=3, thumb=False)
        ops.append({"kind": "copy", "src": f.name, "staged": None, "dst": f"{rel}/lights/{f.name}", "size": 1,
                    "blake2b": "x"})
    db.create_batch(cfg, "b2", "test", ops, {})
    for o in db.operations(cfg, "b2"):
        db.set_operation(cfg, o["id"], "done", "", 0)
    db.set_batch(cfg, "b2", "done")

    pv = catalog.preview(cfg)
    assert [r["folder"] for r in pv.new_targets] == ["NeedleGalaxy-NGC4565"] and pv.new_targets[0]["ngc"] == "4565"
    notes = [c for c in pv.changes if c.kind == "notes"]
    assert sorted(c.path.split("/")[1] for c in notes) == ["2026-09-23-SoulNebula-2600MC-Z61",
                                                            "2026-09-25-SoulNebula-2600MC-Z61"]
    assert any("New target `NeedleGalaxy-NGC4565`" in x for x in pv.log_lines)
    old_csv = (root / "Z95-ClaudeReferences" / "targets.csv").read_text()
    catalog.run(cfg, pv)
    assert "NeedleGalaxy-NGC4565,NeedleGalaxy,,4565,," in (root / "Z95-ClaudeReferences" / "targets.csv").read_text()
    retired = list((root / "Z95-ClaudeReferences" / "_to_delete").glob("targets.csv.*"))
    assert len(retired) == 1 and retired[0].read_text() == old_csv   # source data: the old version is kept
    assert "Night 1 of 2. Siblings: ../2026-09-25-SoulNebula-2600MC-Z61" in (
        root / SOUL / ".project_notes.txt").read_text()
    assert os.readlink(root / "101-ByNGCNumber" / "NGC4565-NeedleGalaxy") == "../NeedleGalaxy-NGC4565"
    assert "NeedleGalaxy" in (cfg.state_dir / "decision-log-drafts.md").read_text()
    # notes are only ever added: a second run changes nothing
    assert catalog.preview(cfg).summary()["writes"] == 0


def test_catalog_refuses_while_another_writer_holds_the_lock(tmp_path):
    cfg, root, _ = copied(tmp_path)
    import pytest
    with fsops.WriteLock(cfg, "someone else"):
        with pytest.raises(fsops.LockHeld):
            catalog.run(cfg, catalog.preview(cfg))
    assert db.uncatalogued_batches(cfg)                                 # still waiting to be catalogued
    assert isinstance(cfg, Config)


def test_borrowed_flats_are_copied_and_noted(tmp_path):
    from fitsgen import star_field
    cfg, air, root = make(tmp_path)
    for i in range(4):                     # an earlier Soul night without flats, same rotation as the 09-23 flats
        asiair_frame(air, "Plan/Light/SoulNebula", "Light", f"20260920-22{i}000", obj="SoulNebula", angle=4,
                     seq=10 + i, data=star_field(seed=60 + i))
    p = stage_and_score(cfg)
    d = next(x for x in p.plan.decisions if x.kind == "borrow-flats")
    assert d.resolved == "none"
    service.set_answers(cfg, {d.id: d.options[1]["value"]})
    p = service.replan(cfg, p)
    pv = batch.preview(p)
    s20 = "SoulNebula-IC1848/2026-09-20-SoulNebula-2600MC-Z61"
    assert pv.borrowed == {s20: SOUL}
    batch.run(cfg, batch.approve(cfg, p, pv), lambda *a, **k: None)
    assert len(list((root / s20 / "flats").glob("*.fit"))) == 2

    cv = catalog.preview(cfg)
    note = next(c for c in cv.changes if c.kind == "flats-note")
    assert note.path == f"{s20}/.flats_are_copies" and note.status == "create"
    assert any("borrowed flats" in x for x in cv.log_lines)
    catalog.run(cfg, cv)
    assert "2026-09-23-SoulNebula-2600MC-Z61" in (root / s20 / ".flats_are_copies").read_text()


def test_catalog_through_the_api(tmp_path):
    import time

    from fastapi.testclient import TestClient

    from astro_ingest.api import create_app
    cfg, root, _ = copied(tmp_path)
    client = TestClient(create_app(cfg))
    pv = client.get("/api/catalog/preview").json()
    assert pv["summary"]["project_info"]["create"] == 2 and "text" not in pv["changes"][0]
    job = client.post("/api/catalog/run").json()["job_id"]
    for _ in range(500):
        snap = client.get(f"/jobs/{job}").json()
        if snap["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.02)
    assert snap["status"] == "succeeded", snap
    assert client.get("/api/catalog/preview").json()["summary"]["writes"] == 0

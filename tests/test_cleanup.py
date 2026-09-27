"""Clean up on tmp trees: only verified or explicitly ticked files are deleted, and every gate holds."""

import os
import shutil
import time

import pytest
from test_batch import SOUL, make, stage_and_score

from astro_ingest import batch, cleanup, db, service
from astro_ingest.sources.base import DeleteRefused, check_deletable, check_removable_dir

LATER = time.time() + 3600          # generated files are brand new: run "an hour later" so the capture guard passes
HEART = "HeartNebula-IC1805/2026-09-23-HeartNebula-2600MC-Z61"


def copied(tmp_path):
    cfg, air, root = make(tmp_path)
    p = stage_and_score(cfg)
    batch.run(cfg, batch.approve(cfg, p, batch.preview(p)), lambda *a, **k: None)
    return cfg, air, root, service.reindex(cfg, p)


def groups(pv):
    return {g["id"]: g for g in pv.groups()}


def test_preview_groups_after_a_copy(tmp_path):
    cfg, air, root, p = copied(tmp_path)
    g = groups(cleanup.preview(cfg, p))
    assert len(g["verified"]["items"]) == 4 + 4 + 2 and g["verified"]["recommended"] and not g["verified"]["ticked"]
    assert all(i["thumb"] and i["nas"] for i in g["verified"]["items"])
    assert [i["rel"].endswith("_0005.fit") for i in g["rejected"]["items"]] == [True]
    assert not g["rejected"]["ticked"] and "loses them" in g["rejected"]["note"]


def test_delete_verified_frames_with_thumbnails_and_prune(tmp_path):
    cfg, air, root, p = copied(tmp_path)
    pv = cleanup.preview(cfg, p)
    sel = [i["rel"] for i in groups(pv)["verified"]["items"]]
    before = {x.relative_to(air).as_posix() for x in air.rglob("*") if x.is_file()}
    cid = cleanup.approve(cfg, p, sel)
    res = cleanup.run(cfg, p, cid, now=LATER)
    assert res["deleted"] == 20 and not res["failed"] and not res["unexpected_missing"] and not res["still_there"]
    after = {x.relative_to(air).as_posix() for x in air.rglob("*") if x.is_file()}
    assert before - after == set(sel) | {s.replace(".fit", "_thn.jpg") for s in sel}
    assert not (air / "Plan/Light/HeartNebula").exists()          # emptied object folder removed
    assert (air / "Plan/Light/SoulNebula").is_dir()                # still holds the rejected frame
    assert (air / "Autorun/Flat").is_dir()                          # structural folders stay, even empty
    assert all((root / d).is_file() for i in groups(pv)["verified"]["items"] for d in i["nas"])   # NAS untouched
    log = (cfg.state_dir / "logs" / f"cleanup-{cid}.log").read_text()
    assert log.count("OK\t") == 21 and (cfg.state_dir / "cleanups" / f"{cid}.tsv").is_file()
    assert db.cleanup(cfg, cid)["status"] == "done"
    again = cleanup.run(cfg, p, cid, now=LATER)                     # re-running removes nothing more
    assert {x.relative_to(air).as_posix() for x in air.rglob("*") if x.is_file()} == after and not again["failed"]


def test_gates_changed_file_changed_nas_copy_and_capturing(tmp_path):
    cfg, air, root, p = copied(tmp_path)
    items = groups(cleanup.preview(cfg, p))["verified"]["items"]
    a, b = items[0], items[1]
    cid = cleanup.approve(cfg, p, [a["rel"], b["rel"]])
    assert cleanup.run(cfg, p, cid)["stopped"]                       # just written: looks like it's capturing
    assert (air / a["rel"]).exists()

    cid = cleanup.approve(cfg, p, [a["rel"], b["rel"]])
    with open(air / a["rel"], "ab") as f:                             # the device file changed since the scan
        f.write(b"x" * 2880)
    nas = root / b["nas"][0]                                          # the NAS copy changed (same size)
    data = bytearray(nas.read_bytes())
    data[-1] ^= 1
    nas.write_bytes(bytes(data))
    res = cleanup.run(cfg, p, cid, now=LATER)
    assert res["deleted"] == 0 and len(res["skipped"]) == 4           # both frames and their thumbnails kept
    assert "changed on the device" in res["skipped"][0]["detail"] and "its frame was kept" in res["skipped"][1]["detail"]
    assert (air / a["rel"]).exists() and (air / b["rel"]).exists() and (air / a["thumb"]).exists()


def test_only_offered_files_can_be_approved(tmp_path):
    cfg, air, root, p = copied(tmp_path)
    with pytest.raises(ValueError):
        cleanup.approve(cfg, p, ["Plan/Light/SoulNebula/nothing.fit"])
    pv = cleanup.preview(cfg, p)
    rejected = groups(pv)["rejected"]["items"][0]["rel"]
    cid = cleanup.approve(cfg, p, [rejected])                          # a callout, ticked on purpose
    assert cleanup.run(cfg, p, cid, now=LATER)["deleted"] == 2


def test_device_deletes_need_the_switch(tmp_path, monkeypatch):
    cfg, air, root, p = copied(tmp_path)
    monkeypatch.setattr(p.source, "local", False)
    monkeypatch.setattr(p.source, "label", "smb://192.168.1.43/EMMC Images")
    sel = [groups(cleanup.preview(cfg, p))["rejected"]["items"][0]["rel"]]
    assert not cleanup.preview(cfg, p).device_delete_allowed
    with pytest.raises(DeleteRefused):
        cleanup.approve(cfg, p, sel)


def test_verify_frames_already_on_the_nas(tmp_path):
    cfg, air, root = make(tmp_path)
    light = sorted((air / "Plan/Light/HeartNebula").glob("*.fit"))[0]
    nas_copy = tmp_path / "nas" / HEART / "lights" / light.name     # on the NAS before the app ever saw it
    nas_copy.parent.mkdir(parents=True)
    shutil.copy(light, nas_copy)
    rel = light.relative_to(air).as_posix()
    p = service.scan_and_plan(cfg, service.open_source(cfg))
    item = next(c for c in cleanup.preview(cfg, p).candidates if c.rel == rel)
    assert item.group == "to-verify"
    res = cleanup.verify(cfg, p)
    assert res["verified"] == 1 and res["bytes"] == light.stat().st_size
    item = next(c for c in cleanup.preview(cfg, p).candidates if c.rel == rel)
    assert item.group == "verified" and item.nas[0][0] == f"{HEART}/lights/{light.name}"

    data = bytearray(nas_copy.read_bytes())                           # a different NAS copy, same size
    data[-1] ^= 1
    nas_copy.write_bytes(bytes(data))
    os.utime(light, (LATER, LATER))                                    # the device file changed: verify again
    p = service.scan_and_plan(cfg, service.open_source(cfg))
    assert next(c for c in cleanup.preview(cfg, p).candidates if c.rel == rel).group == "to-verify"
    assert cleanup.verify(cfg, p)["mismatch"] == 1
    assert next(c for c in cleanup.preview(cfg, p).candidates if c.rel == rel).group == "differs"


def test_path_guards():
    for bad in ("Live/x.fit", "log/a.txt", "GuidingDarkLibrary/d.fit", "Plan/../Live/x.fit", "Plan", "/Plan/x.fit",
                "System Volume Information/x", "Plan\\Light\\x.fit"):
        with pytest.raises(DeleteRefused):
            check_deletable(bad)
    check_deletable("Plan/Light/M 13/Light_x.fit")
    check_removable_dir("Plan/Light/M 13")
    for bad in ("Plan/Light", "Autorun/Flat", "Autorun", "Autorun/Flat/sub"):
        with pytest.raises(DeleteRefused):
            check_removable_dir(bad)


def test_verify_thorough_reads_everything(tmp_path):
    cfg, air, root = make(tmp_path)
    light = sorted((air / "Plan/Light/HeartNebula").glob("*.fit"))[0]
    nas_copy = tmp_path / "nas" / HEART / "lights" / light.name
    nas_copy.parent.mkdir(parents=True)
    shutil.copy(light, nas_copy)
    rel = light.relative_to(air).as_posix()
    p = service.scan_and_plan(cfg, service.open_source(cfg))
    with pytest.raises(ValueError):
        cleanup.verify(cfg, p, method="fast")
    res = cleanup.verify(cfg, p, method="thorough")
    assert res["verified"] == 1 and res["bytes"] == light.stat().st_size
    item = next(c for c in cleanup.preview(cfg, p).candidates if c.rel == rel)
    assert item.group == "verified" and "thorough" in item.how and not item.nas[0][1].startswith("quick:")


class NotSupportedForMacFiles:
    """Wraps a source like the ASIAIR's file server: deleting a Mac metadata file removes it but reports an error;
    a "._" file vanishes with its partner."""

    def __init__(self, source, air, stubborn=()):
        self.source, self.air, self.stubborn = source, air, set(stubborn)

    def __getattr__(self, name):
        return getattr(self.source, name)

    def delete(self, rel):
        name = rel.rsplit("/", 1)[-1]
        if rel in self.stubborn:
            raise OSError("STATUS_NOT_SUPPORTED")
        self.source.delete(rel)
        folder = rel.rsplit("/", 1)[0]
        buddy = self.air / folder / f"._{name}"
        if buddy.exists():
            buddy.unlink()                                             # the metadata goes with its file
        if name == ".DS_Store" or name.startswith("._"):
            raise OSError("STATUS_NOT_SUPPORTED")


def test_mac_metadata_goes_with_its_file(tmp_path):
    cfg, air, root, p = copied(tmp_path)
    frame = next(c for c in cleanup.preview(cfg, p).candidates if c.group == "verified" and c.thumb)
    folder, name = frame.rel.rsplit("/", 1)
    (air / folder / f"._{name}").write_bytes(b"x" * 4096)              # a Mac browsed the share
    (air / "Autorun").mkdir(exist_ok=True)
    (air / "Autorun" / ".DS_Store").write_bytes(b"d" * 6148)
    (air / "Autorun" / "._.DS_Store").write_bytes(b"m" * 4096)
    (air / "Plan" / ".DS_Store").write_bytes(b"d" * 6148)
    p = service.scan_and_plan(cfg, service.open_source(cfg))
    pv = cleanup.preview(cfg, p)
    c = next(x for x in pv.candidates if x.rel == frame.rel)
    assert [x[0] for x in c.companions] == [f"{folder}/._{name}"] and c.files == 3
    others = sorted(x.rel for x in pv.candidates if x.group == "other")
    assert "Autorun/._.DS_Store" not in others and "Autorun/.DS_Store" in others   # ._.DS_Store goes with .DS_Store

    p.source = NotSupportedForMacFiles(p.source, air, stubborn={"Plan/.DS_Store"})
    cid = cleanup.approve(cfg, p, [frame.rel, "Autorun/.DS_Store", "Plan/.DS_Store"])
    res = cleanup.run(cfg, p, cid, now=LATER)
    assert not res["failed"] and not res["unexpected_missing"] and not res["still_there"], res
    assert not (air / folder / f"._{name}").exists() and not (air / "Autorun" / ".DS_Store").exists()
    assert not (air / "Autorun" / "._.DS_Store").exists()
    kept = {s["rel"]: s["detail"] for s in res["skipped"]}
    assert "Mac metadata" in kept["Plan/.DS_Store"] and (air / "Plan" / ".DS_Store").exists()

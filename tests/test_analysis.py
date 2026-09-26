"""Scoring pipeline: which frames are scored (incl. NAS peers), caching, and per-group flags."""

from fitsgen import asiair_frame, star_field

from astro_ingest import analysis, service
from astro_ingest.config import Config

SESSION = "SoulNebula-IC1848/2026-09-23-SoulNebula-2600MC-Z61"


def make_world(tmp_path, with_peers=True):
    air, nas, root = tmp_path / "asiair", tmp_path / "nas", tmp_path / "sandbox"
    (root / "Z95-ClaudeReferences").mkdir(parents=True)
    (root / "Z95-ClaudeReferences" / "targets.csv").write_text(
        "folder,name,messier,ngc,ic,other\nSoulNebula-IC1848,SoulNebula,,,1848,\n")
    nas.mkdir()
    for i in range(4):  # on the ASIAIR: three good frames and one twilight frame, destined for an existing session
        data = star_field(seed=i, background=6000 if i == 3 else 500, peak=600 if i == 3 else 3000)
        stamp = ["20260923-211420", "20260923-221420", "20260923-231420", "20260924-001420"][i]
        asiair_frame(air, "Plan/Light/SoulNebula", "Light", stamp, obj="SoulNebula", angle=3,
                     seq=i + 1, data=data, BAYERPAT="RGGB")
    if with_peers:  # six good frames of the same night already on the NAS
        for i in range(6):
            asiair_frame(nas, f"{SESSION}/lights", "Light", f"20260924-0{i}1420", obj="SoulNebula", angle=3,
                         seq=10 + i, data=star_field(seed=10 + i), thumb=False)
    cfg = Config.from_env({"ASTRO_ROOT": str(root), "ASTRO_NAS": str(nas), "STATE_DIR": str(root / "state"),
                           "TZ": "America/New_York", "ASIAIR_ROOT": str(air)})
    return cfg


def test_targets_include_nas_peers(tmp_path):
    cfg = make_world(tmp_path)
    p = service.scan_and_plan(cfg, service.open_source(cfg))
    targets = analysis.targets_by_group(cfg, p.source, p.scan, p.index, p.plan)
    (group,) = targets.values()
    assert sum(1 for t in group if t.src) == 4                   # the ASIAIR frames (with thumbnails)
    assert sum(1 for t in group if t.src is None) == 6           # NAS peers from the destination session


def test_scoring_caches_and_flags_per_group(tmp_path):
    cfg = make_world(tmp_path)
    p = service.scan_and_plan(cfg, service.open_source(cfg))
    targets = analysis.targets_by_group(cfg, p.source, p.scan, p.index, p.plan)
    messages = []
    first = analysis.run_scoring(cfg, targets, lambda pct, msg: messages.append(msg))
    assert (first["scored"], first["failed"]) == (10, 0) and messages
    again = analysis.run_scoring(cfg, targets, lambda pct, msg: None)
    assert again["scored"] == 0 and again["skipped_cached"] == 10     # nothing re-read
    assert (cfg.state_dir / "quality.json").is_file()
    assert len(list((cfg.cache_dir / "previews").glob("*-320.png"))) == 4

    planned = service.replan(cfg, p)
    by_src = {i.src: i for i in planned.plan.items}
    twilight = next(s for s in by_src if s.endswith("_0004.fit"))
    assert by_src[twilight].action == "rejected" and by_src[twilight].quality["peers"] == 10
    assert sum(1 for i in planned.plan.items if i.action == "append") == 3   # session is already on the NAS
    assert planned.sigma == 4.0

    # sensitivity and keep choices come from answers.json, applied without re-reading anything
    service.set_answers(cfg, {f"keep:{twilight}": "keep"})
    assert {i.src: i for i in service.replan(cfg, p).plan.items}[twilight].action == "append"


def test_write_guard_on_state(tmp_path):
    cfg = make_world(tmp_path, with_peers=False)
    import pytest

    from astro_ingest.config import WriteGuardError
    from astro_ingest.state import write_json
    with pytest.raises(WriteGuardError):
        write_json(cfg, tmp_path / "nas" / "x.json", {})

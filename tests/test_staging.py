"""Staging: one read per frame, verified; resumable; the source is never written; StagedSource prefers the copy."""

import hashlib

import pytest
from fitsgen import asiair_frame, star_field

from astro_ingest import staging
from astro_ingest.config import Config, WriteGuardError
from astro_ingest.sources.local import LocalDirSource


class CountingSource(LocalDirSource):
    """A local source that counts bytes read, and can fail partway (a device dropping off Wi-Fi)."""

    def __init__(self, root, fail_after=None):
        super().__init__(root)
        self.reads, self.fail_after = 0, fail_after

    def open_read(self, rel):
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            raise ConnectionResetError("device went away")
        return super().open_read(rel)


def make(tmp_path, n=4):
    air, root = tmp_path / "asiair", tmp_path / "sandbox"
    root.mkdir()
    for i in range(n):
        asiair_frame(air, "Plan/Light/SoulNebula", "Light", f"20260923-21{i}420", obj="SoulNebula", seq=i + 1,
                     data=star_field(seed=i))
    cfg = Config.from_env({"ASTRO_ROOT": str(root), "ASTRO_NAS": str(tmp_path / "nas"),
                           "STATE_DIR": str(root / "state"), "TZ": "America/New_York",
                           "STAGING_DIR": str(tmp_path / "staging")})
    entries = [e for e in LocalDirSource(air).walk() if e.rel.endswith(".fit")]
    return cfg, air, entries


def test_stage_copies_verified_and_records(tmp_path):
    cfg, air, entries = make(tmp_path)
    before = {p: p.read_bytes() for p in air.rglob("*")  if p.is_file()}
    progress = []
    result = staging.run_staging(cfg, LocalDirSource(air), "dev", entries, True, lambda p, m, **k: progress.append((m, k)))
    assert result["staged"] == 4 and progress
    last = progress[-1][1]   # structured stats for the Stage page
    assert (last["files_done"], last["files_total"], last["bytes_done"]) == (4, 4, last["bytes_total"])
    store = staging.StagingStore(cfg)
    for e in entries:
        staged = cfg.staging_dir / "dev" / e.rel
        assert staged.read_bytes() == (air / e.rel).read_bytes()
        assert store.get("dev", e)["blake2b"] == hashlib.blake2b(staged.read_bytes()).hexdigest()
    assert not list(cfg.staging_dir.rglob("*.part"))
    assert {p: p.read_bytes() for p in air.rglob("*") if p.is_file()} == before     # source untouched


def test_resume_reads_each_frame_once(tmp_path):
    cfg, air, entries = make(tmp_path)
    flaky = CountingSource(air, fail_after=2)
    with pytest.raises(staging.StagingError, match="run Stage again"):
        staging.run_staging(cfg, flaky, "dev", entries, True, lambda p, m, **k: None)
    assert sum(1 for e in entries if staging.StagingStore(cfg).get("dev", e)) == 2
    again = CountingSource(air)
    result = staging.run_staging(cfg, again, "dev", entries, True, lambda p, m, **k: None)
    assert (result["staged"], result["already_staged"], again.reads) == (2, 2, 2)


def test_changed_source_is_restaged(tmp_path):
    cfg, air, entries = make(tmp_path, n=1)
    staging.run_staging(cfg, LocalDirSource(air), "dev", entries, True, lambda p, m, **k: None)
    e = entries[0]
    changed = type(e)(e.rel, e.size, e.mtime + 60)       # same file listed with a new mtime
    assert staging.StagingStore(cfg).get("dev", changed) is None


def test_staged_source_prefers_the_copy(tmp_path):
    cfg, air, entries = make(tmp_path, n=2)
    staging.run_staging(cfg, LocalDirSource(air), "dev", entries[:1], True, lambda p, m, **k: None)
    counting = CountingSource(air)
    src = staging.StagedSource(counting, cfg, "dev", local=False)
    list(src.walk())
    assert src.staged(entries[0].rel) and not src.staged(entries[1].rel)
    assert src.is_fast(entries[0].rel) and not src.is_fast(entries[1].rel)
    with src.open_read(entries[0].rel) as f:
        f.read()
    assert counting.reads == 0                                  # served from staging, not the device
    with src.open_read(entries[1].rel) as f:
        f.read()
    assert counting.reads == 1


def test_staging_writes_are_guarded(tmp_path):
    cfg, air, entries = make(tmp_path, n=1)
    with pytest.raises(ValueError):
        staging.StagingStore(cfg).staged_path("dev", "../../etc/x")
    assert cfg.check_writable(cfg.staging_dir / "dev" / "x.fit")          # staging is writable...
    with pytest.raises(WriteGuardError):
        cfg.check_writable(tmp_path / "nas" / "x.fit")                   # ...the NAS lookup root is not


def test_device_slug():
    assert staging.device_slug({"nickname": "ASIAIR Color", "host": "192.168.1.43"}, False) == "ASIAIR-Color"
    assert staging.device_slug({"label": "ZWO ASIAIR", "host": "192.168.1.43"}, False) == "ZWO-ASIAIR-192.168.1.43"
    assert staging.device_slug(None, True) == "local"

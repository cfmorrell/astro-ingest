"""The only code that writes to the share: verified no-clobber copies, retiring, the single-writer lock."""

import hashlib
import json
import os

import pytest

from astro_ingest import fsops
from astro_ingest.config import Config, WriteGuardError


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "root").mkdir()
    return Config.from_env({"ASTRO_ROOT": str(tmp_path / "root"), "ASTRO_NAS": str(tmp_path / "nas"),
                            "STATE_DIR": str(tmp_path / "root" / "state"), "TZ": "America/New_York",
                            "STAGING_DIR": str(tmp_path / "staging")})


def staged_file(tmp_path, data=b"frame data " * 1000, name="f.fit"):
    p = tmp_path / "staging" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p, len(data), hashlib.blake2b(data).hexdigest()


def test_copy_verify_and_rerun(cfg, tmp_path):
    src, size, h = staged_file(tmp_path)
    counted = []
    assert fsops.copy_verified(cfg, src, "Target/2026-09-23-Target-2600MC-Z61/lights/f.fit", size, h,
                               counted.append) == ("done", "copied and verified")
    dst = cfg.astro_root / "Target/2026-09-23-Target-2600MC-Z61/lights/f.fit"
    assert dst.read_bytes() == src.read_bytes() and sum(counted) == size
    assert not list(cfg.astro_root.rglob("*.part"))
    assert fsops.copy_verified(cfg, src, "Target/2026-09-23-Target-2600MC-Z61/lights/f.fit", size, h)[0] == "already-there"


def test_never_overwrites(cfg, tmp_path):
    src, size, h = staged_file(tmp_path)
    dst = cfg.astro_root / "T/s/lights/f.fit"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"something else")
    status, detail = fsops.copy_verified(cfg, src, "T/s/lights/f.fit", size, h)
    assert status == "clash" and dst.read_bytes() == b"something else" and "not overwritten" in detail


def test_checksum_mismatch_leaves_nothing(cfg, tmp_path):
    src, size, _ = staged_file(tmp_path)
    status, _ = fsops.copy_verified(cfg, src, "T/s/lights/f.fit", size, "0" * 128)
    assert status == "failed"
    assert not (cfg.astro_root / "T/s/lights/f.fit").exists() and not list(cfg.astro_root.rglob("*.part"))


def test_leftover_part_from_a_crash_is_replaced(cfg, tmp_path):
    src, size, h = staged_file(tmp_path)
    part = cfg.astro_root / "T/s/lights/f.fit.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"half a file")
    assert fsops.copy_verified(cfg, src, "T/s/lights/f.fit", size, h)[0] == "done"
    assert not part.exists()


def test_refuses_dotfiles_and_escapes(cfg, tmp_path):
    src, size, h = staged_file(tmp_path)
    for bad in ("T/.DS_Store", "T/s/._f.fit", "../outside/f.fit", "T//f.fit"):
        with pytest.raises((ValueError, WriteGuardError)):
            fsops.copy_verified(cfg, src, bad, size, h)


def test_fallback_rename_without_hard_links(cfg, tmp_path, monkeypatch):
    src, size, h = staged_file(tmp_path)
    import errno
    monkeypatch.setattr(os, "link", lambda a, b: (_ for _ in ()).throw(OSError(errno.EPERM, "no hard links")))
    assert fsops.copy_verified(cfg, src, "T/s/lights/f.fit", size, h)[0] == "done"
    assert (cfg.astro_root / "T/s/lights/f.fit").read_bytes() == src.read_bytes()


def test_retire_moves_to_to_delete(cfg):
    bad = cfg.astro_root / "T/s/lights/f.fit"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"truncated")
    assert fsops.retire(cfg, "T/s/lights/f.fit")[0] == "done"
    assert (cfg.astro_root / "T/s/lights/_to_delete/f.fit").read_bytes() == b"truncated" and not bad.exists()
    bad.write_bytes(b"truncated again")                                    # a second retire never clobbers
    assert fsops.retire(cfg, "T/s/lights/f.fit")[0] == "clash" and bad.exists()
    assert fsops.retire(cfg, "T/s/lights/missing.fit")[0] == "skipped"     # e.g. only on the read-only NAS in dev


def test_lock_refuses_a_second_writer_and_recovers_stale(cfg):
    with fsops.WriteLock(cfg, "first"):
        with pytest.raises(fsops.LockHeld):
            with fsops.WriteLock(cfg, "second"):
                pass
    lock = cfg.state_dir / "ingest.lock"
    assert not lock.exists()
    lock.write_text(json.dumps({"holder": "crashed", "pid": 999999, "host": os.uname().nodename, "since": 0}))
    with fsops.WriteLock(cfg, "after a crash"):
        assert "after a crash" in lock.read_text()

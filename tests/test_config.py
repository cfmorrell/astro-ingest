import os

import pytest

from astro_ingest.config import Config, ConfigError, WriteGuardError


def make_env(tmp_path, **overrides):
    env = {
        "ASTRO_ROOT": str(tmp_path / "root"),
        "ASTRO_NAS": str(tmp_path / "nas"),
        "STATE_DIR": str(tmp_path / "root" / "Z95-ClaudeReferences" / "ingest"),
        "TZ": "America/New_York",
    }
    env.update(overrides)
    return env


def test_from_env_defaults(tmp_path):
    cfg = Config.from_env(make_env(tmp_path))
    assert cfg.asiair_root is None
    assert cfg.asiair_subnet is None   # detected unless overridden
    assert cfg.asiair_share == "EMMC Images"
    assert cfg.asiair_host is None
    assert str(cfg.tz) == "America/New_York"


def test_missing_required_var(tmp_path):
    env = make_env(tmp_path)
    del env["ASTRO_ROOT"]
    with pytest.raises(ConfigError, match="ASTRO_ROOT"):
        Config.from_env(env)


def test_bad_timezone(tmp_path):
    with pytest.raises(ConfigError, match="TZ"):
        Config.from_env(make_env(tmp_path, TZ="Mars/Olympus_Mons"))


def test_write_guard(tmp_path):
    cfg = Config.from_env(make_env(tmp_path))
    (tmp_path / "root").mkdir()
    assert cfg.check_writable(tmp_path / "root" / "Target" / "new.fit")
    assert cfg.check_writable(cfg.state_dir / "ingest.sqlite3")
    for bad in (tmp_path / "nas" / "x.fit", tmp_path / "root" / ".." / "nas" / "x.fit", "/etc/passwd"):
        with pytest.raises(WriteGuardError):
            cfg.check_writable(bad)


def test_write_guard_refuses_symlink_escape(tmp_path):
    cfg = Config.from_env(make_env(tmp_path))
    (tmp_path / "root").mkdir()
    (tmp_path / "nas").mkdir()
    os.symlink(tmp_path / "nas", tmp_path / "root" / "escape")
    with pytest.raises(WriteGuardError):
        cfg.check_writable(tmp_path / "root" / "escape" / "x.fit")


def test_old_astro_archive_name_still_accepted(tmp_path):
    env = make_env(tmp_path)
    del env["ASTRO_NAS"]
    env["ASTRO_ARCHIVE"] = "/astro"
    assert str(Config.from_env(env).astro_nas) == "/astro"
    env["ASTRO_NAS"] = "/nas"
    assert str(Config.from_env(env).astro_nas) == "/nas"

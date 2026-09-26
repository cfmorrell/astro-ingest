"""Environment-derived settings and the write guard.

Every path comes from the environment (see .env.example); nothing is hard-coded. The write guard is the one
place that decides whether the app may write to a path: only under ASTRO_ROOT (the share being ingested into),
STATE_DIR (the app's own state), CACHE_DIR (disposable renders) or STAGING_DIR (frames read once from the device). Source deletes on the ASIAIR go through the Source interface, not here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

# Bumped by hand with each release, like astro-stacker. Stays under 1.0 until Chris has run it long enough to
# trust it. Shown in the UI header and from GET /health.
VERSION = "0.1"


class ConfigError(Exception):
    pass


class WriteGuardError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    astro_root: Path
    astro_nas: Path
    state_dir: Path
    tz: ZoneInfo
    asiair_root: Path | None
    asiair_subnet: str
    asiair_share: str
    asiair_host: str | None
    cache_dir: Path  # disposable renders (thumbnails, lightbox previews); default STATE_DIR/cache
    staging_dir: Path  # one full read of each selected frame from the device; production: a separate UnRAID share
    assumed_wifi_mb_s: float  # transfer-time estimate until a staging run has measured the real rate
    # Clean up may delete from a real device (SMB) only when this is set. Off in dev (Chris, 2026-09-26: backups of
    # the ASIAIR and the NAS first); a local-folder source (the scratch copy) can always be cleaned up.
    allow_device_delete: bool = False

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = os.environ if env is None else env

        def required(name: str) -> Path:
            value = env.get(name, "").strip()
            if not value:
                raise ConfigError(f"{name} is not set (see .env.example)")
            return Path(value)

        def optional(name: str) -> str | None:
            value = env.get(name, "").strip()
            return value or None

        tz_name = env.get("TZ", "").strip() or "America/New_York"
        try:
            tz = ZoneInfo(tz_name)
        except Exception as e:
            raise ConfigError(f"TZ={tz_name!r} is not a known time zone") from e

        asiair_root = optional("ASIAIR_ROOT")
        state_dir = required("STATE_DIR")
        return cls(
            astro_root=required("ASTRO_ROOT"),
            # ASTRO_ARCHIVE is the old name for ASTRO_NAS; still accepted so existing .env files keep working
            astro_nas=required("ASTRO_NAS" if env.get("ASTRO_NAS", "").strip() or "ASTRO_ARCHIVE" not in env
                               else "ASTRO_ARCHIVE"),
            state_dir=state_dir,
            tz=tz,
            asiair_root=Path(asiair_root) if asiair_root else None,
            asiair_subnet=optional("ASIAIR_SUBNET") or "192.168.1.0/24",
            asiair_share=optional("ASIAIR_SHARE") or "EMMC Images",
            asiair_host=optional("ASIAIR_HOST"),
            cache_dir=Path(optional("CACHE_DIR") or state_dir / "cache"),
            # Chris: staging is its own share (/mnt/user/astro-ingest-staging, mounted at /staging). The fallback keeps
            # things working without it; "_" never matches a target folder.
            staging_dir=Path(optional("STAGING_DIR") or Path(env["ASTRO_ROOT"].strip()) / "_staging"),
            assumed_wifi_mb_s=_positive_float(optional("ASSUMED_WIFI_MB_S"), 10.0),
            allow_device_delete=(optional("ALLOW_DEVICE_DELETE") or "0") == "1",
        )

    def check_writable(self, path: str | os.PathLike) -> Path:
        """Return `path` if the app may write there, else raise WriteGuardError.

        Symlinks are resolved first, so a link inside ASTRO_ROOT that points elsewhere is refused.
        """
        real = Path(os.path.realpath(path))
        for root in (self.astro_root, self.state_dir, self.cache_dir, self.staging_dir):
            if real.is_relative_to(os.path.realpath(root)):
                return Path(path)
        raise WriteGuardError(f"refusing to write outside ASTRO_ROOT/STATE_DIR: {path}")


def _positive_float(value: str | None, default: float) -> float:
    try:
        f = float(value) if value else default
    except ValueError:
        raise ConfigError(f"not a number: {value!r}") from None
    if f <= 0:
        raise ConfigError(f"must be positive: {value!r}")
    return f

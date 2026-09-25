"""Environment-derived settings and the write guard.

Every path comes from the environment (see .env.example); nothing is hard-coded. The write guard is the one
place that decides whether the app may write to a path: only under ASTRO_ROOT (the archive being filed into)
or STATE_DIR (the app's own state). Source deletes on the ASIAIR go through the Source interface, not here.
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
    astro_archive: Path
    state_dir: Path
    tz: ZoneInfo
    asiair_root: Path | None
    asiair_subnet: str
    asiair_share: str
    asiair_host: str | None

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
        return cls(
            astro_root=required("ASTRO_ROOT"),
            astro_archive=required("ASTRO_ARCHIVE"),
            state_dir=required("STATE_DIR"),
            tz=tz,
            asiair_root=Path(asiair_root) if asiair_root else None,
            asiair_subnet=optional("ASIAIR_SUBNET") or "192.168.1.0/24",
            asiair_share=optional("ASIAIR_SHARE") or "EMMC Images",
            asiair_host=optional("ASIAIR_HOST"),
        )

    def check_writable(self, path: str | os.PathLike) -> Path:
        """Return `path` if the app may write there, else raise WriteGuardError.

        Symlinks are resolved first, so a link inside ASTRO_ROOT that points elsewhere is refused.
        """
        real = Path(os.path.realpath(path))
        for root in (self.astro_root, self.state_dir):
            if real.is_relative_to(os.path.realpath(root)):
                return Path(path)
        raise WriteGuardError(f"refusing to write outside ASTRO_ROOT/STATE_DIR: {path}")

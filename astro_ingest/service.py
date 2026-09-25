"""Wiring shared by the CLI and the web app: config -> source, NAS index, targets -> scan and plan."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from astro_ingest.config import Config
from astro_ingest.core.nas import NasIndex, build_index, merge
from astro_ingest.core.planner import Plan, build_plan
from astro_ingest.core.scan import Scan, scan
from astro_ingest.core.targets import Target, load_targets
from astro_ingest.sources.base import Source
from astro_ingest.sources.local import LocalDirSource

TARGETS_CSV = Path("Z95-ClaudeReferences") / "targets.csv"


class SourceUnavailable(Exception):
    pass


def open_source(cfg: Config, override: str | Path | None = None) -> Source:
    """The capture source: a local directory (ASIAIR_ROOT or --source). SMB arrives in phase 3."""
    root = Path(override) if override else cfg.asiair_root
    if root is None:
        raise SourceUnavailable("no ASIAIR_ROOT set (direct SMB to the ASIAIR arrives in phase 3)")
    if not root.is_dir():
        raise SourceUnavailable(f"source {root} is not reachable")
    return LocalDirSource(root)


def nas_index(cfg: Config) -> NasIndex:
    """Index of the live share, merged with the write target when they differ (dev sandbox)."""
    index = build_index(cfg.astro_nas)
    if Path(cfg.astro_root).resolve() != Path(cfg.astro_nas).resolve() and Path(cfg.astro_root).is_dir():
        index = merge(index, build_index(cfg.astro_root))
    return index


def targets(cfg: Config) -> list[Target]:
    """targets.csv from the write target (the copy the app will update), else the live share's."""
    for root in (cfg.astro_root, cfg.astro_nas):
        path = Path(root) / TARGETS_CSV
        if path.is_file():
            return load_targets(path)
    raise FileNotFoundError(f"no {TARGETS_CSV} under ASTRO_ROOT or ASTRO_NAS")


@dataclass
class Planned:
    scan: Scan
    plan: Plan
    scanned_at: dt.datetime


def scan_and_plan(cfg: Config, source: Source, answers: dict[str, str] | None = None, progress=None) -> Planned:
    result = scan(source, cfg.tz, progress=progress)
    return Planned(result, build_plan(result, nas_index(cfg), targets(cfg), answers), dt.datetime.now(cfg.tz))

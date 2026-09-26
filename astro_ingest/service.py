"""Wiring shared by the CLI and the web app: config -> source, NAS index, targets -> scan and plan."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from astro_ingest import analysis, state
from astro_ingest.config import Config
from astro_ingest.core import quality
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


ANSWERS_FILE = "answers.json"


def load_answers(cfg: Config) -> dict[str, str]:
    """Chris's answers to plan decisions, {decision_id: answer}, plus per-frame keep/reject choices
    ("keep:<source rel>") and settings ("quality-sigma"). Kept in STATE_DIR (decisions move to SQLite in phase 4)."""
    return state.read_json(Path(cfg.state_dir) / ANSWERS_FILE, {})


def set_answers(cfg: Config, updates: dict[str, str | None]) -> dict[str, str]:
    """Merge updates into answers.json (a None value removes the key) and return the new answers."""
    answers = load_answers(cfg)
    for key, value in updates.items():
        if value is None:
            answers.pop(key, None)
        else:
            answers[key] = value
    state.write_json(cfg, Path(cfg.state_dir) / ANSWERS_FILE, answers)
    return answers


def sigma(answers: dict[str, str]) -> float:
    try:
        return float(answers.get(analysis.SIGMA_KEY, quality.INGEST_ANOMALY_Z_THRESHOLD))
    except ValueError:
        return quality.INGEST_ANOMALY_Z_THRESHOLD


DEVICES_FILE = "devices.json"


def remembered_device(cfg: Config) -> dict | None:
    """The capture device Chris picked (kind, host, server_guid, …), from STATE_DIR/devices.json."""
    return state.read_json(Path(cfg.state_dir) / DEVICES_FILE, {}).get("selected")


def remember_device(cfg: Config, device, nickname: str | None = None) -> dict:
    """Remember `device` (by kind + address) as the one to ingest from, with Chris's name for it."""
    record = {**device.to_dict(), "nickname": nickname}
    state.write_json(cfg, Path(cfg.state_dir) / DEVICES_FILE, {"selected": record})
    return record


@dataclass
class Planned:
    scan: Scan
    plan: Plan
    scanned_at: dt.datetime
    index: NasIndex
    targets: list[Target]
    source: Source
    sigma: float


def replan(cfg: Config, planned: Planned, answers: dict[str, str] | None = None) -> Planned:
    """Plan again from an existing scan (new answers, new frame scores, new sensitivity): no re-reading."""
    answers = load_answers(cfg) if answers is None else answers
    base = build_plan(planned.scan, planned.index, planned.targets, answers)
    s = sigma(answers)
    scores = analysis.score_groups(cfg, planned.source, planned.scan, planned.index, base, s)
    plan = build_plan(planned.scan, planned.index, planned.targets, answers, scores) if scores else base
    return Planned(planned.scan, plan, planned.scanned_at, planned.index, planned.targets, planned.source, s)


def scan_and_plan(cfg: Config, source: Source, answers: dict[str, str] | None = None, progress=None) -> Planned:
    result = scan(source, cfg.tz, progress=progress)
    planned = Planned(result, None, dt.datetime.now(cfg.tz), nas_index(cfg), targets(cfg), source, 0.0)
    return replan(cfg, planned, answers)

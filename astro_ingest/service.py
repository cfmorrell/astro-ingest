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
    """The capture source.

    - `override` "smb" -> the remembered device over SMB; "smb://host/share" -> that share; anything else -> a local
      directory.
    - No override: ASIAIR_ROOT (a local directory, e.g. the dev sample) if set, else the remembered device over SMB.
    """
    override = str(override) if override else None
    if override and override.startswith("smb://"):
        host, _, share = override[len("smb://"):].partition("/")
        return _smb(host, share or cfg.asiair_share)
    if override == "smb" or (override is None and cfg.asiair_root is None):
        device = remembered_device(cfg)
        if not device:
            raise SourceUnavailable("no capture device picked yet: run `astro-ingest find`")
        return _smb(device["host"], device["share"])
    root = Path(override) if override else cfg.asiair_root
    if not root.is_dir():
        raise SourceUnavailable(f"source {root} is not reachable")
    return LocalDirSource(root)


def _smb(host: str, share: str) -> Source:
    from astro_ingest.sources.smb import SmbSource
    try:
        return SmbSource(host, share)
    except Exception as exc:  # offline, refused, ...
        raise SourceUnavailable(f"can't reach smb://{host}/{share}: {type(exc).__name__}: {exc}") from exc


def nas_index(cfg: Config) -> NasIndex:
    """Index of the live share, merged with the write target when they differ (dev sandbox; the write target wins)."""
    index = build_index(cfg.astro_nas)
    if Path(cfg.astro_root).resolve() != Path(cfg.astro_nas).resolve() and Path(cfg.astro_root).is_dir():
        # the write target first: where both have the same path, its file is the current one (a damaged live copy
        # already replaced in the sandbox must not look damaged again)
        index = merge(build_index(cfg.astro_root), index)
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


RECENT_MAX = 10


def _devices(cfg: Config) -> dict:
    """STATE_DIR/devices.json: {"selected": record | None, "recent": [record, ...]} (most recent first). A record is
    the device (kind, label, host, share, …) plus Chris's name for it, a `slug` fixed at first connect (it names the
    device's staging folder and its Verify results, so a rename never orphans them) and `last_connected`."""
    from astro_ingest import staging
    data = state.read_json(Path(cfg.state_dir) / DEVICES_FILE, {})
    sel = data.get("selected")
    recent = data.get("recent")
    if recent is None:                     # before recent devices (2026-09-27): only the selected one
        recent = [sel] if sel else []
    for r in recent + ([sel] if sel else []):
        if r and not r.get("slug"):
            r["slug"] = staging.device_slug(r, False)   # the name its data is already filed under
    if sel:
        sel = next((r for r in recent if _same(r, sel)), sel)
    return {"selected": sel, "recent": recent}


def _same(a: dict, b: dict) -> bool:
    return a.get("kind") == b.get("kind") and a.get("host") == b.get("host")


def _save_devices(cfg: Config, data: dict) -> None:
    state.write_json(cfg, Path(cfg.state_dir) / DEVICES_FILE, {"selected": data["selected"], "recent": data["recent"]})


def remembered_device(cfg: Config) -> dict | None:
    """The capture device currently connected (kind, host, slug, nickname, …), or None."""
    return _devices(cfg)["selected"]


def recent_devices(cfg: Config) -> list[dict]:
    return _devices(cfg)["recent"]


def remember_device(cfg: Config, device, nickname: str | None = None) -> dict:
    """Connect to `device`: it becomes the selected device and moves to the top of the recent list. A device already
    known at that address keeps its name (unless a new one is given) and its slug."""
    import time
    from astro_ingest import staging
    data = _devices(cfg)
    d = device.to_dict() if hasattr(device, "to_dict") else dict(device)
    old = next((r for r in data["recent"] if _same(r, d)), None)
    record = {**(old or {}), **d, "nickname": (nickname or "").strip() or (old or {}).get("nickname"),
              "last_connected": time.time()}
    if not record.get("slug"):
        taken = {r.get("slug") for r in data["recent"]}
        base = slug = staging.device_slug(record, False)
        n = 2
        while slug in taken:
            slug, n = f"{base}-{n}", n + 1
        record["slug"] = slug
    data["recent"] = [record] + [r for r in data["recent"] if not _same(r, record)][:RECENT_MAX - 1]
    data["selected"] = record
    _save_devices(cfg, data)
    return record


def rename_device(cfg: Config, host: str, nickname: str) -> dict:
    data = _devices(cfg)
    rec = next((r for r in data["recent"] if r.get("host") == host), None)
    if rec is None:
        raise KeyError(host)
    rec["nickname"] = nickname.strip() or None
    if data["selected"] and _same(data["selected"], rec):
        data["selected"] = rec
    _save_devices(cfg, data)
    return rec


def forget_device(cfg: Config, host: str) -> None:
    """Drop a device from the recent list (it disconnects if it's the one connected). Its staged files and Verify
    results stay on disk under its slug."""
    data = _devices(cfg)
    data["recent"] = [r for r in data["recent"] if r.get("host") != host]
    if data["selected"] and data["selected"].get("host") == host:
        data["selected"] = None
    _save_devices(cfg, data)


def disconnect_device(cfg: Config) -> None:
    data = _devices(cfg)
    data["selected"] = None
    _save_devices(cfg, data)


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


def reindex(cfg: Config, planned: Planned) -> Planned:
    """After files were written to the share: rebuild the NAS index and plan again (no re-reading the source)."""
    planned = Planned(planned.scan, planned.plan, planned.scanned_at, nas_index(cfg), planned.targets,
                      planned.source, planned.sigma)
    return replan(cfg, planned)


def staged_source(cfg: Config, source: Source):
    """Wrap the device source so staged copies are read locally (see staging.py)."""
    from astro_ingest import staging
    if isinstance(source, staging.StagedSource):
        return source
    local = isinstance(source, LocalDirSource)
    return staging.StagedSource(source, cfg, staging.device_slug(remembered_device(cfg), local), local)


def transfer_rate(cfg: Config) -> tuple[float, str]:
    """(MB/s, "measured" | "assumed") for the Select step's rough estimate."""
    from astro_ingest import staging
    rate = staging.StagingStore(cfg).rate
    if rate and rate.get("mb_s"):
        return float(rate["mb_s"]), "measured"
    return cfg.assumed_wifi_mb_s, "assumed"


def scan_and_plan(cfg: Config, source: Source, answers: dict[str, str] | None = None, progress=None) -> Planned:
    source = staged_source(cfg, source)
    result = scan(source, cfg.tz, progress=progress)
    planned = Planned(result, None, dt.datetime.now(cfg.tz), nas_index(cfg), targets(cfg), source, 0.0)
    return replan(cfg, planned, answers)

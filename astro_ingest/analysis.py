"""Frame-quality scoring and preview rendering around the pure core (core/quality.py, core/imaging.py).

- Which frames to score: every light in a planned light group that still has something to ingest, plus the lights
  already in that group's destination session on the NAS (peers: a group judged against the whole night, not just
  the frames still on the ASIAIR). Frames already on the NAS are read from the NAS copy (same bytes, local read).
- Each frame is read once: stats go to STATE_DIR/quality.json (keyed by path + size + mtime, so rescans reuse
  them) and its thumbnail to CACHE_DIR.
- Flags are not stored: score_groups() recomputes the robust z-scores per group from the stored stats at the
  chosen sensitivity, so changing the sensitivity never needs a re-read.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from astro_ingest import state
from astro_ingest.config import Config
from astro_ingest.core import imaging, quality
from astro_ingest.core import planner as P
from astro_ingest.core.nas import NasIndex
from astro_ingest.core.scan import Scan
from astro_ingest.sources.base import Source

THUMB_SIZE = 320     # astro-stacker's review-card size
FULL_SIZE = 1600     # astro-stacker's lightbox size
QUALITY_FILE = "quality.json"
SIGMA_KEY = "quality-sigma"   # in answers.json


def src_key(rel: str, size: int, mtime: float) -> str:
    return f"src|{rel}|{size}|{int(mtime)}"


def nas_key(rel: str, size: int) -> str:
    return f"nas|{rel}|{size}"


def render_path(cfg: Config, key: str, size: int) -> Path:
    return cfg.cache_dir / "previews" / f"{hashlib.sha1(key.encode()).hexdigest()}-{size}.png"


class QualityStore:
    """Frame stats in STATE_DIR/quality.json: {key: FrameStats dict}."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.path = Path(cfg.state_dir) / QUALITY_FILE
        self.frames: dict[str, dict] = state.read_json(self.path, {}).get("frames", {})

    def get(self, key: str) -> quality.FrameStats | None:
        d = self.frames.get(key)
        return quality.FrameStats.from_dict(d) if d else None

    def put(self, key: str, stats: quality.FrameStats) -> None:
        d = asdict(stats)
        d.pop("anomaly_z", None)
        d.pop("flagged", None)
        self.frames[key] = d

    def save(self) -> None:
        state.write_json(self.cfg, self.path, {"version": 1, "frames": self.frames})


@dataclass
class ScoreTarget:
    key: str                         # stats key
    name: str                        # filename (for FrameStats)
    open: Callable[[], BinaryIO]
    src: str | None                  # source rel when this is a frame on the ASIAIR (gets a thumbnail)
    thumb_key: str | None            # cache key for its thumbnail


def _nas_opener(cfg: Config, rel: str) -> Callable[[], BinaryIO]:
    def opener() -> BinaryIO:
        for root in (cfg.astro_nas, cfg.astro_root):
            p = Path(root) / rel
            if p.is_file():
                return open(p, "rb")
        raise FileNotFoundError(rel)
    return opener


def targets_by_group(cfg: Config, source: Source, scan: Scan, index: NasIndex, plan: P.Plan) -> dict[str, list[ScoreTarget]]:
    """{light group id: frames to score (its own frames, then NAS peers)} for groups with something to ingest."""
    entries = {f.rel: f.entry for f in scan.frames}
    items = {i.src: i for i in plan.items}
    out: dict[str, list[ScoreTarget]] = {}
    for g in plan.groups:
        if g.kind != "lights":
            continue
        group_items = [items[r] for r in g.items if r in items]
        if not any(i.action not in (P.ALREADY, P.IGNORED) for i in group_items):
            continue
        targets, names = [], set()
        for i in group_items:
            e = entries[i.src]
            name = i.src.rsplit("/", 1)[-1]
            names.add(name)
            skey = src_key(e.rel, e.size, e.mtime)
            if i.action == P.ALREADY and i.ingested_at:
                nas_rel = i.ingested_at[0]
                targets.append(ScoreTarget(nas_key(nas_rel, e.size), name, _nas_opener(cfg, nas_rel), i.src, skey))
            else:
                targets.append(ScoreTarget(skey, name, lambda rel=e.rel: source.open_read(rel), i.src, skey))
        for folder in g.dst_folders[:1]:  # peers: lights already in the destination session
            prefix = folder + "/"
            for name, files in sorted(index.files.items()):
                if name in names or not name.lower().endswith(".fit"):
                    continue
                for f in files:
                    if f.rel.startswith(prefix):
                        targets.append(ScoreTarget(nas_key(f.rel, f.size), name, _nas_opener(cfg, f.rel), None, None))
        out[g.id] = targets
    return out


def run_scoring(cfg: Config, targets: dict[str, list[ScoreTarget]], progress: Callable[[float, str], None]) -> dict:
    """Score (and thumbnail) every target not already in the store. Returns counts for the job result."""
    store = QualityStore(cfg)
    todo = [t for ts in targets.values() for t in ts
            if store.get(t.key) is None or (t.thumb_key and not render_path(cfg, t.thumb_key, THUMB_SIZE).is_file())]
    done = failed = 0
    started = time.time()
    for n, t in enumerate(todo, 1):
        progress((n - 1) / len(todo) * 100.0, f"scoring {t.src or t.key.split('|')[1]} ({n}/{len(todo)})")
        try:
            with t.open() as fh:
                data, header = imaging.load_fits(fh)
            if store.get(t.key) is None:
                store.put(t.key, quality.analyze_array(data, t.name, header.get("DATE-OBS")))
            if t.thumb_key:
                png = imaging.render_array(data, header, max_size=THUMB_SIZE, stretch="unlinked",
                                           debayer="BAYERPAT" in header)
                state.write_bytes(cfg, render_path(cfg, t.thumb_key, THUMB_SIZE), png)
            done += 1
        except Exception as exc:  # one bad frame must not stop the rest; it stays unscored and is reported
            failed += 1
            progress((n - 1) / len(todo) * 100.0, f"FAILED {t.key}: {type(exc).__name__}: {exc}")
        if n % 25 == 0:
            store.save()
    store.save()
    return {"scored": done, "failed": failed, "skipped_cached": sum(len(v) for v in targets.values()) - len(todo),
            "seconds": round(time.time() - started, 1)}


def score_groups(cfg: Config, source: Source, scan: Scan, index: NasIndex, plan: P.Plan,
                 sigma: float) -> dict[str, dict]:
    """{source rel: {"stats": ..., "anomaly_z": ..., "flagged": bool, "peers": n}} for every scored light frame,
    flagged against its group (own frames + NAS peers) at `sigma`. Frames not scored yet are simply absent."""
    store = QualityStore(cfg)
    out: dict[str, dict] = {}
    for gid, targets in targets_by_group(cfg, source, scan, index, plan).items():
        pairs = [(t, store.get(t.key)) for t in targets]
        pairs = [(t, s) for t, s in pairs if s is not None]
        if not any(t.src for t, _ in pairs):
            continue
        stats = [s for _, s in pairs]
        quality.flag_anomalies(stats, z_threshold=sigma)
        for t, s in pairs:
            if t.src:
                out[t.src] = {"stats": {k: v for k, v in asdict(s).items() if k not in ("anomaly_z", "flagged")},
                              "anomaly_z": s.anomaly_z, "flagged": s.flagged, "peers": len(stats), "group": gid}
    return out

"""Read-only index of the NAS: the Astronomy share (or the dev sandbox copy of it).

One walk collects what the planner needs:
- every raw frame by filename (to recognize frames already ingested, and where),
- every session folder with its camera/scope/night, whether it has flats, and its lights' rotation angles.
The calibration library index reads headers and is built separately (`calibration_library`).

Walk rules (CLAUDE.md): never follow symlinks; only target folders (A–Y) and the 001/002 libraries; skip
dotfiles and `_to_delete/`.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from astro_ingest.core import asiair, rules
from astro_ingest.core.fits import HeaderError, first, header, num

FRAME_FILE = re.compile(r"\.(fits?|fts|xisf)$", re.I)
LIBRARIES = ("001-MasterBias", "002-MasterDarks")
RETIRED = "_to_delete"


@dataclass(frozen=True)
class NasFile:
    rel: str    # path relative to the share root
    size: int


@dataclass
class Session:
    target_folder: str
    folder: str
    parsed: rules.SessionName | None
    has_flats: bool = False
    light_angles: set[int] = field(default_factory=set)
    light_count: int = 0

    @property
    def rel(self) -> str:
        return f"{self.target_folder}/{self.folder}"


@dataclass
class NasIndex:
    root: Path
    files: dict[str, list[NasFile]] = field(default_factory=lambda: defaultdict(list))
    sessions: list[Session] = field(default_factory=list)
    target_folders: list[str] = field(default_factory=list)

    def find(self, name: str) -> list[NasFile]:
        return self.files.get(name, [])

    def sessions_on(self, night: dt.date) -> list[Session]:
        return [s for s in self.sessions if s.parsed and s.parsed.night == night]

    def session(self, target_folder: str, folder: str) -> Session | None:
        return next((s for s in self.sessions if s.target_folder == target_folder and s.folder == folder), None)


def merge(*indexes: NasIndex) -> NasIndex:
    """Union of several indexes (dev: the live share plus whatever has been ingested into the sandbox).

    Paths are relative to each index's own root, so the same relative path in two indexes is one location.
    """
    merged = NasIndex(indexes[0].root)
    sessions: dict[str, Session] = {}
    for index in indexes:
        for name, files in index.files.items():
            seen = {f.rel for f in merged.files.get(name, [])}
            merged.files[name].extend(f for f in files if f.rel not in seen)
        for s in index.sessions:
            if s.rel in sessions:
                existing = sessions[s.rel]
                existing.has_flats |= s.has_flats
                existing.light_angles |= s.light_angles
                existing.light_count = max(existing.light_count, s.light_count)
            else:
                sessions[s.rel] = Session(s.target_folder, s.folder, s.parsed, s.has_flats, set(s.light_angles),
                                          s.light_count)
        for t in index.target_folders:
            if t not in merged.target_folders:
                merged.target_folders.append(t)
    merged.sessions = sorted(sessions.values(), key=lambda s: s.rel)
    merged.target_folders.sort()
    return merged


def _entries(path: Path) -> list[os.DirEntry]:
    with os.scandir(path) as it:
        return sorted((e for e in it if not asiair.is_junk(e.name)), key=lambda e: e.name)


def _walk_files(root: Path, start: Path, index: NasIndex, on_file=None) -> None:
    """Record every frame file under `start` (no symlinks, no _to_delete); call on_file(dir_rel_parts, name)."""
    stack = [start]
    while stack:
        d = stack.pop()
        for e in _entries(d):
            if e.is_symlink():
                continue
            if e.is_dir():
                if e.name != RETIRED:
                    stack.append(Path(e.path))
            elif e.is_file() and FRAME_FILE.search(e.name):
                rel = Path(e.path).relative_to(root).as_posix()
                index.files[e.name].append(NasFile(rel, e.stat().st_size))
                if on_file:
                    on_file(Path(e.path).parent.relative_to(start).parts, e.name)


def build_index(root: str | Path) -> NasIndex:
    root = Path(root)
    index = NasIndex(root)
    for e in _entries(root):
        if e.is_symlink() or not e.is_dir():
            continue
        if e.name in LIBRARIES:
            _walk_files(root, Path(e.path), index)
        elif rules.is_target_folder(e.name):
            index.target_folders.append(e.name)
            _index_target(root, Path(e.path), index)
    return index


def _index_target(root: Path, target: Path, index: NasIndex) -> None:
    for e in _entries(target):
        if e.is_symlink() or not e.is_dir() or e.name == RETIRED:
            continue
        session = Session(target.name, e.name, rules.parse_session_name(e.name))

        def on_file(parts: tuple[str, ...], name: str, s: Session = session) -> None:
            sub = parts[0].lower() if parts else ""
            if sub.startswith("flats"):
                s.has_flats = True
            elif sub.startswith("lights"):
                s.light_count += 1
                fn = asiair.parse_name(name)
                if fn and fn.angle is not None:
                    s.light_angles.add(fn.angle)

        _walk_files(root, Path(e.path), index, on_file)
        if session.parsed:
            index.sessions.append(session)


# ---------------------------------------------------------------- calibration library

@dataclass(frozen=True)
class CalibrationSet:
    kind: str                 # "Bias" | "Dark"
    rel: str                  # folder relative to the share root
    count: int
    camera: rules.Camera | None
    exposure_s: float | None
    gain: str | None
    offset: str | None
    temp_c: float | None      # mean CCD-TEMP of the first and last frames
    date: dt.date | None      # from the folder name (local date the set started)


_DATE_FOLDER = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def calibration_library(root: str | Path) -> list[CalibrationSet]:
    """Every leaf folder of frames in 001-MasterBias / 002-MasterDarks, described from its headers.

    Port of projinfo.build_index(): reads the first and last frame of each folder.
    """
    root = Path(root)
    sets = []
    for base, kind in zip(LIBRARIES, ("Bias", "Dark")):
        top = root / base
        if not top.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(top):  # os.walk does not follow symlinks by default
            dirnames[:] = sorted(d for d in dirnames if d != RETIRED and not asiair.is_junk(d))
            frames = sorted(f for f in filenames if FRAME_FILE.search(f) and not asiair.is_junk(f)
                            and not os.path.islink(os.path.join(dirpath, f)))
            if not frames:
                continue
            try:
                h0 = header(os.path.join(dirpath, frames[0]))
                h1 = header(os.path.join(dirpath, frames[-1]))
            except (OSError, HeaderError):
                continue
            temps = [t for t in (num(first(h, "CCD-TEMP")) for h in (h0, h1)) if t is not None]
            m = _DATE_FOLDER.match(os.path.basename(dirpath))
            sets.append(CalibrationSet(
                kind=kind,
                rel=Path(dirpath).relative_to(root).as_posix(),
                count=len(frames),
                camera=rules.camera_from(first(h0, "INSTRUME")) or rules.camera_from(dirpath),
                exposure_s=num(first(h0, "EXPTIME", "EXPOSURE")),
                gain=first(h0, "GAIN"),
                offset=first(h0, "OFFSET"),
                temp_c=sum(temps) / len(temps) if temps else None,
                date=dt.date.fromisoformat(m.group(1)) if m else None,
            ))
    return sets

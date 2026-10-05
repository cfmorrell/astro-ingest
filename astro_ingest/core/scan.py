"""Index a capture source: every ASIAIR frame with its parsed name, header, night and thumbnail.

Headers are read only for frames in handled folders (Autorun, Plan); ignored folders (Live, Preview, …) are
listed but never opened. Nothing is written.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable
from zoneinfo import ZoneInfo

from astro_ingest.core import asiair, rules
from astro_ingest.core.fits import HeaderError, first, read_header
from astro_ingest.sources.base import Source, SourceEntry

_IMAGETYP = {"light": "Light", "flat": "Flat", "dark": "Dark", "bias": "Bias", "offset": "Bias",
             "dark flat": "DarkFlat", "darkflat": "DarkFlat", "flat dark": "DarkFlat"}


@dataclass
class SourceFrame:
    entry: SourceEntry
    category: str                        # handled | ignored | unknown
    name: asiair.FrameName | None        # None: not an ASIAIR frame name (masters, stacks, previews)
    thumb: SourceEntry | None = None
    header: dict[str, str] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def rel(self) -> str:
        return self.entry.rel

    @property
    def folder(self) -> str:
        return str(PurePosixPath(self.entry.rel).parent)

    @property
    def kind(self) -> str | None:
        """Frame type: IMAGETYP from the header, falling back to the filename prefix."""
        imagetyp = (self.header or {}).get("IMAGETYP", "").strip().lower()
        if imagetyp in _IMAGETYP:
            return _IMAGETYP[imagetyp]
        return self.name.type if self.name else None

    @property
    def start_local(self) -> dt.datetime | None:
        """Local time the exposure started (filename save time minus the exposure)."""
        if not self.name:
            return None
        return self.name.saved_local - dt.timedelta(seconds=self.name.exposure_s)

    @property
    def night(self) -> dt.date | None:
        start = self.start_local
        return rules.night_of(start) if start else None

    @property
    def size_with_thumb(self) -> int:
        return self.entry.size + (self.thumb.size if self.thumb else 0)


@dataclass
class Scan:
    source_label: str
    frames: list[SourceFrame]
    orphan_thumbs: list[SourceEntry]   # _thn.jpg with no .fit next to it (Autorun/Plan)
    unrecognized: list[SourceEntry]    # anything else in Autorun/Plan (other tools' files, ._*, .DS_Store): offered
                                       # for cleanup, called out one by one, never deleted without Chris's approval
    other_files: list[SourceEntry]     # outside Autorun/Plan (logs, videos, junk): never touched

    def handled(self) -> list[SourceFrame]:
        return [f for f in self.frames if f.category == "handled"]


def scan(source: Source, tz: ZoneInfo, read_headers: bool = True,
         progress: Callable[[int, int], None] | None = None) -> Scan:
    if getattr(source, "layout", "asiair") == "folder":
        return _scan_folder(source, tz, read_headers, progress)
    entries = list(source.walk())
    by_rel = {e.rel: e for e in entries}
    frames: list[SourceFrame] = []
    orphans: list[SourceEntry] = []
    unrecognized: list[SourceEntry] = []
    others: list[SourceEntry] = []

    for e in entries:
        name = PurePosixPath(e.rel).name
        handled = asiair.folder_category(e.rel) == "handled"
        if name.endswith(asiair.THUMB_SUFFIX) and asiair.fit_for_thumb(e.rel) in by_rel:
            continue  # paired with its frame
        if not handled and (asiair.is_junk(name) or not name.lower().endswith(asiair.FIT_SUFFIX)):
            others.append(e)
            continue
        if asiair.is_junk(name):  # ._x.fit is AppleDouble metadata, not a frame
            unrecognized.append(e)
            continue
        if name.endswith(asiair.THUMB_SUFFIX):  # no .fit next to it (Video/Live ones went to other_files above)
            orphans.append(e)
            continue
        if not name.lower().endswith(asiair.FIT_SUFFIX):
            unrecognized.append(e)
            continue
        frame = SourceFrame(e, asiair.folder_category(e.rel), asiair.parse_name(name),
                            thumb=by_rel.get(asiair.thumb_for(e.rel)))
        if frame.category == "handled" and frame.name is None:
            frame.warnings.append("filename is not an ASIAIR frame name")
        frames.append(frame)

    todo = [f for f in frames if read_headers and f.category == "handled"]
    for i, frame in enumerate(todo):
        try:
            with source.open_read(frame.rel) as fh:
                frame.header = read_header(fh, frame.rel)
        except (OSError, HeaderError) as exc:
            frame.warnings.append(f"cannot read header: {exc}")
        else:
            _check(frame, tz)
        if progress:
            progress(i + 1, len(todo))

    return Scan(source.label, frames, orphans, unrecognized, others)


def _check(frame: SourceFrame, tz: ZoneInfo) -> None:
    """Cross-checks between the filename and the header."""
    if not frame.name or not frame.header:
        return
    kind = frame.kind
    if kind != frame.name.type and not (kind == "DarkFlat" and frame.name.type == "Dark"):
        frame.warnings.append(f"IMAGETYP says {kind}, filename says {frame.name.type}")
    lag = rules.clock_mismatch(frame.name.saved_local, first(frame.header, "DATE-OBS"),
                               frame.name.exposure_s, tz)
    if lag is not None:
        frame.warnings.append(
            f"filename time is {lag.total_seconds():+.0f} s from DATE-OBS + exposure: check the ASIAIR clock/time zone")


def _scan_folder(source: Source, tz: ZoneInfo, read_headers: bool, progress) -> Scan:
    """A folder of FITS frames in any layout (e.g. a NINA session): every FITS file anywhere under it is a frame,
    and what it is comes from its header, or its path (inference.infer_name) unless it's ASIAIR-named."""
    from astro_ingest.core.inference import FITS_SUFFIXES, infer_name
    frames: list[SourceFrame] = []
    others: list[SourceEntry] = []
    for e in source.walk():
        name = PurePosixPath(e.rel).name
        if asiair.is_junk(name) or not name.lower().endswith(FITS_SUFFIXES):
            others.append(e)          # never ingested, never touched (files on the user's computer)
            continue
        frames.append(SourceFrame(e, "handled", asiair.parse_name(name)))
    header_for = getattr(source, "header_for", None)
    for i, frame in enumerate(frames):
        if read_headers:
            try:
                if header_for is not None:
                    frame.header = header_for(frame.rel)
                else:
                    with source.open_read(frame.rel) as fh:
                        frame.header = read_header(fh, frame.rel)
            except (OSError, HeaderError) as exc:
                frame.warnings.append(f"cannot read header: {exc}")
        if frame.name is None:
            frame.name = infer_name(frame.header, frame.rel, tz, frame.entry.mtime)
            if frame.name is None:
                frame.warnings.append("can't tell what this frame is: no IMAGETYP or exposure time in its header "
                                      "or its name")
        elif frame.header:
            _check(frame, tz)
        if progress:
            progress(i + 1, len(frames))
    return Scan(source.label, frames, [], [], others)

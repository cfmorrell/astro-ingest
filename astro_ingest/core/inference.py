"""What a FITS frame is, from its header (and its path as a fallback): for frames that aren't ASIAIR-named.

Chris, 2026-10-05: ingest from any folder (e.g. a NINA session on the capture PC), found by a recursive search, and
"categorize them by file name and/or fits headers to identify target, exposure time, filter, instrument, image type,
and any other necessary metadata". This builds the same FrameName the ASIAIR filename parser does, so the planner,
scoring and filing rules work unchanged.

Headers first (NINA, SharpCap, SGP, Voyager and APT all write the standard keywords); the file and folder names only
fill gaps: a folder or file called FLAT/DARK/BIAS/DARKFLAT/LIGHT, a "300s"/"300.00s" exposure, a "_0012" sequence.
Written without real NINA data yet: assumptions to check against Chris's first session are marked ASSUMPTION.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from astro_ingest.core import rules
from astro_ingest.core.asiair import FrameName
from astro_ingest.core.fits import first, num

FITS_SUFFIXES = (".fit", ".fits", ".fts")

# IMAGETYP values seen in the wild (NINA: LIGHT/FLAT/DARK/BIAS/DARKFLAT; MaxIm/SGP: "Light Frame", ...)
_TYPES = {
    "light": "Light", "light frame": "Light", "lightframe": "Light", "object": "Light", "science": "Light",
    "flat": "Flat", "flat frame": "Flat", "flat field": "Flat", "flatfield": "Flat", "skyflat": "Flat",
    "dark": "Dark", "dark frame": "Dark", "darkframe": "Dark",
    "bias": "Bias", "bias frame": "Bias", "biasframe": "Bias", "offset": "Bias", "zero": "Bias",
    "darkflat": "DarkFlat", "dark flat": "DarkFlat", "flat dark": "DarkFlat", "flatdark": "DarkFlat",
}
# path words, most specific first (a DARKFLAT folder must not read as DARK or FLAT)
_PATH_TYPES = [("darkflat", "DarkFlat"), ("flatdark", "DarkFlat"), ("dark_flat", "DarkFlat"), ("flat_dark", "DarkFlat"),
               ("bias", "Bias"), ("offset", "Bias"), ("dark", "Dark"), ("flat", "Flat"), ("light", "Light")]
_SEQ = re.compile(r"(?:^|[_\-. ])(\d{3,6})$")
_EXP = re.compile(r"(?:^|[_\- ])(\d+(?:\.\d+)?)s(?:ec)?(?:[_\- .]|$)", re.I)


def frame_type(header: dict[str, str] | None, rel: str) -> str | None:
    """Light/Flat/Dark/Bias/DarkFlat from IMAGETYP, else from words in the path (file name, then folders)."""
    t = (header or {}).get("IMAGETYP", "").strip().strip("'").strip().lower()
    if t in _TYPES:
        return _TYPES[t]
    parts = [p.lower() for p in PurePosixPath(rel).parts]
    for part in reversed(parts):              # the file name first, then its folders, innermost first
        words = re.split(r"[^a-z_]+", part.replace("-", "_"))
        for key, kind in _PATH_TYPES:
            if any(w == key or w.startswith(key + "_") or w.endswith("_" + key) for w in words):
                return kind
    return None


def _object(header: dict[str, str], rel: str, kind: str) -> str | None:
    obj = first(header, "OBJECT", "OBJNAME")
    if obj:
        return obj.strip()
    if kind != "Light":
        return None
    # ASSUMPTION: NINA's default file pattern puts the target in a folder above the frame; take the nearest folder
    # that isn't a frame-type or a date folder
    for part in reversed(PurePosixPath(rel).parts[:-1]):
        low = part.lower()
        if frame_type(None, part) or re.fullmatch(r"[\d_\-]+", part) or low in ("lights", "light frames"):
            continue
        return part
    return None


def _when(header: dict[str, str], tz: ZoneInfo) -> dt.datetime | None:
    """Local wall-clock start of the exposure: DATE-LOC (NINA writes it), else DATE-OBS (UTC) in the site's zone."""
    local = first(header, "DATE-LOC")
    if local:
        try:
            return dt.datetime.fromisoformat(local.strip()[:26]).replace(tzinfo=None)
        except ValueError:
            pass
    obs = first(header, "DATE-OBS")
    if obs:
        try:
            utc = dt.datetime.fromisoformat(obs.strip()[:26])
        except ValueError:
            return None
        if utc.tzinfo is None:
            utc = utc.replace(tzinfo=dt.timezone.utc)
        return utc.astimezone(tz).replace(tzinfo=None)
    return None


def infer_name(header: dict[str, str] | None, rel: str, tz: ZoneInfo, mtime: float | None = None) -> FrameName | None:
    """A FrameName for a frame that isn't ASIAIR-named, or None if it can't be told what it is (no type, or no time)."""
    header = header or {}
    kind = frame_type(header, rel)
    if kind is None:
        return None
    stem = PurePosixPath(rel).stem
    exposure = num(first(header, "EXPTIME", "EXPOSURE"))
    if exposure is None:
        m = _EXP.search(stem)
        exposure = float(m.group(1)) if m else None
    if exposure is None:
        return None
    start = _when(header, tz)
    if start is None and mtime:
        # ASSUMPTION: no capture time in the header; the file's modification time is the end of the exposure
        start = dt.datetime.fromtimestamp(mtime, tz).replace(tzinfo=None) - dt.timedelta(seconds=exposure)
    if start is None:
        return None
    instrument = first(header, "INSTRUME") or ""
    cam = rules.camera_from(instrument)
    angle = num(first(header, "OBJCTROT", "ROTATANG", "ROTATOR", "POSANGLE"))
    seq = _SEQ.search(stem)
    return FrameName(
        type="Dark" if kind == "DarkFlat" else kind,
        object=_object(header, rel, kind),
        exposure_s=float(exposure),
        binning=int(num(first(header, "XBINNING")) or 1),
        camera=cam.token if cam else (instrument.strip() or "unknown"),
        filter=(first(header, "FILTER") or "").strip() or None,
        gain=int(num(first(header, "GAIN")) or 0),
        saved_local=start + dt.timedelta(seconds=float(exposure)),
        angle=round(angle) % 360 if angle is not None else None,
        temp_c=float(num(first(header, "CCD-TEMP", "SET-TEMP")) or 0.0),
        seq=int(seq.group(1)) if seq else 0,
    )

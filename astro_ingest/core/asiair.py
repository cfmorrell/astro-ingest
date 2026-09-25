"""What the ASIAIR writes: folder layout, filenames, thumbnails.

Filenames are
    <Type>_[<Object>_]<exp>(s|ms)_Bin<b>_<cam>_[<filter>_]gain<g>_<YYYYMMDD-HHMMSS>_[<angle>deg_]<temp>C_<seq>.fit
The timestamp is local wall-clock time at the END of the exposure (when the file was saved), so it trails
DATE-OBS (UTC, exposure start) by the exposure time plus a second or two of download. The angle is the
plate-solved rotation; a meridian flip adds 180°.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

# Top-level folders on the EMMC Images share
HANDLED_FOLDERS = ("Autorun", "Plan")
# Ignored unless Chris asks for them; never deleted (decision 8)
IGNORED_FOLDERS = ("Live", "Preview", "Video", "log", "GuidingDarkLibrary", "System Volume Information",
                   "batch_stack_tmp")
# Folders the ASIAIR expects to exist; cleanup must never remove them even when empty
STRUCTURAL_DIRS = frozenset({
    "Autorun", "Autorun/Light", "Autorun/Flat", "Autorun/Dark", "Autorun/Bias",
    "Plan", "Plan/Light",
})

FRAME_TYPES = ("Light", "Flat", "Dark", "Bias")
THUMB_SUFFIX = "_thn.jpg"
FIT_SUFFIX = ".fit"

_NAME = re.compile(
    r"^(?P<type>Light|Flat|Dark|Bias)_"
    r"(?:(?P<object>.+?)_)?"
    r"(?P<exp>\d+(?:\.\d+)?)(?P<unit>ms|s)_"
    r"Bin(?P<bin>\d+)_"
    r"(?P<cam>[0-9A-Za-z]+?)_"
    r"(?:(?P<filter>[A-Za-z0-9]+)_)?"
    r"gain(?P<gain>\d+)_"
    r"(?P<ts>\d{8}-\d{6})_"
    r"(?:(?P<angle>-?\d+)deg_)?"
    r"(?P<temp>[+-]?\d+(?:\.\d+)?)C_"
    r"(?P<seq>\d+)\.fit$"
)


@dataclass(frozen=True)
class FrameName:
    type: str                 # Light | Flat | Dark | Bias
    object: str | None        # as typed on the ASIAIR, e.g. "M 42", "NGC 7000", "ElephantTrunk"; None for calibration
    exposure_s: float
    binning: int
    camera: str               # filename camera token, e.g. "2600MC"
    filter: str | None
    gain: int
    saved_local: dt.datetime  # local wall-clock time the file was saved (end of exposure)
    angle: int | None         # plate-solved rotation in degrees
    temp_c: float
    seq: int


def parse_name(name: str) -> FrameName | None:
    """Parse an ASIAIR frame filename, or return None if it isn't one (masters, stacks, previews, junk)."""
    m = _NAME.match(name)
    if not m:
        return None
    exp = float(m["exp"]) / (1000 if m["unit"] == "ms" else 1)
    return FrameName(
        type=m["type"],
        object=m["object"],
        exposure_s=exp,
        binning=int(m["bin"]),
        camera=m["cam"],
        filter=m["filter"],
        gain=int(m["gain"]),
        saved_local=dt.datetime.strptime(m["ts"], "%Y%m%d-%H%M%S"),
        angle=int(m["angle"]) if m["angle"] is not None else None,
        temp_c=float(m["temp"]),
        seq=int(m["seq"]),
    )


def is_junk(name: str) -> bool:
    """macOS/Windows metadata and other dotfiles: never ingested, never counted."""
    return name.startswith(".") or name in ("desktop.ini", "Thumbs.db")


def thumb_for(fit_path: str) -> str:
    """The thumbnail the ASIAIR writes next to a frame: X.fit -> X_thn.jpg."""
    p = PurePosixPath(fit_path)
    return str(p.with_name(p.stem + THUMB_SUFFIX))


def fit_for_thumb(thumb_path: str) -> str | None:
    p = PurePosixPath(thumb_path)
    if not p.name.endswith(THUMB_SUFFIX):
        return None
    return str(p.with_name(p.name[: -len(THUMB_SUFFIX)] + FIT_SUFFIX))


def folder_category(rel_path: str) -> str:
    """'handled', 'ignored' or 'unknown', from the top-level folder of a path relative to the share root."""
    top = PurePosixPath(rel_path).parts[0] if rel_path else ""
    if top in HANDLED_FOLDERS:
        return "handled"
    if top in IGNORED_FOLDERS:
        return "ignored"
    return "unknown"

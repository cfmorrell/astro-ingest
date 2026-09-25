"""The Astronomy share's filing rules (docs/ORGANIZATION_GUIDE.md, docs/ClaudeHandoff.md §3–§6) as pure functions."""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- time

NIGHT_OFFSET = dt.timedelta(hours=12)


def night_of(local: dt.datetime) -> dt.date:
    """The night a local timestamp belongs to: the local evening the night began (local time minus 12 h)."""
    return (local - NIGHT_OFFSET).date()


def parse_date_obs(value: str | None) -> dt.datetime | None:
    """FITS DATE-OBS (UTC, no zone suffix) as an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(value.strip().rstrip("Z")[:26])
    except ValueError:
        return None
    return t.replace(tzinfo=dt.timezone.utc)


def utc_to_local(t: dt.datetime, tz: ZoneInfo) -> dt.datetime:
    """Aware UTC -> naive local wall-clock time. zoneinfo picks EDT or EST for that instant."""
    return t.astimezone(tz).replace(tzinfo=None)


# The ASIAIR filename time is when the file was saved: DATE-OBS (exposure start) + exposure + download time.
# Download/processing lag seen in the sample is 0–2 s for Autorun/Plan frames; allow generous slack but stay far
# below the 3600 s a wrong time zone or DST rule would produce.
SAVE_LAG_MIN = dt.timedelta(seconds=-5)
SAVE_LAG_MAX = dt.timedelta(seconds=180)


def clock_mismatch(saved_local: dt.datetime, date_obs: str | None, exposure_s: float,
                   tz: ZoneInfo) -> dt.timedelta | None:
    """How far the filename time is from what DATE-OBS predicts, or None if it agrees (or DATE-OBS is missing).

    A result near ±1 h means a time-zone/DST problem on the ASIAIR; the frame should wait for Chris.
    """
    start = parse_date_obs(date_obs)
    if start is None:
        return None
    lag = saved_local - (utc_to_local(start, tz) + dt.timedelta(seconds=exposure_s))
    if SAVE_LAG_MIN <= lag <= SAVE_LAG_MAX:
        return None
    return lag


# ---------------------------------------------------------------- cameras

@dataclass(frozen=True)
class Camera:
    token: str     # session-name token, e.g. "2600MC"
    library: str   # folder name in 001-MasterBias / 002-MasterDarks
    cooled: bool   # 10-frame calibration cap and libraries apply only to cooled ZWO cameras
    darkflats: bool  # keep dark flats (294MC/183MM); 2600s use the master bias instead


CAMERAS = (
    Camera("2600MC", "ASI2600MC Pro", True, False),   # headers say "ZWO ASI2600MC Duo"; no "Duo" in the folder
    Camera("2600MM", "ASI2600MM Pro", True, False),
    Camera("294MC", "ASI294MC Pro", True, True),
    Camera("183MM", "ASI183MM Pro (PANE)", True, True),
)


def camera_from(text: str | None) -> Camera | None:
    """Match an INSTRUME header or a filename camera token by substring ('ZWO ASI2600MC Duo' -> 2600MC)."""
    if not text:
        return None
    for cam in CAMERAS:
        if cam.token in text:
            return cam
    return None


# ---------------------------------------------------------------- telescopes

@dataclass(frozen=True)
class Scope:
    token: str | None   # session-name token; None for the Seestar (no token)
    name: str


# ASIAIR's TELESCOP holds the mount, so the scope is inferred from FOCALLEN (mm, inclusive ranges).
FOCAL_LENGTH_SCOPES = (
    (130, 145, Scope("FMA135", "Askar FMA135")),
    (245, 256, Scope(None, "Seestar S50 (integrated)")),
    (280, 292, Scope("Z61", "William Optics ZenithStar 61 with 0.8x reducer")),
    (355, 372, Scope("Z61", "William Optics ZenithStar 61 with Flat61A flattener")),
    (555, 575, Scope("SV503", "SVBony SV503 102ED with flattener")),
    (1360, 1395, Scope("RC6", "iOptron RC6")),
)


def scope_from_focal_length(focal_length_mm: float | None) -> Scope | None:
    """The telescope for a FOCALLEN value, or None (unknown: ask Chris)."""
    if focal_length_mm is None:
        return None
    for lo, hi, scope in FOCAL_LENGTH_SCOPES:
        if lo <= focal_length_mm <= hi:
            return scope
    return None


# ---------------------------------------------------------------- calibration libraries

STANDARD_TEMP_C = -10


def temp_suffix(mean_ccd_temp_c: float | None) -> str:
    """'' when the rounded mean CCD-TEMP is the standard -10 C, else ' (+14C)' / ' (-20C)'.

    Always from the frames' CCD-TEMP, never from a folder label.
    """
    if mean_ccd_temp_c is None:
        return ""
    rounded = round(mean_ccd_temp_c)
    return "" if rounded == STANDARD_TEMP_C else f" ({rounded:+d}C)"


def exposure_folder(exposure_s: float) -> str:
    """'300 Seconds' (capital S, integer exposure)."""
    return f"{round(exposure_s)} Seconds"


def bias_dir(camera: Camera, first_frame_local_date: dt.date, mean_ccd_temp_c: float | None) -> str:
    return f"001-MasterBias/{camera.library}/{first_frame_local_date.isoformat()}{temp_suffix(mean_ccd_temp_c)}"


def dark_dir(camera: Camera, exposure_s: float, first_frame_local_date: dt.date,
             mean_ccd_temp_c: float | None) -> str:
    return (f"002-MasterDarks/{camera.library}/{exposure_folder(exposure_s)}/"
            f"{first_frame_local_date.isoformat()}{temp_suffix(mean_ccd_temp_c)}")


CALIBRATION_CAP = 10  # frames per set (one exposure + filter) for cooled cameras; DSLR calibration is exempt


# ---------------------------------------------------------------- sites

@dataclass(frozen=True)
class Site:
    lat: float
    lon: float
    label: str


# Port of projinfo.py SITES. Nearest within SITE_RADIUS_KM wins; otherwise the site is unrecognized (ask Chris).
SITES = (
    Site(41.43, -74.036, "Cornwall, NY (home)"),
    Site(41.5, -74.0167, "Cornwall, NY area (coarse NINA coords - likely home)"),
    Site(41.3903, -73.9539, "West Point, NY"),
    Site(41.3819, -73.9752, "West Point, NY area"),
    Site(41.75, -73.9167, "GPS error - Chris confirms this was home (Cornwall, NY)"),
    Site(42.0896, -73.72, "MHAA Star Party (Lake Taghkanic State Park parking lot, Ancram NY)"),
    Site(44.3875, -68.0155, "Gouldsboro/Schoodic Peninsula area, Maine"),
)
SITE_RADIUS_KM = 4.0

_SEXAGESIMAL = re.compile(r"\s*([+-]?)(\d+)[ :d°]+(\d+)[ :']+([\d.]+)")


def parse_angle(value: str | float | None) -> float | None:
    """Decimal degrees from a number or a sexagesimal string ('41 25 48', '-74:02:10')."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    m = _SEXAGESIMAL.match(str(value))
    if not m:
        return None
    deg = int(m.group(2)) + int(m.group(3)) / 60 + float(m.group(4)) / 3600
    return -deg if m.group(1) == "-" else deg


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # Equirectangular approximation: plenty for "within 4 km" (same as projinfo.py).
    return 111 * math.hypot(lat1 - lat2, (lon1 - lon2) * math.cos(math.radians(lat2)))


def site_for(lat: str | float | None, lon: str | float | None) -> Site | None:
    """The named site within SITE_RADIUS_KM of SITELAT/SITELONG, or None if unrecognized or missing."""
    la, lo = parse_angle(lat), parse_angle(lon)
    if la is None or lo is None:
        return None
    best = min(SITES, key=lambda s: distance_km(s.lat, s.lon, la, lo))
    return best if distance_km(best.lat, best.lon, la, lo) <= SITE_RADIUS_KM else None


# ---------------------------------------------------------------- names

_SESSION = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<rest>.+)$")


def session_name(night: dt.date, target_name: str, camera_token: str, scope_token: str | None,
                 mosaic: bool = False) -> str:
    """YYYY-MM-DD-TargetName-[Mosaic]-Camera[-Telescope]. No Mono token; no filter set; no location."""
    parts = [night.isoformat(), target_name]
    if mosaic:
        parts.append("Mosaic")
    parts.append(camera_token)
    if scope_token:
        parts.append(scope_token)
    return "-".join(parts)


@dataclass(frozen=True)
class SessionName:
    night: dt.date
    target_name: str
    mosaic: bool
    camera_token: str | None
    scope_token: str | None


CAMERA_TOKENS = ("2600MM", "2600MC", "294MC", "183MM", "450D", "SeestarS50")
SCOPE_TOKENS = ("Z61", "WO61", "RC6", "SV503", "FMA135")


def parse_session_name(name: str) -> SessionName | None:
    """Split a session folder name into its parts; None if it doesn't start with a date."""
    m = _SESSION.match(name)
    if not m:
        return None
    try:
        night = dt.date.fromisoformat(m["date"])
    except ValueError:
        return None
    tokens = m["rest"].split("-")
    scope = tokens.pop() if len(tokens) > 1 and tokens[-1] in SCOPE_TOKENS else None
    camera = tokens.pop() if len(tokens) > 1 and tokens[-1] in CAMERA_TOKENS else None
    mosaic = len(tokens) > 1 and tokens[-1] == "Mosaic"
    if mosaic:
        tokens.pop()
    return SessionName(night, "-".join(tokens), mosaic, camera, "Z61" if scope == "WO61" else scope)


def is_target_folder(name: str) -> bool:
    """A top-level folder that holds a target: starts with a letter A–Y (skips 0*/1* index folders, Z*, dotfiles)."""
    return bool(name) and "A" <= name[0].upper() <= "Y" and name[0].isalpha()

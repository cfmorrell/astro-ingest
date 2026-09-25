"""Generate tiny FITS files for tests.

Real subs are never committed (and .gitignore blocks *.fit*). Instead, tests build ASIAIR-shaped trees of
2x2-pixel FITS files carrying just the header cards the app reads. The layout matches what the ASIAIR writes:
BITPIX 16 with BZERO 32768, an 80-character-card header padded to 2880 bytes, then big-endian data padded
to 2880 bytes.
"""

from __future__ import annotations

import struct
from pathlib import Path

BLOCK = 2880

# Header cards of a typical ASIAIR light frame (values from a real 2600MC Duo sub).
ASIAIR_LIGHT = {
    "IMAGETYP": "Light",
    "EXPTIME": 300.0,
    "INSTRUME": "ZWO ASI2600MC Duo",
    "TELESCOP": "ZWO AM5",
    "FOCALLEN": 369,
    "XPIXSZ": 3.76,
    "XBINNING": 1,
    "GAIN": 100,
    "OFFSET": 50,
    "CCD-TEMP": -10.0,
    "SET-TEMP": -10.0,
    "BAYERPAT": "RGGB",
    "CREATOR": "ZWO ASIAIR Plus",
    "GUIDECAM": "ZWO ASI220MM Mini",
    "SITELAT": 41.43,
    "SITELONG": -74.0358,
}


def _card(key: str, value) -> bytes:
    if isinstance(value, bool):
        text = f"{key:<8}= {'T' if value else 'F':>20}"
    elif isinstance(value, (int, float)):
        text = f"{key:<8}= {value!r:>20}"
    else:
        escaped = str(value).replace("'", "''")
        text = f"{key:<8}= '{escaped:<8}'"
    if len(text) > 80:
        raise ValueError(f"card too long: {text}")
    return text.ljust(80).encode("ascii")


def _pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * (-len(data) % BLOCK)


def write_fits(path: Path, header: dict | None = None, width: int = 2, height: int = 2) -> Path:
    """Write a minimal 16-bit FITS image with `header` cards after the mandatory ones."""
    cards = [
        _card("SIMPLE", True),
        _card("BITPIX", 16),
        _card("NAXIS", 2),
        _card("NAXIS1", width),
        _card("NAXIS2", height),
        _card("BZERO", 32768),
        _card("BSCALE", 1),
    ]
    cards += [_card(k, v) for k, v in (header or {}).items()]
    cards.append(b"END".ljust(80))
    pixels = struct.pack(f">{width * height}h", *([0] * (width * height)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_pad(b"".join(cards), b" ") + _pad(pixels, b"\0"))
    return path


INSTRUMENTS = {"2600MC": "ZWO ASI2600MC Duo", "2600MM": "ZWO ASI2600MM Pro", "294MC": "ZWO ASI294MC Pro",
               "183MM": "ZWO ASI183MM Pro"}


def asiair_frame(root: Path, folder: str, type_: str, saved_local: str, exposure_s: float = 300.0,
                 obj: str | None = None, angle: int | None = None, temp_c: float = -10.0, seq: int = 1,
                 tz: str = "America/New_York", thumb: bool = True, clock_error_s: float = 0,
                 camera: str = "2600MC", filter_: str | None = None, **cards) -> Path:
    """Write an ASIAIR-named frame (and its _thn.jpg) whose DATE-OBS matches the filename time.

    `saved_local` is the true local time at the END of the exposure ('20260923-211420'). `clock_error_s`
    shifts only the filename timestamp, simulating an ASIAIR whose clock or time zone is wrong.
    """
    import datetime as dt
    from zoneinfo import ZoneInfo

    saved = dt.datetime.strptime(saved_local, "%Y%m%d-%H%M%S")
    start_utc = (saved - dt.timedelta(seconds=exposure_s)).replace(tzinfo=ZoneInfo(tz)).astimezone(dt.timezone.utc)
    exp = f"{exposure_s * 1000:.1f}ms" if exposure_s < 1 else f"{exposure_s:.1f}s"  # as ASIAIR: 800.0ms, 1.0ms
    stamp = (saved + dt.timedelta(seconds=clock_error_s)).strftime("%Y%m%d-%H%M%S")
    name = "_".join(p for p in (
        type_, obj, exp, "Bin1", camera, filter_, "gain100", stamp,
        f"{angle}deg" if angle is not None else None, f"{temp_c:.1f}C", f"{seq:04d}") if p is not None) + ".fit"
    header = {**ASIAIR_LIGHT, "INSTRUME": INSTRUMENTS[camera], "IMAGETYP": type_, "EXPTIME": exposure_s,
              "CCD-TEMP": temp_c,
              "DATE-OBS": start_utc.replace(tzinfo=None).isoformat(timespec="microseconds"), **cards}
    if obj:
        header["OBJECT"] = obj
    if filter_:
        header["FILTER"] = filter_
    path = write_fits(root / folder / name, header)
    if thumb:
        path.with_name(path.stem + "_thn.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    return path

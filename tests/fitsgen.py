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

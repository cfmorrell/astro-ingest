"""Minimal FITS and XISF header reader (no astropy). Port of reference/scripts/fitshdr.py.

Reads only the header, so it is cheap over SMB: FITS headers are read in 2880-byte blocks until END, XISF headers
from the XML block at the start of the file. Values come back as strings, exactly as written (quotes stripped);
use `num()` for numbers. The first occurrence of a keyword wins.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import BinaryIO

BLOCK = 2880
MAX_HEADER_BLOCKS = 200  # 576 KB of header: far more than any real capture; stops runaway reads of non-FITS files
XISF_HEADER_BYTES = 200_000

_XISF_KEYWORD = re.compile(r'<FITSKeyword name="([^"]+)" value="([^"]*)"')
_QUOTED = re.compile(r"\s*'((?:[^']|'')*)'")


class HeaderError(Exception):
    pass


def read_fits_header(f: BinaryIO) -> dict[str, str]:
    h: dict[str, str] = {}
    for _ in range(MAX_HEADER_BLOCKS):
        block = f.read(BLOCK)
        if len(block) < BLOCK:
            raise HeaderError("truncated FITS header (no END card)")
        for i in range(0, BLOCK, 80):
            card = block[i:i + 80].decode("ascii", "replace")
            key = card[:8].strip()
            if key == "END":
                return h
            if card[8:10] != "= ":
                continue
            raw = card[10:]
            m = _QUOTED.match(raw)
            value = m.group(1).replace("''", "'").strip() if m else raw.split("/")[0].strip()
            h.setdefault(key, value)
    raise HeaderError("no END card in the first %d header blocks" % MAX_HEADER_BLOCKS)


def read_xisf_header(f: BinaryIO) -> dict[str, str]:
    text = f.read(XISF_HEADER_BYTES).decode("utf-8", "replace")
    h: dict[str, str] = {}
    for m in _XISF_KEYWORD.finditer(text):
        h.setdefault(m.group(1), m.group(2).strip().strip("'").strip())
    return h


def read_header(f: BinaryIO, name: str) -> dict[str, str]:
    """Read the header of an open file; `name` picks the format by extension."""
    if name.lower().endswith(".xisf"):
        return read_xisf_header(f)
    return read_fits_header(f)


def header(path: str | Path) -> dict[str, str]:
    with open(path, "rb") as f:
        return read_header(f, str(path))


def num(value: str | None) -> float | None:
    """Parse a header value as a float, or None if it is missing or not numeric."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def first(h: dict[str, str], *keys: str) -> str | None:
    """The first non-empty value among `keys` (headers differ between capture tools)."""
    for k in keys:
        v = h.get(k)
        if v not in (None, ""):
            return v
    return None

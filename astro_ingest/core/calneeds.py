"""Calibration gaps: which darks/bias a session's lights need that the library can't supply (or only poorly).

A port of reference/scripts/calneeds.py, working from the sessions' frames and the library directly instead of
re-parsing PROJECT_INFO text. Report only: ZZ_IMAGING_TODO.md stays Chris's to edit.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from astro_ingest.core import projinfo
from astro_ingest.core.fits import num
from astro_ingest.core.nas import CalibrationSet


@dataclass(frozen=True)
class Gap:
    kind: str          # Dark | Bias
    camera: str | None
    exposure: str      # "300" for darks, "-" for bias
    gain: str
    offset: str
    problem: str       # "missing" | "only offset X in library" | "temperature mismatch"
    session: str


def gaps(session_rel: str, frames: list[projinfo.Frame], library: list[CalibrationSet]) -> list[Gap]:
    L = [x for x in frames if x.kind == "Light" and not x.bad]
    if not L:
        return []
    inst = projinfo._f(L[0].h, "INSTRUME")
    from astro_ingest.core import rules
    cam_obj = rules.camera_from(inst) or rules.camera_from(session_rel)
    if cam_obj is None:
        return []   # no library for this camera (Seestar, DSLR)
    cam = cam_obj.library
    starts = [projinfo._dateobs(x.h) for x in L if projinfo._dateobs(x.h)]
    start = min(starts).date() if starts else dt.date.today()
    need = {(num(projinfo._f(x.h, "EXPTIME", "EXPOSURE")), projinfo._f(x.h, "GAIN"), projinfo._f(x.h, "OFFSET"))
            for x in L}
    out = []
    for e, g, o in sorted(need, key=lambda k: (k[0] or 0, str(k[1]), str(k[2]))):
        best = projinfo.pick(library, "Dark", cam, e, g, o, L, start)
        exp = projinfo._fmt(e)
        if not best:
            out.append(Gap("Dark", cam, exp, str(g), str(o), "missing", session_rel))
        elif o and str(best[0][0].offset) != str(o):
            out.append(Gap("Dark", cam, exp, str(g), str(o), f"only offset {best[0][0].offset} in library", session_rel))
        elif best[0][1]:
            out.append(Gap("Dark", cam, exp, str(g), str(o), "temperature mismatch", session_rel))
    for g, o in sorted({(k[1], k[2]) for k in need}, key=lambda k: (str(k[0]), str(k[1]))):
        if not projinfo.pick(library, "Bias", cam, None, g, o, L, start):
            out.append(Gap("Bias", cam, "-", str(g), str(o), "missing", session_rel))
    return out

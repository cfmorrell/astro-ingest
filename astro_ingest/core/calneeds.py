"""Calibration gaps: which darks/bias a session's lights need that the library can't supply (or only poorly).

A port of reference/scripts/calneeds.py, working from the sessions' frames and the library directly instead of
re-parsing PROJECT_INFO text. Report only: ZZ_IMAGING_TODO.md stays Chris's to edit.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from astro_ingest.core import projinfo, rules
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
    temp_c: int | None = None          # the lights' mean CCD-TEMP, rounded: what the calibration should be shot at
    have: str | None = None            # the closest set the library does have (offset or temperature off)
    have_offset: str | None = None
    have_temp_c: int | None = None
    why: str = ""                      # plain words for the page (Chris, 2026-10-08: "be more clear on what is needed")
    todo: str = ""
    file_to: str = ""


def _explain(kind, cam, exp, gain, offset, temp, problem, have) -> tuple[str, str, str]:
    """(why, what to shoot, where it's filed) for one gap."""
    what = f"{exp} s darks" if kind == "Dark" else "bias frames"
    at = f"gain {gain}, offset {offset}" + (f", cooled to {temp} °C" if temp is not None else "")
    if problem == "missing":
        why = f"The library has no {what} at gain {gain} for the {cam}."
    elif problem.startswith("only offset"):
        why = (f"The library's {what} at gain {gain} are all at offset {have.offset} ({have.rel}); "
               f"these lights were shot at offset {offset}.")
    else:
        t = round(have.first_temp_c) if have.first_temp_c is not None else "?"
        why = (f"The closest {what} at gain {gain} were shot at {t} °C ({have.rel}); these lights were at "
               f"{temp} °C, more than 5 °C apart.")
    n = rules.CALIBRATION_CAP
    todo = (f"Shoot {n} {what} ({at})" + (" with the scope capped" if kind == "Dark" else ", shortest exposure")
            + ", then ingest them like any other run.")
    suffix = rules.temp_suffix(temp)
    folder = rules.exposure_folder(float(exp)) if exp not in ("?", "-") else "<N> Seconds"
    file_to = (f"002-MasterDarks/{cam}/{folder}/<date you shoot them>{suffix}/"
               if kind == "Dark" else f"001-MasterBias/{cam}/<date you shoot them>{suffix}/")
    return why, todo, file_to


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
    temps = [num(projinfo._f(x.h, "CCD-TEMP")) for x in L if num(projinfo._f(x.h, "CCD-TEMP")) is not None]
    temp = round(sum(temps) / len(temps)) if temps else None
    out = []

    def gap(kind, exp, g, o, problem, have=None):
        why, todo, file_to = _explain(kind, cam, exp, g, o, temp, problem, have)
        out.append(Gap(kind, cam, exp, str(g), str(o), problem, session_rel, temp, have.rel if have else None,
                       str(have.offset) if have else None,
                       round(have.first_temp_c) if have and have.first_temp_c is not None else None, why, todo, file_to))

    for e, g, o in sorted(need, key=lambda k: (k[0] or 0, str(k[1]), str(k[2]))):
        best = projinfo.pick(library, "Dark", cam, e, g, o, L, start)
        exp = projinfo._fmt(e)
        if not best:
            gap("Dark", exp, g, o, "missing")
        elif o and str(best[0][0].offset) != str(o):
            gap("Dark", exp, g, o, f"only offset {best[0][0].offset} in library", best[0][0])
        elif best[0][1]:
            gap("Dark", exp, g, o, "temperature mismatch", best[0][0])
    for g, o in sorted({(k[1], k[2]) for k in need}, key=lambda k: (str(k[0]), str(k[1]))):
        if not projinfo.pick(library, "Bias", cam, None, g, o, L, start):
            gap("Bias", "-", g, o, "missing")
    return out

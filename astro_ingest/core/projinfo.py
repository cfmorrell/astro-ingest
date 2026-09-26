"""PROJECT_INFO.txt for a session: a port of reference/scripts/projinfo.py (main() and pick()).

The sections, wording and number formats are kept so every existing PROJECT_INFO on the share reads the same. Changes
from the reference:
- nights come from DATE-OBS converted with zoneinfo (local evening, minus 12 h) instead of a fixed UTC-16 h;
- output is deterministic (frames sorted, bias pairs sorted) where the reference depended on directory order;
- a session can be read from several roots at once (dev: the live share plus the sandbox overlay);
- the generator line says astro-ingest.
`scan_session()` reads headers from disk; `render()` is pure.
"""

from __future__ import annotations

import collections
import datetime as dt
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from astro_ingest.core import rules
from astro_ingest.core.fits import HeaderError, header, num
from astro_ingest.core.nas import CalibrationSet

SKIP = re.compile(r"^(_to_delete|process|masters|siril.*|wbpp.*|pixinsight.*|autointegrate|session \d+|dss.*|logs?|cache|"
                  r"drizztmp|calibrated|debayered|registered)$", re.I)
BAD = re.compile(r"^(trash|badframes)$", re.I)
FITS = re.compile(r"\.(fits?|xisf)$", re.I)
NOTES = ".project_notes.txt"

# The reference's telescope wording (kept verbatim for PROJECT_INFO continuity)
_SCOPE_NAMES = {"Z61": "William Optics ZenithStar 61 (Z61)", "WO61": "William Optics ZenithStar 61 (Z61)",
                "RC6": "6in Ritchey-Chretien (RC6)", "SV503": "SVBony SV503 80mm", "FMA135": "Askar FMA135"}
_FOCAL_SCOPES = ((130, 145, "Askar FMA135"), (245, 256, "Seestar S50 (integrated)"),
                 (280, 292, "Z61 with 0.8x reducer"), (355, 372, "Z61 with flattener"),
                 (555, 575, "SVBony SV503 80mm"), (1360, 1395, "RC6"))


@dataclass
class Frame:
    rel: str          # path inside the session, e.g. "lights/Light_….fit"
    dir: str          # the folder part of rel ("." for the session root)
    h: dict
    kind: str
    bad: bool         # inside TRASH/BadFrames


def _f(h: dict, *keys: str):
    for k in keys:
        if k in h and h[k] not in ("", None):
            return h[k]
    return None


def _kind(h: dict, name: str, parent: str) -> str:
    t = (h.get("IMAGETYP") or h.get("FRAME") or "").lower()
    p, n = parent.lower(), name.lower()
    if "darkflat" in p.replace(" ", "") or "flatdark" in t.replace(" ", "") or "dark flat" in t:
        return "DarkFlat"
    if "flat" in t:
        return "Flat"
    if "dark" in t:
        return "Dark"
    if "bias" in t or "offset" in t:
        return "Bias"
    if "light" in t:
        return "Light"
    for k in ("darkflat", "flat", "dark", "bias", "light"):
        if k in n or k in p:
            return {"darkflat": "DarkFlat"}.get(k, k.capitalize())
    return "Unknown"


def scan_session(roots: list[Path], session_rel: str) -> tuple[list[Frame], list[str], str | None]:
    """Frames of a session read from every root that has it (later roots add files the earlier ones lack), the
    processing-leftover folders in it, and its .project_notes.txt text (first root that has one)."""
    frames: dict[str, Frame] = {}
    other: set[str] = set()
    notes = None
    for root in roots:
        base = Path(root) / session_rel
        if not base.is_dir():
            continue
        if notes is None and (base / NOTES).is_file():
            notes = (base / NOTES).read_text()
        other.update(d for d in os.listdir(base) if (base / d).is_dir() and SKIP.match(d))
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not SKIP.match(d))
            rel_dir = os.path.relpath(dirpath, base)
            bad = any(BAD.match(p) for p in rel_dir.split(os.sep))
            for x in sorted(filenames):
                if not FITS.search(x) or x.startswith("."):
                    continue
                p = os.path.join(dirpath, x)
                rel = os.path.relpath(p, base)
                if os.path.islink(p) or rel in frames:
                    continue
                try:
                    h = header(p)
                except (OSError, HeaderError):
                    continue
                if (_f(h, "NAXIS") == "3" or "Stacked" in x
                        or x.lower().startswith(("master", "result", "starless", "starmask", "autosave"))
                        or re.search(r"_\d+x\d+sec_", x)):
                    continue
                frames[rel] = Frame(rel, rel_dir, h, _kind(h, x, os.path.basename(dirpath)), bad)
    return [frames[k] for k in sorted(frames)], sorted(other), notes


def _hms(deg, ra=True) -> str:
    d = num(deg)
    if d is None:
        return str(deg)
    if ra:
        h = d / 15
        H = int(h); m = (h - H) * 60; M = int(m); S = (m - M) * 60
        return f"{H:02d}h{M:02d}m{S:04.1f}s"
    s = "-" if d < 0 else "+"
    d = abs(d); D = int(d); m = (d - D) * 60; M = int(m); S = (m - M) * 60
    return f"{s}{D:02d}°{M:02d}'{S:04.1f}\""


def _dateobs(h: dict) -> dt.datetime | None:
    v = _f(h, "DATE-OBS", "DATE-LOC")
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(v[:19])
    except ValueError:
        return None


def _place(lat, lon) -> str | None:
    la, lo = rules.parse_angle(lat), rules.parse_angle(lon)
    if la is None or lo is None:
        return None
    best = min(rules.SITES, key=lambda s: (s.lat - la) ** 2 + ((s.lon - lo) * math.cos(math.radians(la))) ** 2)
    dkm = 111 * math.hypot(best.lat - la, (best.lon - lo) * math.cos(math.radians(la)))
    return (f"{best.label}  (~{best.lat:.3f}, {best.lon:.3f})" if dkm < rules.SITE_RADIUS_KM
            else f"unrecognized site ({la:.4f}, {lo:.4f})")


def _scope(fl, session_folder: str) -> str:
    toks = [t for t in re.split(r"[- ]", session_folder) if t.upper() in _SCOPE_NAMES]
    if toks:
        return _SCOPE_NAMES[toks[0].upper()] + " (from folder name)"
    fl = num(fl)
    if fl is None:
        return "unknown - confirm"
    for lo, hi, n in _FOCAL_SCOPES:
        if lo <= fl <= hi:
            return n + " (inferred from focal length - confirm)"
    return "unknown - confirm"


def _fmt(e) -> str:
    return "?" if e is None else f"{e:g}"


def _gb(frames, keyf):
    d = collections.OrderedDict()
    for fr in frames:
        d.setdefault(keyf(fr), []).append(fr)
    return d


def pick(library: list[CalibrationSet], kind: str, cam: str | None, exposure, gain, offset, lights: list[Frame],
         start: dt.date) -> list[tuple[CalibrationSet, bool]]:
    """The reference's ranking: same camera, gain (and exposure for darks); then same offset first, then within
    5 C of the lights' mean CCD-TEMP, then nearest date. Returns (set, temperature_mismatch) best first."""
    c = [m for m in library if m.kind == kind and m.camera and m.camera.library == cam
         and str(m.gain) == str(gain) and (exposure is None or m.exposure_s == exposure)]
    lt = [num(_f(x.h, "CCD-TEMP")) for x in lights if num(_f(x.h, "CCD-TEMP")) is not None]
    lt = sum(lt) / len(lt) if lt else None
    tbad = lambda m: lt is not None and m.first_temp_c is not None and abs(m.first_temp_c - lt) > 5  # noqa: E731
    when = lambda m: m.obs_date or m.date  # noqa: E731
    c.sort(key=lambda m: (str(m.offset) != str(offset) if offset else False, tbad(m),
                          abs((when(m) - start).days) if when(m) else 9999, m.rel))
    return [(m, tbad(m)) for m in c]


def render(session_rel: str, frames: list[Frame], other: list[str], notes: str | None,
           library: list[CalibrationSet], tz: ZoneInfo, today: dt.date) -> str | None:
    """The PROJECT_INFO text, or None if the session has no usable lights (the reference's 'SKIP no lights')."""
    target_folder, session_folder = session_rel.split("/", 1)
    L = [x for x in frames if x.kind == "Light" and not x.bad]
    if not L:
        return None
    h0 = L[0].h
    inst = _f(h0, "INSTRUME")
    cam_obj = rules.camera_from(inst) or rules.camera_from(session_rel)
    cam = cam_obj.library if cam_obj else None
    out: list[str] = []
    w = out.append
    w("PROJECT / SESSION INFORMATION"); w("=" * 60)
    w(f"Target folder : {target_folder}"); w(f"Session folder: {session_folder}")
    w(f"Generated     : {today.isoformat()} by astro-ingest from FITS headers + folder names")
    w("")
    w("TARGET"); w("-" * 60)
    objs = collections.Counter(_f(x.h, "OBJECT") for x in L)
    w(f"Object (header) : {', '.join(f'{k} ({v})' for k, v in objs.items() if k) or 'n/a'}")
    ra = _f(h0, "RA", "OBJCTRA"); de = _f(h0, "DEC", "OBJCTDEC")
    if _f(h0, "OBJCTRA") and num(ra) is None:
        ra, de = h0["OBJCTRA"], h0.get("OBJCTDEC")
    if ra:
        w(f"Pointing RA/Dec : {_hms(ra) if num(ra) is not None else ra}  "
          f"{_hms(de, False) if num(de) is not None else de}  (J2000, from mount)")
    if _f(h0, "CRVAL1"):
        w(f"Plate-solved ctr: {_hms(h0['CRVAL1'])}  {_hms(h0['CRVAL2'], False)}  (first light frame)")
    if _f(h0, "ROTATOR", "OBJCTROT", "POSANGLE"):
        w(f"Rotation/angle  : {_f(h0, 'ROTATOR', 'OBJCTROT', 'POSANGLE')}")
    w("")
    w("EQUIPMENT"); w("-" * 60)
    w(f"Camera          : {inst or 'n/a'}   (master-folder name: {cam})")
    if _f(h0, "BAYERPAT"):
        w(f"Bayer pattern   : {h0['BAYERPAT']}")
    px = num(_f(h0, "XPIXSZ")); fl = num(_f(h0, "FOCALLEN"))
    w("Sensor          : " + (f"{_f(h0, 'NAXIS1')} x {_f(h0, 'NAXIS2')} px, " if _f(h0, "NAXIS1") else "")
      + (f"pixel {px:.2f} µm, " if px else "") + f"bin {_f(h0, 'XBINNING')}")
    tel = _f(h0, "TELESCOP")
    w(f"TELESCOP header : {tel or 'n/a'}" + ("  (mount name on ASIAIR)" if tel and ("AM5" in tel or "iOptron" in tel) else ""))
    w(f"Telescope       : {_scope(_f(h0, 'FOCALLEN'), session_folder)}")
    if fl:
        w(f"Focal length    : {fl:g} mm" + (f"   Image scale: {206.265 * px / fl:.2f} \"/px" if px else ""))
    if _f(h0, "APTDIA"):
        w(f"Aperture        : {h0['APTDIA']} mm")
    if _f(h0, "GUIDECAM"):
        w(f"Guide camera    : {h0['GUIDECAM']}")
    if _f(h0, "FOCUSPOS", "FOCPOS"):
        w(f"Focuser pos     : {_f(h0, 'FOCUSPOS', 'FOCPOS')}")
    w(f"Capture tool    : {_f(h0, 'CREATOR', 'SWCREATE', 'PROGRAM') or 'n/a'}")
    sites = collections.Counter(_place(_f(x.h, "SITELAT"), _f(x.h, "SITELONG")) for x in L)
    for sname, cnt in sites.items():
        w(f"Capture site    : {sname or 'not recorded in headers'}" + (f"  ({cnt} frames)" if len(sites) > 1 else ""))
    if len(sites) > 1:
        w("  ** headers disagree on site - check **")
    w("")
    w("CAPTURE NIGHTS (night = local evening date)"); w("-" * 60)

    def night(x: Frame):
        d = _dateobs(x.h)
        if d is None:
            return None
        return rules.night_of(rules.utc_to_local(d.replace(tzinfo=dt.timezone.utc), tz))

    nights = _gb(L, night)
    for n, v in nights.items():
        ds = [_dateobs(x.h) for x in v if _dateobs(x.h)]
        w(f"  {n}: {len(v)} lights, {min(ds).isoformat()}Z -> {max(ds).isoformat()}Z" if ds else f"  {n}: {len(v)} lights")
    if len(nights) > 1:
        w("  ** MULTI-NIGHT SESSION **")
    w("")
    w("LIGHTS"); w("-" * 60)
    tot = 0.0
    for (flt, e, g, o, d), v in _gb(L, lambda x: (_f(x.h, "FILTER") or "-", num(_f(x.h, "EXPTIME", "EXPOSURE")),
                                                    _f(x.h, "GAIN"), _f(x.h, "OFFSET"), x.dir)).items():
        temps = [num(_f(x.h, "CCD-TEMP")) for x in v if num(_f(x.h, "CCD-TEMP")) is not None]
        t = f"{min(temps):.1f}..{max(temps):.1f}C" if temps else "?"
        tot += (e or 0) * len(v)
        w(f"  filter {flt:5} {len(v):4d} x {_fmt(e)}s  gain {g} offset {o} temp {t}  -> {d}/")
    w(f"  Total integration: {tot / 3600:.2f} h")
    bad = [x for x in frames if x.bad]
    if bad:
        w(f"  (+ {len(bad)} rejected frames in TRASH/BadFrames, not counted)")
    w("")
    w("FLATS / DARK FLATS (stay with this session)"); w("-" * 60)
    for k in ("Flat", "DarkFlat"):
        for (flt, e, d), v in _gb([x for x in frames if x.kind == k],
                                  lambda x: (_f(x.h, "FILTER") or "-", num(_f(x.h, "EXPTIME", "EXPOSURE")), x.dir)).items():
            w(f"  {k:8} filter {flt:5} {len(v):3d} x {_fmt(e)}s -> {d}/")
    w("")
    w("CALIBRATION - DARKS (paths relative to the Astronomy volume root)"); w("-" * 60)
    need = _gb(L, lambda x: (num(_f(x.h, "EXPTIME", "EXPOSURE")), _f(x.h, "GAIN"), _f(x.h, "OFFSET")))
    start = min(d for d in (_dateobs(x.h) for x in L) if d).date() if any(_dateobs(x.h) for x in L) else today
    for (e, g, o) in sorted(need, key=lambda k: (k[0] or 0, str(k[1]), str(k[2]))):
        c = pick(library, "Dark", cam, e, g, o, L, start)
        w(f"  For {_fmt(e)}s lights (gain {g}, offset {o}):")
        if not c:
            w("    !! NO MATCHING MASTER DARKS FOUND in 002-MasterDarks" + ("" if cam else " (camera has no master-dark library)"))
        for i, (m, tbad) in enumerate(c[:3]):
            temp = f"{m.first_temp_c:.1f}C" if m.first_temp_c is not None else "?"
            w(f"    {'USE ->' if i == 0 else '  alt '} {m.rel}/  ({m.count} frames, {m.obs_date or m.date}, gain {m.gain} "
              f"offset {m.offset} temp {temp})" + ("  !! TEMPERATURE MISMATCH vs lights" if tbad else ""))
    w("")
    w("CALIBRATION - BIAS"); w("-" * 60)
    for (g, o) in sorted({(k[1], k[2]) for k in need}, key=lambda k: (str(k[0]), str(k[1]))):
        c = pick(library, "Bias", cam, None, g, o, L, start)
        if not c:
            w(f"  gain {g} offset {o}: !! NO MATCHING MASTER BIAS in 001-MasterBias (use dark flats for flats)")
        for i, (m, _tbad) in enumerate(c[:3]):
            w(f"  {'USE ->' if i == 0 else '  alt '} {m.rel}/  ({m.count} frames, {m.obs_date or m.date}, gain {m.gain} "
              f"offset {m.offset})")
    w("")
    if other:
        w("OTHER FOLDERS: " + ", ".join(other)); w("")
    if notes is not None:
        w("NOTES"); w("-" * 60)
        out.extend(notes.rstrip().split("\n"))
        w("")
    return "\n".join(out) + "\n"

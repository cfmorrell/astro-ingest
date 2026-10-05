"""Frames that aren't ASIAIR-named (e.g. a NINA session): what they are, from the header, else the path."""

from zoneinfo import ZoneInfo

from fitsgen import nina_frame

from astro_ingest.core import inference
from astro_ingest.core import planner as P
from astro_ingest.core.nas import build_index
from astro_ingest.core.scan import scan
from astro_ingest.core.targets import load_targets
from astro_ingest.sources.local import LocalDirSource

TZ = ZoneInfo("America/New_York")


def test_infer_from_a_nina_header():
    h = {"IMAGETYP": "LIGHT", "OBJECT": "M 31", "EXPOSURE": "300", "FILTER": "L", "INSTRUME": "ZWO ASI2600MC Pro",
         "GAIN": "100", "CCD-TEMP": "-9.9", "DATE-LOC": "2026-10-04T21:30:15.123", "ROTATOR": "182.4"}
    n = inference.infer_name(h, "M 31/2026-10-04/LIGHT/x_0007.fits", TZ)
    assert (n.type, n.object, n.exposure_s, n.filter, n.camera, n.gain, n.angle, n.seq) == \
        ("Light", "M 31", 300.0, "L", "2600MC", 100, 182, 7)
    assert str(n.saved_local) == "2026-10-04 21:35:15.123000"          # end of the exposure, local time


def test_infer_type_from_the_path_when_the_header_says_nothing():
    assert inference.frame_type({}, "M 31/2026-10-04/FLAT/a.fits") == "Flat"
    assert inference.frame_type({}, "cal/DARKFLAT/a.fits") == "DarkFlat"
    assert inference.frame_type({}, "cal/Bias_0001.fits") == "Bias"
    assert inference.frame_type({"IMAGETYP": "Flat Field"}, "anything.fits") == "Flat"
    n = inference.infer_name({}, "NGC 7000/2026-10-04/LIGHT/2026-10-04_22-00-00_Ha_-10.00_180.00s_0003.fits", TZ,
                             mtime=1791216000)
    assert (n.type, n.object, n.exposure_s, n.seq) == ("Light", "NGC 7000", 180.0, 3)   # target from the folder


def test_a_nina_folder_plans_like_an_asiair(tmp_path):
    src, nas = tmp_path / "nina", tmp_path / "nas"
    for i in range(5):
        nina_frame(src, "SoulNebula", "light", f"2026-10-04T21:{10 + 5 * i:02d}:00", seq=i + 1)
    for i in range(3):
        nina_frame(src, "SoulNebula", "flat", f"2026-10-05T06:30:{10 + i:02d}", exposure_s=2.5, seq=i + 1)
    (src / "notes.txt").write_text("not a frame")
    (nas / "Z95-ClaudeReferences").mkdir(parents=True)
    (nas / "Z95-ClaudeReferences" / "targets.csv").write_text(
        "folder,name,messier,ngc,ic,other\nSoulNebula-IC1848,SoulNebula,,,1848,\n")
    source = LocalDirSource(src)
    source.layout = "folder"
    sc = scan(source, TZ)
    assert len(sc.frames) == 8 and [e.rel for e in sc.other_files] == ["notes.txt"]
    assert all(f.name is not None and not f.warnings for f in sc.frames), [f.warnings for f in sc.frames]
    plan = P.build_plan(sc, build_index(nas), load_targets(nas / "Z95-ClaudeReferences" / "targets.csv"))
    soul = next(s for s in plan.sessions if s.target_folder == "SoulNebula-IC1848")
    assert soul.folder.startswith("2026-10-04-SoulNebula-2600MC") and (soul.lights, soul.flats) == (5, 3)

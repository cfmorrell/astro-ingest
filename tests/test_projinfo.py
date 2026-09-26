"""PROJECT_INFO: the reference's calibration ranking and the rendered sheet."""

import datetime as dt
from zoneinfo import ZoneInfo

from fitsgen import asiair_frame

from astro_ingest.core import projinfo, rules
from astro_ingest.core.nas import CalibrationSet

CAM = rules.camera_from("ZWO ASI2600MC Duo")


def dark(rel, offset="50", temp=-10.0, date="2026-08-29"):
    d = dt.date.fromisoformat(date)
    return CalibrationSet("Dark", rel, 10, CAM, 300.0, "100", offset, temp, d, temp, d)


def light(temp=-10.0):
    return projinfo.Frame("lights/x.fit", "lights", {"CCD-TEMP": temp}, "Light", False)


def test_pick_prefers_offset_then_temperature_then_nearest_date():
    lib = [dark("a-near-wrong-offset", offset="30", date="2026-06-16"), dark("b-far", date="2025-04-14"),
           dark("c-near-warm", temp=5.0, date="2026-06-20"), dark("d-mid", date="2026-08-29")]
    ranked = projinfo.pick(lib, "Dark", CAM.library, 300.0, "100", "50", [light()], dt.date(2026, 6, 15))
    assert [m.rel for m, _ in ranked] == ["d-mid", "b-far", "c-near-warm", "a-near-wrong-offset"]
    assert [bad for _, bad in ranked] == [False, False, True, False]
    assert projinfo.pick(lib, "Dark", CAM.library, 120.0, "100", "50", [light()], dt.date(2026, 6, 15)) == []


def test_render_session(tmp_path):
    rel = "SoulNebula-IC1848/2026-09-23-SoulNebula-2600MC-Z61"
    for i in range(3):
        asiair_frame(tmp_path, f"{rel}/lights", "Light", f"20260923-22{i}000", obj="SoulNebula", angle=3, seq=i + 1,
                     thumb=False)
    asiair_frame(tmp_path, f"{rel}/flats", "Flat", "20260924-063300", exposure_s=6.1, angle=3, thumb=False)
    (tmp_path / rel / projinfo.NOTES).write_text("Night 1 of 2. Siblings: ../2026-09-25-SoulNebula-2600MC-Z61\n")
    frames, other, notes = projinfo.scan_session([tmp_path], rel)
    text = projinfo.render(rel, frames, other, notes, [], ZoneInfo("America/New_York"), dt.date(2026, 9, 26))
    assert "Session folder: 2026-09-23-SoulNebula-2600MC-Z61" in text
    assert "2026-09-23: 3 lights" in text and "Night 1 of 2" in text
    assert text == projinfo.render(rel, *projinfo.scan_session([tmp_path], rel), [], ZoneInfo("America/New_York"),
                                   dt.date(2026, 9, 26))   # deterministic
    assert projinfo.render(rel, [f for f in frames if f.kind != "Light"], other, notes, [],
                           ZoneInfo("America/New_York"), dt.date(2026, 9, 26)) is None

"""Calibration gaps say what's needed in plain words: why, what to shoot, where it's filed (Chris, 2026-10-08)."""

import datetime as dt

from astro_ingest.core import calneeds, rules
from astro_ingest.core.nas import CalibrationSet
from astro_ingest.core.projinfo import Frame

SESSION = "SoulNebula-IC1848/2026-10-04-SoulNebula-2600MM-RC6"
MM = rules.camera_from("ZWO ASI2600MM Pro")


def lights(temp=-10.0, exposure=300, gain=100, offset=50):
    return [Frame(f"lights-Ha/L{i}.fit", "lights-Ha", {"INSTRUME": "ZWO ASI2600MM Pro", "EXPTIME": exposure, "GAIN": gain,
                                                      "OFFSET": offset, "CCD-TEMP": temp,
                                                      "DATE-OBS": "2026-10-05T02:00:00"}, "Light", False)
            for i in range(3)]


def lib(kind, exposure, offset, temp, date="2025-04-14"):
    folder = (f"002-MasterDarks/ASI2600MM Pro/{exposure} Seconds/{date}" if kind == "Dark"
              else f"001-MasterBias/ASI2600MM Pro/{date}")
    return CalibrationSet(kind, folder, 10, MM, exposure, "100", str(offset), temp, dt.date.fromisoformat(date), temp)


def test_missing_darks_and_bias_say_what_to_shoot_and_where_it_goes():
    darks, bias = calneeds.gaps(SESSION, lights(), [])
    assert (darks.kind, darks.problem, darks.temp_c, bias.kind) == ("Dark", "missing", -10, "Bias")
    assert darks.why == "The library has no 300 s darks at gain 100 for the ASI2600MM Pro."
    assert darks.todo == ("Shoot 10 300 s darks (gain 100, offset 50, cooled to -10 °C) with the scope capped, "
                          "then ingest them like any other run.")
    assert darks.file_to == "002-MasterDarks/ASI2600MM Pro/300 Seconds/<date you shoot them>/"
    assert bias.file_to == "001-MasterBias/ASI2600MM Pro/<date you shoot them>/" and "shortest exposure" in bias.todo


def test_wrong_offset_names_the_set_the_library_has():
    (g,) = calneeds.gaps(SESSION, lights(), [lib("Dark", 300, 30, -10.0), lib("Bias", None, 50, -10.0)])
    assert g.problem == "only offset 30 in library" and g.have_offset == "30"
    assert g.why == ("The library's 300 s darks at gain 100 are all at offset 30 "
                     "(002-MasterDarks/ASI2600MM Pro/300 Seconds/2025-04-14); these lights were shot at offset 50.")


def test_wrong_temperature_gives_both_temperatures_and_the_folder_suffix():
    (g,) = calneeds.gaps(SESSION, lights(temp=-20.4), [lib("Dark", 300, 50, -10.0), lib("Bias", None, 50, -20.0)])
    assert (g.problem, g.temp_c, g.have_temp_c) == ("temperature mismatch", -20, -10)
    assert "were shot at -10 °C" in g.why and "these lights were at -20 °C" in g.why
    assert g.file_to.endswith("<date you shoot them> (-20C)/")   # not the standard -10 °C: the folder says so


def test_no_gaps_when_the_library_matches():
    assert calneeds.gaps(SESSION, lights(), [lib("Dark", 300, 50, -10.0), lib("Bias", None, 50, -10.0)]) == []

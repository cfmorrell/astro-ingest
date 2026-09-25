"""The generated fixtures must read back through the reference header reader the app will port."""

import sys
from pathlib import Path

from fitsgen import ASIAIR_LIGHT, BLOCK, write_fits

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reference" / "scripts"))
from fitshdr import header  # noqa: E402


def test_fixture_round_trips(tmp_path):
    path = write_fits(tmp_path / "Light_M 8_300.0s_Bin1_2600MC_gain100_20260828-221500_-10.0C_0001.fit",
                      {**ASIAIR_LIGHT, "OBJECT": "M 8", "DATE-OBS": "2026-08-29T02:15:00.000000"})
    assert path.stat().st_size % BLOCK == 0
    assert path.stat().st_size == 2 * BLOCK  # stays tiny
    h = header(str(path))
    assert h["OBJECT"] == "M 8"
    assert h["IMAGETYP"] == "Light"
    assert float(h["EXPTIME"]) == 300.0
    assert h["INSTRUME"] == "ZWO ASI2600MC Duo"
    assert h["DATE-OBS"] == "2026-08-29T02:15:00.000000"
    assert float(h["CCD-TEMP"]) == -10.0

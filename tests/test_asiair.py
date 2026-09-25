import datetime as dt

import pytest

from astro_ingest.core.asiair import fit_for_thumb, folder_category, is_junk, parse_name, thumb_for


def test_light_with_object_spaces_and_angle():
    n = parse_name("Light_M 42_300.0s_Bin1_2600MC_gain100_20260411-212434_79deg_-10.0C_0001.fit")
    assert (n.type, n.object, n.exposure_s, n.camera, n.filter, n.gain) == ("Light", "M 42", 300.0, "2600MC", None, 100)
    assert n.saved_local == dt.datetime(2026, 4, 11, 21, 24, 34)
    assert (n.angle, n.temp_c, n.seq, n.binning) == (79, -10.0, 1, 1)


def test_light_without_angle():
    n = parse_name("Light_IC 1805_300.0s_Bin1_2600MC_gain100_20251016-194720_-10.0C_0001.fit")
    assert n.object == "IC 1805" and n.angle is None


def test_calibration_frames_have_no_object():
    flat = parse_name("Flat_800.0ms_Bin1_2600MC_gain100_20260915-064702_185deg_-9.6C_0003.fit")
    assert (flat.type, flat.object, flat.exposure_s, flat.angle) == ("Flat", None, 0.8, 185)
    bias = parse_name("Bias_1.0ms_Bin1_2600MC_gain100_20260829-121555_91deg_-10.2C_0001.fit")
    assert (bias.type, bias.exposure_s) == ("Bias", 0.001)
    dark = parse_name("Dark_120.0s_Bin1_2600MC_gain100_20260428-064808_79deg_-9.9C_0001.fit")
    assert (dark.type, dark.exposure_s, dark.temp_c) == ("Dark", 120.0, -9.9)


def test_filter_and_uncooled_temp():
    n = parse_name("Light_NGC 7000_300.0s_Bin1_2600MM_Ha_gain100_20240902-221500_12deg_+14.0C_0012.fit")
    assert (n.camera, n.filter, n.temp_c, n.seq) == ("2600MM", "Ha", 14.0, 12)


def test_object_that_looks_like_an_exposure():
    n = parse_name("Light_Test_5s_300.0s_Bin1_2600MC_gain100_20260101-010101_-10.0C_0001.fit")
    assert n.object == "Test_5s" and n.exposure_s == 300.0


@pytest.mark.parametrize("name", [
    "MasterDark_Stack10_30.0s_Bin1_2600MC_gain100_20250223-200216_-10.0C.fit",
    "Stacked55_M 13_30.0s_Bin1_2600MC_gain100_20260613-220551_-10.0C.fit",
    "Preview_Mizar_20.0ms_Bin1_2600MC_gain100_20260613-235255_79deg_-10.0C.fit",
    "DarkLibrary_220MM_gain350_20250223-205615.fit",
    "Light_M 42_300.0s_Bin1_2600MC_gain100_20260411-212434_79deg_-10.0C_0001_thn.jpg",
])
def test_non_frames(name):
    assert parse_name(name) is None


def test_thumbnail_pairing():
    fit = "Plan/Light/M 13/Light_M 13_300.0s_Bin1_2600MC_gain100_20250611-212845_-10.0C_0001.fit"
    thumb = "Plan/Light/M 13/Light_M 13_300.0s_Bin1_2600MC_gain100_20250611-212845_-10.0C_0001_thn.jpg"
    assert thumb_for(fit) == thumb
    assert fit_for_thumb(thumb) == fit
    assert fit_for_thumb("Plan/x.jpg") is None


def test_folders_and_junk():
    assert folder_category("Autorun/Flat/x.fit") == "handled"
    assert folder_category("Plan/Light/M13/x.fit") == "handled"
    assert folder_category("Live/Light/M 13/x.fit") == "ignored"
    assert folder_category("GuidingDarkLibrary/x.fit") == "ignored"
    assert folder_category("Somewhere/x.fit") == "unknown"
    assert is_junk(".DS_Store") and is_junk("._x.fit") and is_junk("desktop.ini")
    assert not is_junk("Light_x.fit")

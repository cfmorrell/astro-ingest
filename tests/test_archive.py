import datetime as dt
import os

from fitsgen import ASIAIR_LIGHT, asiair_frame, write_fits

from astro_ingest.core.archive import build_index, calibration_library


def make_archive(root):
    s = "ElephantTrunkNebula-IC1396/2026-09-13-ElephantTrunkNebula-2600MC-Z61"
    asiair_frame(root, f"{s}/lights", "Light", "20260913-213151", obj="ElephantTrunk", angle=4, thumb=False)
    asiair_frame(root, f"{s}/lights", "Light", "20260914-031000", obj="ElephantTrunk", angle=184, seq=2, thumb=False)
    asiair_frame(root, f"{s}/flats", "Flat", "20260914-064000", exposure_s=15, angle=4, thumb=False)
    (root / s / "PROJECT_INFO.txt").write_text("x")
    # retired files are not "archived"
    asiair_frame(root, f"{s}/_to_delete", "Light", "20260913-220000", obj="ElephantTrunk", seq=9, thumb=False)
    # a session without flats
    asiair_frame(root, "SoulNebula-IC1848/2020-10-03-SoulNebula-294MC-Z61/lights", "Light", "20201003-230000",
                 obj="Soul", thumb=False)
    # libraries
    for i in range(3):
        write_fits(root / f"001-MasterBias/ASI2600MC Pro/2026-08-29/Bias_{i}.fit",
                   {**ASIAIR_LIGHT, "IMAGETYP": "Bias", "EXPTIME": 0.001, "CCD-TEMP": -10.2})
        write_fits(root / f"002-MasterDarks/ASI294MC Pro/300 Seconds/2023-04-19 (+14C)/Dark_{i}.fit",
                   {**ASIAIR_LIGHT, "INSTRUME": "ZWO ASI294MC Pro", "IMAGETYP": "Dark", "GAIN": 120, "OFFSET": 30,
                    "CCD-TEMP": 14.0 + i})
    # things the walk must skip
    (root / "100-ByMessierNumber").mkdir()
    os.symlink("../ElephantTrunkNebula-IC1396", root / "100-ByMessierNumber" / "IC1396-ElephantTrunk")
    os.symlink("ElephantTrunkNebula-IC1396", root / "AAA-LinkedTarget")
    write_fits(root / "Z95-ClaudeReferences/x.fit")
    write_fits(root / "000-FinalizedImages/x.fit")
    (root / ".DS_Store").write_bytes(b"junk")
    return s


def test_index_files_and_sessions(tmp_path):
    s = make_archive(tmp_path)
    index = build_index(tmp_path)

    assert index.target_folders == ["ElephantTrunkNebula-IC1396", "SoulNebula-IC1848"]
    names = set(index.files)
    assert "Light_ElephantTrunk_300.0s_Bin1_2600MC_gain100_20260913-213151_4deg_-10.0C_0001.fit" in names
    assert not any("0009" in n for n in names)                   # _to_delete skipped
    assert "x.fit" not in names                                  # Z*/0* skipped
    hits = index.find("Light_ElephantTrunk_300.0s_Bin1_2600MC_gain100_20260913-213151_4deg_-10.0C_0001.fit")
    assert len(hits) == 1 and hits[0].rel.startswith(f"{s}/lights/") and hits[0].size == 5760
    assert len([n for n in names if n.startswith("Bias_")]) == 3

    by_name = {x.folder: x for x in index.sessions}
    et = by_name["2026-09-13-ElephantTrunkNebula-2600MC-Z61"]
    assert et.has_flats and et.light_count == 2 and et.light_angles == {4, 184}
    assert et.parsed.camera_token == "2600MC" and et.parsed.scope_token == "Z61"
    soul = by_name["2020-10-03-SoulNebula-294MC-Z61"]
    assert not soul.has_flats and soul.light_angles == set()
    assert [x.folder for x in index.sessions_on(dt.date(2026, 9, 13))] == [et.folder]


def test_calibration_library(tmp_path):
    make_archive(tmp_path)
    sets = {s.rel: s for s in calibration_library(tmp_path)}
    bias = sets["001-MasterBias/ASI2600MC Pro/2026-08-29"]
    assert (bias.kind, bias.count, bias.camera.token, bias.gain, bias.offset) == ("Bias", 3, "2600MC", "100", "50")
    assert bias.date == dt.date(2026, 8, 29) and round(bias.temp_c, 1) == -10.2
    dark = sets["002-MasterDarks/ASI294MC Pro/300 Seconds/2023-04-19 (+14C)"]
    assert (dark.kind, dark.camera.library, dark.exposure_s, dark.gain) == ("Dark", "ASI294MC Pro", 300.0, "120")
    assert dark.temp_c == 15.0  # mean of first (14) and last (16) frame

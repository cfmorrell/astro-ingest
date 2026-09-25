import datetime as dt
from zoneinfo import ZoneInfo

from fitsgen import asiair_frame, write_fits

from astro_ingest.core.scan import scan
from astro_ingest.sources.local import LocalDirSource

NY = ZoneInfo("America/New_York")


def make_asiair(root):
    asiair_frame(root, "Plan/Light/SoulNebula", "Light", "20260923-211420", obj="SoulNebula", angle=3)
    asiair_frame(root, "Plan/Light/SoulNebula", "Light", "20260924-031500", obj="SoulNebula", angle=185, seq=2)
    asiair_frame(root, "Autorun/Flat", "Flat", "20260924-064702", exposure_s=6.1, angle=185)
    asiair_frame(root, "Autorun/Bias", "Bias", "20260829-121555", exposure_s=0.001, angle=91)
    # ASIAIR clock on the wrong zone: filename an hour behind DATE-OBS
    asiair_frame(root, "Autorun/Light/M 8", "Light", "20260828-221000", obj="M 8", angle=91, clock_error_s=-3600)
    # ignored folders, orphans, other files, junk
    asiair_frame(root, "Live/Light/M 13", "Light", "20260613-213513", exposure_s=30, obj="M 13")
    write_fits(root / "Live/Dark/MasterDark_Stack10_30.0s_Bin1_2600MC_gain100_20250223-200216_-10.0C.fit")
    (root / "Autorun/Dark").mkdir(parents=True)
    (root / "Autorun/Dark/Dark_300.0s_Bin1_2600MC_gain100_20260616-143306_132deg_-10.0C_0001_thn.jpg").write_bytes(b"j")
    (root / "Video").mkdir()
    (root / "Video/2026-04-11-232244-Jupiter-Bin1 -10.0C_thn.jpg").write_bytes(b"j")
    (root / "Plan/Light/SoulNebula/astropup-view-scan.json").write_text("{}")
    (root / "Plan/Light/SoulNebula/._astropup-view-scan.json").write_bytes(b"junk")
    (root / ".DS_Store").write_bytes(b"junk")
    write_fits(root / "Plan/Light/SoulNebula/Stacked_weird.fit")


def test_scan(tmp_path):
    make_asiair(tmp_path)
    result = scan(LocalDirSource(tmp_path), NY)
    assert len(result.handled()) == 6

    lights = sorted((f for f in result.handled() if f.kind == "Light" and "Soul" in f.rel), key=lambda f: f.rel)
    assert [f.night for f in lights] == [dt.date(2026, 9, 23)] * 2
    assert all(f.thumb is not None and f.size_with_thumb == f.entry.size + 4 for f in lights)
    assert all(f.warnings == [] for f in lights)
    assert lights[0].header["OBJECT"] == "SoulNebula"
    assert lights[0].start_local == dt.datetime(2026, 9, 23, 21, 9, 20)

    flat = next(f for f in result.handled() if f.kind == "Flat")
    assert flat.night == dt.date(2026, 9, 23) and flat.name.angle == 185  # dawn flats belong to the previous evening
    bias = next(f for f in result.handled() if f.kind == "Bias")
    assert bias.start_local.date() == dt.date(2026, 8, 29)                # library date: local calendar date

    m8 = next(f for f in result.handled() if "M 8" in f.rel)
    assert any("ASIAIR clock" in w for w in m8.warnings)
    weird = next(f for f in result.handled() if "Stacked_weird" in f.rel)
    assert weird.name is None and weird.warnings == ["filename is not an ASIAIR frame name"]

    ignored = [f for f in result.frames if f.category == "ignored"]
    assert {f.rel.split("/")[0] for f in ignored} == {"Live"} and all(f.header is None for f in ignored)

    assert [e.rel.split("/")[-2] for e in result.orphan_thumbs] == ["Dark"]
    others = {e.rel for e in result.other_files}
    assert "Plan/Light/SoulNebula/astropup-view-scan.json" in others
    assert "Video/2026-04-11-232244-Jupiter-Bin1 -10.0C_thn.jpg" in others
    assert not any("/." in e.rel or e.rel.startswith(".") for e in result.other_files + result.orphan_thumbs)


def test_scan_without_headers(tmp_path):
    make_asiair(tmp_path)
    result = scan(LocalDirSource(tmp_path), NY, read_headers=False)
    assert all(f.header is None for f in result.frames)
    assert len(result.handled()) == 6


def test_local_source_skips_symlinks_and_escapes(tmp_path):
    make_asiair(tmp_path / "src")
    (tmp_path / "outside.fit").write_bytes(b"x")
    (tmp_path / "src/Plan/link.fit").symlink_to(tmp_path / "outside.fit")
    (tmp_path / "src/Plan/linkdir").symlink_to(tmp_path)
    source = LocalDirSource(tmp_path / "src")
    rels = [e.rel for e in source.walk()]
    assert not any("link" in r for r in rels)
    try:
        source.open_read("../outside.fit")
    except ValueError:
        pass
    else:
        raise AssertionError("path escape not refused")

"""Index links (100-…103-) and ZZ_TARGET_INDEX.md: the port of make_index_links.py."""

import os

from astro_ingest.core import links

ROWS = [{"folder": "AndromedaGalaxy-M31", "name": "AndromedaGalaxy", "messier": "31", "ngc": "224", "ic": "", "other": ""},
        {"folder": "HeartNebula-IC1805", "name": "HeartNebula", "messier": "", "ngc": "", "ic": "1805", "other": "Sh2-190"},
        {"folder": "Missing-M1", "name": "Missing", "messier": "1", "ngc": "", "ic": "", "other": ""}]


def test_wanted_and_diff(tmp_path):
    (tmp_path / "AndromedaGalaxy-M31" / "2025-10-01-AndromedaGalaxy-2600MC-Z61").mkdir(parents=True)
    (tmp_path / "AndromedaGalaxy-M31" / "_to_delete").mkdir()
    (tmp_path / "HeartNebula-IC1805").mkdir()
    want = links.wanted(tmp_path, ROWS)
    assert want == {"100-ByMessierNumber/M031-AndromedaGalaxy": "../AndromedaGalaxy-M31",
                    "101-ByNGCNumber/NGC0224-AndromedaGalaxy": "../AndromedaGalaxy-M31",
                    "102-ByICNumber/IC1805-HeartNebula": "../HeartNebula-IC1805",
                    "103-ByDate/2025-10-01-AndromedaGalaxy-2600MC-Z61":
                        "../AndromedaGalaxy-M31/2025-10-01-AndromedaGalaxy-2600MC-Z61"}   # no link for a missing folder

    (tmp_path / "100-ByMessierNumber").mkdir()
    os.symlink("../AndromedaGalaxy-M31", tmp_path / "100-ByMessierNumber" / "M031-AndromedaGalaxy")   # correct
    os.symlink("../Gone", tmp_path / "100-ByMessierNumber" / "M099-Gone")                               # stale
    (tmp_path / "100-ByMessierNumber" / "README.txt").write_text("not a link")                          # left alone
    add, remove = links.diff(tmp_path, want)
    assert remove == ["100-ByMessierNumber/M099-Gone"]
    assert "100-ByMessierNumber/M031-AndromedaGalaxy" not in add and len(add) == 3


def test_index_markdown():
    md = links.index_markdown(ROWS)
    assert "| `AndromedaGalaxy-M31` | M31 | NGC 224 |  |  |" in md
    assert "| `HeartNebula-IC1805` |  |  | IC 1805 | Sh2-190 |" in md


def test_check_reports_broken_and_empty_links(tmp_path):
    from astro_ingest.core import links as L
    (tmp_path / "SoulNebula-IC1848" / "2026-09-23-SoulNebula-2600MC-Z61" / "lights").mkdir(parents=True)
    (tmp_path / "SoulNebula-IC1848" / "2026-09-23-SoulNebula-2600MC-Z61" / "lights" / "a.fit").write_bytes(b"x")
    (tmp_path / "HeartNebula-IC1805" / "2026-09-23-HeartNebula-2600MC-Z61").mkdir(parents=True)   # emptied by hand
    (tmp_path / "Old-450D" / "2020-08-27-Pleiades-450D").mkdir(parents=True)
    (tmp_path / "Old-450D" / "2020-08-27-Pleiades-450D" / "IMG_0001.CR2").write_bytes(b"raw")   # a DSLR session
    for d in ("102-ByICNumber", "103-ByDate"):
        (tmp_path / d).mkdir()
    (tmp_path / "102-ByICNumber" / "IC1848-SoulNebula").symlink_to("../SoulNebula-IC1848")
    (tmp_path / "103-ByDate" / "2026-09-23-HeartNebula-2600MC-Z61").symlink_to("../HeartNebula-IC1805/2026-09-23-HeartNebula-2600MC-Z61")
    (tmp_path / "103-ByDate" / "2020-08-27-Pleiades-450D").symlink_to("../Old-450D/2020-08-27-Pleiades-450D")
    (tmp_path / "103-ByDate" / "2025-01-01-Gone-2600MC-RC6").symlink_to("../Gone/2025-01-01-Gone-2600MC-RC6")
    r = L.check(tmp_path)
    assert r["checked"] == 4
    assert [b["link"] for b in r["broken"]] == ["103-ByDate/2025-01-01-Gone-2600MC-RC6"]
    assert [e["link"] for e in r["empty"]] == ["103-ByDate/2026-09-23-HeartNebula-2600MC-Z61"]

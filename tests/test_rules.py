import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from astro_ingest.core import rules

NY = ZoneInfo("America/New_York")
D = dt.datetime


# ---------------------------------------------------------------- night date

@pytest.mark.parametrize("local, night", [
    (D(2026, 9, 23, 21, 14), dt.date(2026, 9, 23)),   # evening
    (D(2026, 9, 24, 2, 30), dt.date(2026, 9, 23)),    # after midnight: same night
    (D(2026, 9, 24, 6, 45), dt.date(2026, 9, 23)),    # dawn flats: same night
    (D(2026, 9, 24, 11, 59), dt.date(2026, 9, 23)),
    (D(2026, 9, 24, 12, 0), dt.date(2026, 9, 24)),    # noon starts the next night
])
def test_night_of(local, night):
    assert rules.night_of(local) == night


def _date_obs(local_start: dt.datetime, tz=NY) -> str:
    return local_start.replace(tzinfo=tz).astimezone(dt.timezone.utc).replace(tzinfo=None).isoformat()


@pytest.mark.parametrize("start", [
    D(2026, 7, 15, 22, 0),          # summer (EDT, -4)
    D(2026, 1, 15, 22, 0),          # winter (EST, -5)
    D(2026, 3, 8, 3, 30),           # spring forward night, after the change (EDT)
    D(2026, 3, 8, 1, 30),           # spring forward night, before the change (EST)
    D(2026, 10, 31, 23, 0),         # fall back night, before the change (EDT)
    D(2026, 11, 1, 3, 30),          # fall back night, after the change (EST)
])
def test_clock_agrees_across_dst(start):
    saved = start + dt.timedelta(seconds=301)  # 300 s exposure + 1 s download
    assert rules.clock_mismatch(saved, _date_obs(start), 300, NY) is None


def test_clock_wrong_time_zone_detected():
    start = D(2026, 7, 15, 22, 0)
    saved_by_asiair_on_est = start - dt.timedelta(hours=1) + dt.timedelta(seconds=301)
    lag = rules.clock_mismatch(saved_by_asiair_on_est, _date_obs(start), 300, NY)
    assert lag is not None and round(lag.total_seconds()) == -3600 + 1


def test_clock_missing_date_obs():
    assert rules.clock_mismatch(D(2026, 7, 15, 22, 5), None, 300, NY) is None
    assert rules.clock_mismatch(D(2026, 7, 15, 22, 5), "garbage", 300, NY) is None


def test_utc_to_local_uses_zone_rules():
    assert rules.utc_to_local(rules.parse_date_obs("2026-09-24T01:17:32.792625"), NY) == D(2026, 9, 23, 21, 17, 32, 792625)
    assert rules.utc_to_local(rules.parse_date_obs("2026-01-15T03:00:00"), NY) == D(2026, 1, 14, 22, 0)


# ---------------------------------------------------------------- equipment

def test_camera_from_headers_and_tokens():
    assert rules.camera_from("ZWO ASI2600MC Duo").library == "ASI2600MC Pro"
    assert rules.camera_from("ZWO ASI2600MM Pro").token == "2600MM"
    assert rules.camera_from("2600MC").token == "2600MC"
    assert rules.camera_from("ZWO ASI183MM Pro").library == "ASI183MM Pro (PANE)"
    assert rules.camera_from("ZWO ASI294MC Pro").darkflats is True
    assert rules.camera_from("ZWO ASI2600MC Duo").darkflats is False
    assert rules.camera_from("Canon EOS 450D") is None
    assert rules.camera_from(None) is None


@pytest.mark.parametrize("fl, token", [
    (135, "FMA135"), (130, "FMA135"), (145, "FMA135"),
    (250, None),                     # Seestar: known scope, no token
    (287, "Z61"), (360, "Z61"), (369, "Z61"),
    (568, "SV503"), (1382, "RC6"), (1370, "RC6"),
])
def test_scope_from_focal_length(fl, token):
    scope = rules.scope_from_focal_length(fl)
    assert scope is not None and scope.token == token


@pytest.mark.parametrize("fl", [100, 200, 300, 450, 750, None])
def test_unknown_focal_length(fl):
    assert rules.scope_from_focal_length(fl) is None


# ---------------------------------------------------------------- calibration library

@pytest.mark.parametrize("temp, suffix", [
    (-10.0, ""), (-9.6, ""), (-11.5, ""), (None, ""),
    (14.2, " (+14C)"), (11.0, " (+11C)"), (-20.1, " (-20C)"), (0.4, " (+0C)"),
])
def test_temp_suffix(temp, suffix):
    assert rules.temp_suffix(temp) == suffix


def test_library_dirs():
    cam = rules.camera_from("ZWO ASI2600MC Duo")
    assert rules.bias_dir(cam, dt.date(2026, 8, 29), -10.2) == "001-MasterBias/ASI2600MC Pro/2026-08-29"
    assert rules.dark_dir(cam, 120.0, dt.date(2026, 4, 28), -9.9) == "002-MasterDarks/ASI2600MC Pro/120 Seconds/2026-04-28"
    cam294 = rules.camera_from("ZWO ASI294MC Pro")
    assert rules.dark_dir(cam294, 300, dt.date(2023, 4, 19), 14.1) == \
        "002-MasterDarks/ASI294MC Pro/300 Seconds/2023-04-19 (+14C)"


# ---------------------------------------------------------------- sites

@pytest.mark.parametrize("lat, lon, label", [
    ("41.43", "-74.0358", "Cornwall, NY (home)"),
    ("41 25 48", "-74 02 10", "Cornwall, NY (home)"),                 # sexagesimal
    (41.39, -73.954, "West Point, NY"),
    (42.09, -73.72, "MHAA Star Party (Lake Taghkanic State Park parking lot, Ancram NY)"),
    (44.39, -68.02, "Gouldsboro/Schoodic Peninsula area, Maine"),
])
def test_site_for(lat, lon, label):
    assert rules.site_for(lat, lon).label == label


def test_unrecognized_site():
    assert rules.site_for(40.0, -75.0) is None
    assert rules.site_for(None, "-74") is None


# ---------------------------------------------------------------- names

def test_session_name():
    assert rules.session_name(dt.date(2026, 9, 23), "SoulNebula", "2600MC", "Z61") == "2026-09-23-SoulNebula-2600MC-Z61"
    assert rules.session_name(dt.date(2023, 11, 2), "Pleiades", "183MM", "SV503", mosaic=True) == \
        "2023-11-02-Pleiades-Mosaic-183MM-SV503"
    assert rules.session_name(dt.date(2024, 7, 19), "SplinterGalaxy", "SeestarS50", None) == \
        "2024-07-19-SplinterGalaxy-SeestarS50"


@pytest.mark.parametrize("name, parsed", [
    ("2026-09-13-ElephantTrunkNebula-2600MC-Z61", ("2026-09-13", "ElephantTrunkNebula", False, "2600MC", "Z61")),
    ("2023-11-02-Pleiades-Mosaic-183MM-SV503", ("2023-11-02", "Pleiades", True, "183MM", "SV503")),
    ("2024-07-19-SplinterGalaxy-SeestarS50", ("2024-07-19", "SplinterGalaxy", False, "SeestarS50", None)),
    ("2020-07-31-AndromedaGalaxy-450D", ("2020-07-31", "AndromedaGalaxy", False, "450D", None)),
    ("2021-09-24-ElephantTrunkNebula-294MC-WO61", ("2021-09-24", "ElephantTrunkNebula", False, "294MC", "Z61")),
])
def test_parse_session_name(name, parsed):
    s = rules.parse_session_name(name)
    assert (s.night.isoformat(), s.target_name, s.mosaic, s.camera_token, s.scope_token) == parsed


def test_parse_session_name_rejects_non_sessions():
    assert rules.parse_session_name("stacked") is None
    assert rules.parse_session_name("2026-13-45-Bad-2600MC") is None


@pytest.mark.parametrize("name, ok", [
    ("AndromedaGalaxy-M31", True), ("MarkariansChain", True), ("YellowThing", True),
    ("000-FinalizedImages", False), ("100-ByMessierNumber", False), ("Z95-ClaudeReferences", False),
    ("ZZ_EQUIPMENT.md", False), (".DS_Store", False), ("_asiair-sample", False), ("", False),
])
def test_is_target_folder(name, ok):
    assert rules.is_target_folder(name) is ok

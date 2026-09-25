"""Planner behaviour on small fixture trees: an ASIAIR share and a NAS, both built from tiny generated FITS."""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from fitsgen import asiair_frame

from astro_ingest.core import planner as P
from astro_ingest.core.nas import build_index
from astro_ingest.core.scan import scan
from astro_ingest.core.targets import load_targets
from astro_ingest.sources.local import LocalDirSource

NY = ZoneInfo("America/New_York")
TARGETS_CSV = """folder,name,messier,ngc,ic,other
SoulNebula-IC1848,SoulNebula,,,1848,
SadrRegion-IC1318,SadrRegion,,,1318,
CrescentNebula-NGC6888,CrescentNebula,,6888,,Sh2-105
HeartNebula-IC1805,HeartNebula,,,1805,
HeartAndSoulNebulae-IC1805-IC1848,HeartAndSoulNebulae,,,1805;1848,
LagoonNebula-M8,LagoonNebula,8,6523,,
OmegaNebula-M17,OmegaNebula,17,6618,,
HerculesCluster-M13,HerculesCluster,13,6205,,
"""
SOUL = "SoulNebula-IC1848/2026-09-23-SoulNebula-2600MC-Z61"


class World:
    """An ASIAIR tree and a NAS tree under tmp_path, planned on demand."""

    def __init__(self, tmp_path):
        self.air = tmp_path / "asiair"
        self.nas = tmp_path / "nas"
        self.air.mkdir()
        self.nas.mkdir()
        self.targets = tmp_path / "targets.csv"
        self.targets.write_text(TARGETS_CSV)

    def light(self, saved, obj="SoulNebula", folder=None, **kw):
        return asiair_frame(self.air, folder or f"Plan/Light/{obj}", "Light", saved, obj=obj, **kw)

    def flat(self, saved, **kw):
        kw.setdefault("exposure_s", 6.1)
        return asiair_frame(self.air, "Autorun/Flat", "Flat", saved, **kw)

    def plan(self, answers=None):
        return P.build_plan(scan(LocalDirSource(self.air), NY), build_index(self.nas), load_targets(self.targets),
                            answers)


def by_name(plan, fragment):
    return [i for i in plan.items if fragment in i.src and not i.src.endswith("_thn.jpg")]


def decision(plan, kind):
    ds = [d for d in plan.decisions if d.kind == kind]
    assert len(ds) == 1, ds
    return ds[0]


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


# ---------------------------------------------------------------- lights and flats

def test_new_session_with_matching_flats(w):
    for i, t in enumerate(["20260923-211420", "20260923-231500", "20260924-031500", "20260924-041500"]):
        w.light(t, angle=3 if i < 2 else 185, seq=i + 1)          # meridian flip: 3° -> 185°
    for i in range(3):
        w.flat(f"20260924-06330{i}", angle=185, seq=i + 1)         # dawn flats, same night
    plan = w.plan()

    lights = by_name(plan, "Light_SoulNebula")
    assert {i.action for i in lights} == {P.COPY}
    assert all(i.dsts == [f"{SOUL}/lights/{i.src.rsplit('/', 1)[1]}"] for i in lights)
    assert all(i.thumb and i.cleanup == "after-verify" for i in lights)
    flats = by_name(plan, "Flat_")
    assert all(i.action == P.COPY and i.dsts[0].startswith(f"{SOUL}/flats/") for i in flats)
    s = plan.sessions[0]
    assert (s.rel, s.exists, s.lights, s.flats, s.warnings) == (SOUL, False, 4, 3, [])
    assert plan.decisions == []


def test_ingest_evidence_beats_catalog_match_and_missing_frames_append(w):
    sadr = "SadrRegion-IC1318/2025-10-16-SadrRegion-2600MC-FMA135"
    on_nas = asiair_frame(w.nas, f"{sadr}/lights", "Light", "20251016-204000", obj="NGC 6888", FOCALLEN=137)
    w.light("20251016-204000", obj="NGC 6888", folder="Autorun/Light/NGC 6888", FOCALLEN=137)
    w.light("20251016-214500", obj="NGC 6888", folder="Autorun/Light/NGC 6888", FOCALLEN=137, seq=2)
    plan = w.plan()
    first, second = by_name(plan, "Light_NGC 6888")
    assert first.action == P.ALREADY and first.ingested_at == [f"{sadr}/lights/{on_nas.name}"]
    assert second.action == P.APPEND and second.dsts[0].startswith(f"{sadr}/lights/")   # not CrescentNebula
    d = decision(plan, "append")
    assert d.default == "append" and d.items == [second.src]
    skipped = w.plan({d.id: "skip"})
    assert by_name(skipped, "Light_NGC 6888")[1].action == P.SKIP
    assert by_name(skipped, "Light_NGC 6888")[1].cleanup == "never"


def test_ambiguous_target_asks_and_flats_wait(w):
    w.light("20251016-194720", obj="IC 1805", folder="Autorun/Light/IC 1805", FOCALLEN=137)
    w.light("20251016-195220", obj="IC 1805", folder="Autorun/Light/IC 1805", FOCALLEN=137, seq=2)
    w.light("20251016-195720", obj="IC 1805", folder="Autorun/Light/IC 1805", FOCALLEN=137, seq=3)
    w.light("20251016-200220", obj="IC 1805", folder="Autorun/Light/IC 1805", FOCALLEN=137, seq=4)
    w.flat("20251017-063000", FOCALLEN=137)
    plan = w.plan()
    d = decision(plan, "target")
    assert [o["value"] for o in d.options][:2] == ["HeartNebula-IC1805", "HeartAndSoulNebulae-IC1805-IC1848"]
    assert d.resolved is None
    assert {i.action for i in plan.items if i.src.endswith(".fit")} == {P.PENDING}
    flat = by_name(plan, "Flat_")[0]
    assert flat.decision == d.id and flat.src in d.items                        # flats follow their lights

    answered = w.plan({d.id: "HeartAndSoulNebulae-IC1805-IC1848"})
    s = answered.sessions[0]
    assert s.rel == "HeartAndSoulNebulae-IC1805-IC1848/2025-10-16-HeartAndSoulNebulae-2600MC-FMA135"
    assert (s.lights, s.flats) == (4, 1)


def test_new_target_proposal(w):
    for i in range(4):
        w.light(f"20260427-21{i}000", obj="NGC 4565", FOCALLEN=1384, seq=i + 1)
    plan = w.plan()
    d = decision(plan, "target")
    assert "new:NGC4565" in [o["value"] for o in d.options]
    s = w.plan({d.id: "new:NeedleGalaxy-NGC4565"}).sessions[0]
    assert (s.rel, s.new_target, s.lights) == ("NeedleGalaxy-NGC4565/2026-04-27-NeedleGalaxy-2600MC-RC6", True, 4)


def test_tiny_group_is_asked(w):
    w.light("20250611-212845", obj="M13", FOCALLEN=570)
    plan = w.plan()
    d = decision(plan, "tiny-group")
    assert by_name(plan, "Light_M13")[0].action == P.PENDING
    assert by_name(w.plan({d.id: "file"}), "Light_M13")[0].dsts[0].startswith(
        "HerculesCluster-M13/2025-06-11-HerculesCluster-2600MC-SV503/lights/")
    assert by_name(w.plan({d.id: "test"}), "Light_M13")[0].action == P.NOT_KEPT


def test_unknown_scope_is_asked(w):
    for i in range(4):
        w.light(f"20260923-21{i}000", FOCALLEN=750, seq=i + 1)
    plan = w.plan()
    assert decision(plan, "scope").resolved is None


def test_clock_mismatch_waits(w):
    for i in range(4):
        w.light(f"20260923-21{i}000", seq=i + 1, clock_error_s=-3600 if i == 0 else 0)
    plan = w.plan()
    d = decision(plan, "clock")
    assert len(d.items) == 1 and by_name(plan, "Light_")[0].action == P.PENDING
    used = w.plan({d.id: "use"})
    assert all(i.action == P.COPY for i in by_name(used, "Light_"))


def test_site_disagreement_warns(w):
    for i in range(4):
        w.light(f"20260923-21{i}000", seq=i + 1, **({"SITELAT": 44.3875, "SITELONG": -68.0155} if i == 0 else {}))
    assert "frames disagree on site" in w.plan().sessions[0].warnings[0]


def test_flats_shared_by_two_sessions_same_night(w):
    for i in range(4):
        w.light(f"20260828-21{i}000", obj="M 8", angle=91, seq=i + 1)
        w.light(f"20260828-23{i}000", obj="M 17", angle=91, seq=i + 1)
    w.flat("20260828-222700", exposure_s=15, angle=91)
    flat = by_name(w.plan(), "Flat_")[0]
    assert sorted(d.split("/")[0] for d in flat.dsts) == ["LagoonNebula-M8", "OmegaNebula-M17"]


def test_flats_already_in_one_session_still_copied_to_the_other(w):
    lagoon = "LagoonNebula-M8/2026-08-28-LagoonNebula-2600MC-Z61"
    f = w.flat("20260828-222700", exposure_s=15, angle=91)
    asiair_frame(w.nas, f"{lagoon}/flats", "Flat", "20260828-222700", exposure_s=15, angle=91, thumb=False)
    asiair_frame(w.nas, f"{lagoon}/lights", "Light", "20260828-211000", obj="M 8", angle=91, thumb=False)
    for i in range(4):
        w.light(f"20260828-23{i}000", obj="M 17", angle=91, seq=i + 1)
    flat = by_name(w.plan(), f.name)[0]
    assert flat.action == P.COPY and [d.split("/")[0] for d in flat.dsts] == ["OmegaNebula-M17"]


def test_flats_without_lights_are_blocked_until_released(w):
    w.flat("20260516-153800", exposure_s=4, angle=79, FOCALLEN=1384)
    plan = w.plan()
    d = decision(plan, "release")
    flat = by_name(plan, "Flat_")[0]
    assert (d.default, flat.action, flat.cleanup) == ("keep", P.NO_LIGHTS, "blocked")
    released = by_name(w.plan({d.id: "release"}), "Flat_")[0]
    assert (released.action, released.cleanup) == (P.NOT_KEPT, "callout")


def test_79_degree_flats_match_with_a_warning(w):
    for i in range(4):
        w.light(f"20260615-22{i}000", obj="NGC 5907", angle=133, seq=i + 1, FOCALLEN=1386,
                folder="Plan/Light/NGC 5907")
    w.targets.write_text(TARGETS_CSV + "SplinterGalaxy-NGC5907,SplinterGalaxy,,5907,,\n")
    w.flat("20260615-204300", exposure_s=5.9, angle=79, FOCALLEN=1386)   # shot before the lights
    plan = w.plan()
    assert by_name(plan, "Flat_")[0].action == P.COPY
    assert any("79°" in x for x in plan.sessions[0].warnings)


def test_flat_cap_and_dark_flat_policy(w):
    for i in range(4):
        w.light(f"20260923-21{i}000", angle=3, seq=i + 1)
    for i in range(12):
        w.flat(f"20260924-0633{i:02d}", angle=185, seq=i + 1)
    for i in range(2):   # "Dark" frames at the flat exposure are dark flats; not kept for the 2600MC
        asiair_frame(w.air, "Autorun/Dark", "Dark", f"20260924-0640{i:02d}", exposure_s=6.1, angle=185, seq=i + 1)
    plan = w.plan()
    flats = sorted(by_name(plan, "Flat_"), key=lambda i: i.src)
    assert [i.action for i in flats].count(P.COPY) == 10
    assert [i.action for i in flats[10:]] == [P.OVER_CAP, P.OVER_CAP] and flats[10].cleanup == "callout"
    darkflats = by_name(plan, "Dark_")
    assert {i.action for i in darkflats} == {P.NOT_KEPT}


def test_dark_flats_kept_for_294mc(w):
    for i in range(4):
        w.light(f"20260923-21{i}000", angle=3, seq=i + 1, camera="294MC")
    w.flat("20260924-063300", angle=185, camera="294MC")
    asiair_frame(w.air, "Autorun/Dark", "Dark", "20260924-064000", exposure_s=6.1, angle=185, camera="294MC")
    plan = w.plan()
    df = by_name(plan, "Dark_")[0]
    assert df.action == P.COPY and df.dsts[0].startswith("SoulNebula-IC1848/2026-09-23-SoulNebula-294MC-Z61/darkflats/")


# ---------------------------------------------------------------- library

def test_library_batches(w):
    def stamp(start, minutes):
        return (dt.datetime.strptime(start, "%Y%m%d-%H%M%S") + dt.timedelta(minutes=minutes)).strftime("%Y%m%d-%H%M%S")

    for i in range(12):
        asiair_frame(w.air, "Autorun/Dark", "Dark", stamp("20260428-064608", 2 * i), exposure_s=120, seq=i + 1)
    for i in range(3):   # a second batch weeks later: its own folder
        asiair_frame(w.air, "Autorun/Dark", "Dark", stamp("20260616-143306", 5 * i), exposure_s=120, seq=i + 1)
    for i in range(2):   # uncooled bias
        asiair_frame(w.air, "Autorun/Bias", "Bias", f"20230419-12000{i}", exposure_s=0.001, temp_c=14.2, seq=i + 1)
    plan = w.plan()
    darks = sorted(by_name(plan, "Dark_120.0s"), key=lambda i: i.src)
    april = [i for i in darks if "20260428" in i.src]
    assert [i.action for i in april] == [P.COPY] * 10 + [P.OVER_CAP] * 2
    assert april[0].dsts == [f"002-MasterDarks/ASI2600MC Pro/120 Seconds/2026-04-28/{april[0].src.rsplit('/', 1)[1]}"]
    june = [i for i in darks if "20260616" in i.src]
    assert all(i.dsts[0].startswith("002-MasterDarks/ASI2600MC Pro/120 Seconds/2026-06-16/") for i in june)
    bias = by_name(plan, "Bias_")
    assert all(i.dsts[0].startswith("001-MasterBias/ASI2600MC Pro/2023-04-19 (+14C)/") for i in bias)


def test_library_duplicates_and_existing_folder(w):
    folder = "002-MasterDarks/ASI2600MC Pro/300 Seconds/2026-08-29"
    for i in range(2):
        asiair_frame(w.air, "Autorun/Dark", "Dark", f"20260829-11{i}418", seq=i + 1)
        asiair_frame(w.nas, folder, "Dark", f"20260829-11{i}418", seq=i + 1, thumb=False)
    plan = w.plan()
    assert {i.action for i in by_name(plan, "Dark_")} == {P.ALREADY}

    asiair_frame(w.air, "Autorun/Dark", "Dark", "20260829-150000", seq=7)   # same-day second batch, 3 h later
    asiair_frame(w.nas, "002-MasterDarks/ASI2600MC Pro/300 Seconds/2026-08-29", "Dark", "20260829-090000",
                 seq=9, thumb=False)
    plan = w.plan()
    d = decision(plan, "library-folder")
    assert by_name(plan, "150000")[0].action == P.PENDING and d.resolved is None


# ---------------------------------------------------------------- clashes, cleanup, determinism

def test_truncated_copy_on_nas(w):
    sadr = "SadrRegion-IC1318/2025-10-16-SadrRegion-2600MC-FMA135"
    good = w.light("20251016-204000", obj="NGC 6888", folder="Autorun/Light/NGC 6888", FOCALLEN=137)
    bad = w.nas / sadr / "lights" / good.name
    bad.parent.mkdir(parents=True)
    bad.write_bytes(good.read_bytes()[:3000])                      # a partial copy
    plan = w.plan()
    d = decision(plan, "name-clash")
    item = by_name(plan, good.name)[0]
    # a damaged NAS copy with a good one here: replace is the default (Chris)
    assert d.default == "replace" and "truncated" in d.question and item.warnings
    assert item.action == P.COPY and item.retire == [f"{sadr}/lights/{good.name}"]
    assert by_name(w.plan({d.id: "skip"}), good.name)[0].action == P.SKIP
    fixed = by_name(w.plan({d.id: "replace"}), good.name)[0]
    assert fixed.action == P.COPY and fixed.retire == fixed.dsts == [f"{sadr}/lights/{good.name}"]


def test_other_files_are_called_out(w):
    w.light("20260923-211420")
    (w.air / "Plan/Light/SoulNebula/astropup-view-scan.json").write_text("{}")
    (w.air / "Plan/Light/SoulNebula/._astropup-view-scan.json").write_bytes(b"x")
    (w.air / "Autorun/Dark").mkdir(parents=True)
    (w.air / "Autorun/Dark/Dark_300.0s_Bin1_2600MC_gain100_20260616-143306_132deg_-10.0C_0001_thn.jpg").write_bytes(b"j")
    asiair_frame(w.air, "Live/Light/M 13", "Light", "20260613-213513", exposure_s=30, obj="M 13")
    plan = w.plan()
    actions = {i.src.rsplit("/", 1)[1][:12]: (i.action, i.cleanup) for i in plan.items}
    assert actions["astropup-vie"] == (P.UNRECOGNIZED, "callout")
    assert actions["._astropup-v"] == (P.UNRECOGNIZED, "callout")
    assert actions["Dark_300.0s_"] == (P.ORPHAN_THUMB, "callout")
    assert actions["Light_M 13_3"] == (P.IGNORED, "never")


def test_plan_is_deterministic(w):
    for i in range(4):
        w.light(f"20260427-21{i}000", obj="NGC 4565", FOCALLEN=1384, seq=i + 1)
    w.flat("20260428-063800", exposure_s=15, angle=79, FOCALLEN=1384)
    a, b = w.plan().to_dict(), w.plan().to_dict()
    assert a == b
    assert a["summary"]["decisions_open"] == 1

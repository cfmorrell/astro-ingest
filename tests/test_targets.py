from pathlib import Path

import pytest

from astro_ingest.core.targets import catalog_id, load_targets, match_object

TARGETS = load_targets(Path(__file__).resolve().parents[1] / "docs" / "targets.csv")


@pytest.mark.parametrize("text, cid", [
    ("M 8", ("M", 8)), ("M8", ("M", 8)), ("m 31", ("M", 31)), ("NGC 7000", ("NGC", 7000)), ("NGC7000", ("NGC", 7000)),
    ("IC 1805", ("IC", 1805)), ("Sh2-136", ("SH2", 136)), ("Sh2 136", ("SH2", 136)), ("LBN777", ("LBN", 777)),
    ("Barnard33", ("B", 33)), ("Caldwell19", ("C", 19)), ("Mel22", ("MEL", 22)),
])
def test_catalog_id(text, cid):
    assert catalog_id(text) == cid


@pytest.mark.parametrize("text", ["ElephantTrunk", "Seven Sisters", "C-2025 A6", "Mizar", "M 13 west", ""])
def test_not_catalog_ids(text):
    assert catalog_id(text) is None


@pytest.mark.parametrize("obj, folder, by", [
    ("M 8", "LagoonNebula-M8", "catalog"),
    ("M13", "HerculesCluster-M13", "catalog"),
    ("NGC 7000", "NorthAmericaNebula-NGC7000", "catalog"),
    ("NGC 2841", "TigersEyeGalaxy-NGC2841", "catalog"),
    ("NGC 224", "AndromedaGalaxy-M31", "catalog"),          # secondary ID
    ("NGC 6995", "VeilNebula-NGC6960-NGC6992", "catalog"),
    ("M 84", "MarkariansChain", "catalog"),
    ("Sh2-136", "GhostNebula-Sh2-136", "catalog"),           # from the "other" column
    ("NGC 5907", "SplinterGalaxy-NGC5907", "catalog"),
    ("ElephantTrunk", "ElephantTrunkNebula-IC1396", "name"),  # name minus a generic suffix
    ("HeartNebula", "HeartNebula-IC1805", "name"),
    ("SoulNebula", "SoulNebula-IC1848", "name"),
    ("Sadr", "SadrRegion-IC1318", "name"),
])
def test_unique_matches(obj, folder, by):
    m = match_object(obj, TARGETS)
    assert m.status == "unique" and m.target.folder == folder and m.by == by


def test_shared_catalog_id_is_ambiguous():
    m = match_object("IC 1805", TARGETS)
    assert m.status == "ambiguous" and m.target is None
    assert [t.folder for t in m.candidates] == ["HeartNebula-IC1805", "HeartAndSoulNebulae-IC1805-IC1848"]


@pytest.mark.parametrize("obj", ["NGC 4565", "NGC 5982", "Seven Sisters", "Mizar", "Great Orion Nebula"])
def test_no_match_never_guesses(obj):
    assert match_object(obj, TARGETS).status == "none"


def test_name_suffix_rule_is_strict():
    # "Heart" + "Nebula" is HeartNebula; HeartAndSoulNebulae is not a generic-suffix extension of "Heart"
    assert [t.folder for t in match_object("Heart", TARGETS).candidates] == ["HeartNebula-IC1805"]

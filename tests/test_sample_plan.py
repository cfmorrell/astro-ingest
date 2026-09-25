"""Acceptance test: the plan for the real ASIAIR sample against the live NAS (read-only).

Skipped unless both are mounted (dev container). The numbers come from the Phase 2 survey in docs/PLAN.md.
"""

from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from astro_ingest.core import planner as P
from astro_ingest.core.nas import build_index
from astro_ingest.core.scan import scan
from astro_ingest.core.targets import load_targets
from astro_ingest.sources.local import LocalDirSource

SAMPLE = Path("/astro-sandbox/_asiair-sample")
NAS = Path("/astro")
TARGETS = NAS / "Z95-ClaudeReferences" / "targets.csv"

pytestmark = pytest.mark.skipif(not (SAMPLE.is_dir() and TARGETS.is_file()), reason="sample or NAS not mounted")


@pytest.fixture(scope="module")
def plan():
    return P.build_plan(scan(LocalDirSource(SAMPLE), ZoneInfo("America/New_York")), build_index(NAS),
                        load_targets(TARGETS))


def session(plan, rel):
    return next(s for s in plan.sessions if s.rel == rel)


def test_already_ingested_and_appends(plan):
    actions = Counter(i.action for i in plan.items)
    assert actions[P.ALREADY] == 446            # 447 by name; one NAS copy is truncated (name clash)
    assert actions[P.APPEND] == 22
    appended = Counter(i.dsts[0].split("/")[1] for i in plan.items if i.action == P.APPEND)
    assert appended == {"2026-09-14-ElephantTrunkNebula-2600MC-Z61": 8, "2026-09-13-ElephantTrunkNebula-2600MC-Z61": 1,
                        "2026-09-15-HeartNebula-2600MC-Z61": 9, "2026-04-11-OrionNebula-2600MC-RC6": 4}


def test_truncated_nas_copy(plan):
    d = [d for d in plan.decisions if d.kind == "name-clash"]
    assert len(d) == 1 and "truncated" in d[0].question and "20250704-003013" in d[0].items[0]
    assert d[0].resolved == "replace"


def test_new_sessions(plan):
    soul = session(plan, "SoulNebula-IC1848/2026-09-23-SoulNebula-2600MC-Z61")
    assert (soul.exists, soul.lights, soul.flats) == (False, 104, 10)
    splinter = session(plan, "SplinterGalaxy-NGC5907/2026-06-15-SplinterGalaxy-2600MC-RC6")
    assert (splinter.exists, splinter.lights, splinter.flats) == (False, 72, 10)
    assert any("79°" in w for w in splinter.warnings)


def test_open_decisions(plan):
    kinds = Counter(d.kind for d in plan.decisions if d.resolved is None)
    assert kinds == {"target": 2, "tiny-group": 1}
    targets = {d.question.split("'")[1] for d in plan.decisions if d.kind == "target"}
    assert targets == {"NGC 4565", "NGC 5982"}


def test_library(plan):
    darks = [i for i in plan.items if i.dsts and "/120 Seconds/" in i.dsts[0]]
    assert len(darks) == 10 and all(i.dsts[0].startswith("002-MasterDarks/ASI2600MC Pro/120 Seconds/2026-04-28/")
                                    for i in darks)
    assert all(i.action == P.ALREADY for i in plan.items if "Autorun/Bias/" in i.src and i.src.endswith(".fit"))


def test_blocked_and_called_out(plan):
    blocked = Counter(i.src.split("_")[5][:8] for i in plan.items if i.action == P.NO_LIGHTS)
    assert blocked == {"20260516": 10, "20260624": 10}
    assert sum(1 for i in plan.items if i.action == P.ORPHAN_THUMB) == 10
    unrecognized = [i for i in plan.items if i.action == P.UNRECOGNIZED]
    assert len(unrecognized) == 20 and all(i.cleanup == "callout" for i in unrecognized)
    assert any(i.src.endswith("astropup-view-scan.json") for i in unrecognized)


def test_ignored_never_touched(plan):
    for i in plan.items:
        if i.src.split("/")[0] in ("Live", "Preview", "Video", "log", "GuidingDarkLibrary"):
            assert i.action == P.IGNORED and i.cleanup == "never" and not i.dsts

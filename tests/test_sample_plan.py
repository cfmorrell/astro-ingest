"""Acceptance test: the plan for the real ASIAIR sample against the Astronomy share as it was on 2026-09-26.

Both sides are frozen so the numbers keep meaning something: the ASIAIR side is the sample copy of the device
(/astro-sandbox/_asiair-sample, dev container only: skipped elsewhere), and the NAS side is a snapshot of the share
before production started ingesting this same data (tests/data/nas-before-prod.json.gz, made by
dev/freeze_nas_snapshot.py: names and sizes only, rebuilt here as empty placeholder files). The numbers come from
the Phase 2 survey in docs/PLAN.md.
"""

import gzip
import json
import os
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
SNAPSHOT = Path(__file__).parent / "data" / "nas-before-prod.json.gz"

pytestmark = pytest.mark.skipif(not SAMPLE.is_dir(), reason="the ASIAIR sample isn't mounted (dev container only)")


@pytest.fixture(scope="module")
def frozen_nas(tmp_path_factory):
    """The share as it was: every frame as an empty placeholder of its real size (sparse), plus targets.csv."""
    root = tmp_path_factory.mktemp("nas")
    with gzip.open(SNAPSHOT, "rt") as f:
        snap = json.load(f)
    for rel, size in snap["files"]:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as fh:
            fh.truncate(size)
    targets = root / "Z95-ClaudeReferences" / "targets.csv"
    targets.parent.mkdir(parents=True, exist_ok=True)
    targets.write_text(snap["targets_csv"])
    return root


@pytest.fixture(scope="module")
def plan(frozen_nas):
    return P.build_plan(scan(LocalDirSource(SAMPLE), ZoneInfo("America/New_York")), build_index(frozen_nas),
                        load_targets(frozen_nas / "Z95-ClaudeReferences" / "targets.csv"))


def session(plan, rel):
    return next(s for s in plan.sessions if s.rel == rel)


def test_already_ingested_and_appends(plan):
    actions = Counter(i.action for i in plan.items)
    # The Phase 2 survey had 446 and 22 (with 4 appends into OrionNebula-M42/2026-04-11-OrionNebula-2600MC-RC6). That
    # session was removed from the share by hand on 2026-10-02, before the snapshot was frozen, so its 16 frames
    # aren't on the frozen share and those 4 frames start a new session instead.
    assert actions[P.ALREADY] == 430            # one more NAS copy by name is truncated (name clash)
    assert actions[P.APPEND] == 18
    appended = Counter(i.dsts[0].split("/")[1] for i in plan.items if i.action == P.APPEND)
    assert appended == {"2026-09-14-ElephantTrunkNebula-2600MC-Z61": 8, "2026-09-13-ElephantTrunkNebula-2600MC-Z61": 1,
                        "2026-09-15-HeartNebula-2600MC-Z61": 9}


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

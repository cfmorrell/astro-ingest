"""Target catalog (targets.csv) and matching capture object names to target folders.

Match on catalog ID first, then on name; never guess. A catalog ID can legitimately belong to several targets
(IC 1805 is both HeartNebula-IC1805 and HeartAndSoulNebulae-IC1805-IC1848), so matching returns every candidate
and the caller decides (usually from ingest evidence or by asking Chris).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

# Catalog prefixes as they appear in object names and in targets.csv's "other" column
_CATALOG = re.compile(
    r"^(?P<cat>M|NGC|IC|SH2|SH|LBN|LDN|B|BARNARD|C|CALDWELL|MEL|MELOTTE|CR|COLLINDER|VDB|ABELL)"
    r"[\s_-]*(?P<num>\d+)$",
    re.I,
)
_ALIASES = {"SH": "SH2", "BARNARD": "B", "CALDWELL": "C", "MELOTTE": "MEL", "COLLINDER": "CR"}
# Words a capture name may leave off a target name ("ElephantTrunk" -> ElephantTrunkNebula, "Sadr" -> SadrRegion)
_GENERIC_SUFFIXES = ("nebula", "nebulae", "galaxy", "cluster", "region")


def catalog_id(text: str) -> tuple[str, int] | None:
    """('M', 8) from 'M 8', 'M8', 'm-8'; ('NGC', 7000) from 'NGC 7000'; ('SH2', 136) from 'Sh2-136'."""
    t = text.strip()
    # "Sh2-136": the "2" belongs to the catalog name, not the number
    m = re.match(r"^sh\s*2\s*-\s*(\d+)$", t, re.I)
    if m:
        return ("SH2", int(m.group(1)))
    m = _CATALOG.match(t)
    if not m:
        return None
    cat = m["cat"].upper()
    return (_ALIASES.get(cat, cat), int(m["num"]))


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass(frozen=True)
class Target:
    folder: str          # e.g. "AndromedaGalaxy-M31"
    name: str            # e.g. "AndromedaGalaxy" (the session-name token)
    ids: frozenset[tuple[str, int]] = field(default_factory=frozenset)


def load_targets(path: str | Path) -> list[Target]:
    targets = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ids = set()
            for col, cat in (("messier", "M"), ("ngc", "NGC"), ("ic", "IC")):
                ids.update((cat, int(n)) for n in (row.get(col) or "").split(";") if n.strip())
            for other in (row.get("other") or "").split(";"):
                cid = catalog_id(other) if other.strip() else None
                if cid:
                    ids.add(cid)
            targets.append(Target(row["folder"], row["name"], frozenset(ids)))
    return targets


@dataclass(frozen=True)
class Match:
    object: str
    candidates: tuple[Target, ...]
    by: str | None  # "catalog" | "name" | None

    @property
    def target(self) -> Target | None:
        """The target when the match is unambiguous, else None."""
        return self.candidates[0] if len(self.candidates) == 1 else None

    @property
    def status(self) -> str:
        return {0: "none", 1: "unique"}.get(len(self.candidates), "ambiguous")


def match_object(obj: str, targets: list[Target]) -> Match:
    """Match an ASIAIR object name ('M 8', 'NGC 7000', 'ElephantTrunk', 'SoulNebula') to targets."""
    cid = catalog_id(obj)
    if cid:
        # Among several, the most specific target (fewest catalog IDs) is listed first; the match stays ambiguous
        hits = tuple(sorted((t for t in targets if cid in t.ids), key=lambda t: (len(t.ids), t.folder)))
        return Match(obj, hits, "catalog" if hits else None)
    key = _squash(obj)
    if not key:
        return Match(obj, (), None)
    hits = tuple(t for t in targets
                 if _squash(t.name) == key
                 or (_squash(t.name).startswith(key) and _squash(t.name)[len(key):] in _GENERIC_SUFFIXES))
    return Match(obj, hits, "name" if hits else None)


TARGETS_FIELDS = ["folder", "name", "messier", "ngc", "ic", "other"]


def new_target_row(folder: str, objects: list[str]) -> dict:
    """A targets.csv row for a new target folder Chris named (e.g. "NeedleGalaxy-NGC4565"), with every catalog ID found
    in the folder name and the frames' OBJECT names ("NGC 4565")."""
    ids: dict[str, set[int]] = {"M": set(), "NGC": set(), "IC": set()}
    other: list[str] = []
    for token in folder.split("-")[1:] + list(objects):
        cid = catalog_id(token)
        if not cid:
            continue
        if cid[0] in ids:
            ids[cid[0]].add(cid[1])
        else:
            label = {"SH2": "Sh2-"}.get(cid[0], cid[0])
            other.append(f"{label}{cid[1]}")
    return {"folder": folder, "name": folder.split("-")[0],
            "messier": ";".join(str(n) for n in sorted(ids["M"])), "ngc": ";".join(str(n) for n in sorted(ids["NGC"])),
            "ic": ";".join(str(n) for n in sorted(ids["IC"])), "other": ";".join(sorted(set(other)))}


def csv_text(rows: list[dict]) -> str:
    """targets.csv as the share keeps it: header row, one line per target, sorted by folder."""
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=TARGETS_FIELDS, lineterminator="\n")
    w.writeheader()
    for r in sorted(rows, key=lambda r: r["folder"]):
        w.writerow({k: r.get(k, "") for k in TARGETS_FIELDS})
    return buf.getvalue()

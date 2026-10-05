"""Freeze the Astronomy share as it was before production's first runs, for the sample acceptance test.

The test (tests/test_sample_plan.py) plans the frozen ASIAIR sample (/astro-sandbox/_asiair-sample) against the share;
its numbers were written down on 2026-09-26, before prod started ingesting that same data. This rebuilds that share
state: today's file listing, minus every file prod copied, plus the file prod retired, with the targets.csv from
before prod's first Catalog. Only names, sizes and folders are kept (all the planner reads). Read-only on the share.

    .venv/bin/python dev/freeze_nas_snapshot.py   # writes tests/data/nas-before-prod.json.gz
"""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
from pathlib import Path

NAS = Path("/astro")
PROD_DB = NAS / "Z95-ClaudeReferences/ingest/ingest.sqlite3"
ORIGINAL_TARGETS = NAS / "Z95-ClaudeReferences/_to_delete/targets.csv.20260927-225344"   # retired by prod's first Catalog
OUT = Path(__file__).resolve().parent.parent / "tests/data/nas-before-prod.json.gz"
FRAME = (".fit", ".fits", ".fts", ".xisf")


def listing(before: float) -> dict[str, int]:
    """Every frame file the NAS index would see (target folders and the 001/002 libraries), by path -> size,
    leaving out files that appeared after `before` (prod's first batch): stacked results and the like added since."""
    from astro_ingest.core import rules
    from astro_ingest.core.nas import LIBRARIES, RETIRED
    out = {}
    for top in sorted(os.listdir(NAS)):
        if not (top in LIBRARIES or rules.is_target_folder(top)) or (NAS / top).is_symlink():
            continue
        for dirpath, dirnames, filenames in os.walk(NAS / top):
            dirnames[:] = [d for d in dirnames if d != RETIRED and not d.startswith(".")
                           and not (Path(dirpath) / d).is_symlink()]
            for f in filenames:
                p = Path(dirpath) / f
                if f.lower().endswith(FRAME) and not f.startswith(".") and not p.is_symlink():
                    st = p.stat()
                    if st.st_mtime < before:   # survives renames and moves (ctime does not)
                        out[p.relative_to(NAS).as_posix()] = st.st_size
    return out


def main() -> None:
    db = sqlite3.connect(f"file:{PROD_DB}?mode=ro&immutable=1", uri=True)
    first_batch = db.execute("SELECT MIN(created_at) FROM batches").fetchone()[0]
    files = listing(first_batch)
    copied = [r[0] for r in db.execute("SELECT dst FROM operations WHERE kind='copy' AND status IN ('done','already-there')")]
    retired = [r[0] for r in db.execute("SELECT dst FROM operations WHERE kind='retire' AND status='done'")]
    for rel in copied:
        files.pop(rel, None)
    for rel in retired:                       # back where it was, at its (damaged) size
        moved = NAS / Path(rel).parent / "_to_delete" / Path(rel).name
        files[rel] = moved.stat().st_size
    snap = {"note": "Astronomy share before production's first runs (dev/freeze_nas_snapshot.py); later reorganisations by hand "
                    "(renamed sessions, frames moved for stacking) are as they were on the day it was made",
            "removed_prod_copies": len(copied), "restored_retired": len(retired),
            "targets_csv": ORIGINAL_TARGETS.read_text(), "files": sorted(files.items())}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt") as f:
        json.dump(snap, f)
    print(f"{OUT}: {len(files)} files ({len(copied)} prod copies removed, {len(retired)} retired file restored)")


if __name__ == "__main__":
    main()

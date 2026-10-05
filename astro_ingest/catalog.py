"""The Catalog step: the paperwork after Copy & verify (handoff §6, §7.4, §7.6, §9).

For every finished copy batch not catalogued yet:
- PROJECT_INFO.txt for each session that received frames, and for sessions whose library darks/bias match could
  change because a new library batch arrived;
- a targets.csv row for each new target (the previous targets.csv is retired to _to_delete/ first);
- the index links (100-…103-) and ZZ_TARGET_INDEX.md brought in line with targets.csv and the sessions;
- "Night X of N. Siblings: …" lines in .project_notes.txt for multi-night groups (added, never edited);
- .flats_are_copies in sessions that borrowed flats from a sibling night;
- a calibration-gap report and decision-log lines (drafts in STATE_DIR, not written into the guide).
preview() only reads; run() writes through fsops under the single-writer lock and logs every write.
"""

from __future__ import annotations

import csv
import datetime as dt
import difflib
import os
import io
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from astro_ingest import db, fsops, service
from astro_ingest.config import Config
from astro_ingest.core import calneeds, links, projinfo, rules
from astro_ingest.core.nas import calibration_library
from astro_ingest.core.targets import csv_text, new_target_row

TARGETS_CSV = "Z95-ClaudeReferences/targets.csv"
SIBLING_GAP_DAYS = 3


@dataclass
class Change:
    kind: str            # project-info | targets-csv | index-md | link-add | link-remove | notes | flats-note
    path: str            # relative to the share root
    status: str          # create | update | unchanged | add | remove
    why: str = ""
    diff: str | None = None
    text: str | None = field(default=None, repr=False)   # content to write
    target: str | None = None                             # symlink target


@dataclass
class Preview:
    batches: list[str]
    sessions: list[str]
    changes: list[Change]
    new_targets: list[dict]
    gaps: list[dict]
    log_lines: list[str]
    links_check: dict = field(default_factory=dict)   # links.check(): checked, broken, empty

    def summary(self) -> dict:
        c = Counter((ch.kind, ch.status) for ch in self.changes)
        return {"batches": self.batches, "sessions": len(self.sessions),
                "project_info": {s: c[("project-info", s)] for s in ("create", "update", "unchanged")},
                "new_targets": [t["folder"] for t in self.new_targets],
                "links_added": c[("link-add", "add")], "links_removed": c[("link-remove", "remove")],
                "notes": c[("notes", "update")] + c[("notes", "create")], "gaps": len(self.gaps),
                "writes": sum(1 for ch in self.changes if ch.status != "unchanged")}

    def to_dict(self) -> dict:
        return {"summary": self.summary(), "sessions": self.sessions, "new_targets": self.new_targets,
                "gaps": self.gaps, "log_lines": self.log_lines, "links_check": self.links_check,
                "changes": [{k: v for k, v in asdict(ch).items() if k != "text"} for ch in self.changes]}


def _roots(cfg: Config) -> list[Path]:
    roots = [Path(cfg.astro_root)]
    if Path(cfg.astro_nas).resolve() != Path(cfg.astro_root).resolve():
        roots.append(Path(cfg.astro_nas))   # dev: the live share (read-only) behind the sandbox
    return roots


def _read_first(roots: list[Path], rel: str) -> str | None:
    for r in roots:
        p = r / rel
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    return None


def _library(roots: list[Path]):
    seen, out = set(), []
    for r in roots:
        for s in calibration_library(r):
            if s.rel not in seen:
                seen.add(s.rel)
                out.append(s)
    return out


def _unified(old: str | None, new: str, path: str) -> str:
    strip = lambda t: [l for l in (t or "").splitlines() if not l.startswith("Generated")]  # noqa: E731
    return "\n".join(difflib.unified_diff(strip(old), strip(new), f"{path} (now)", f"{path} (new)", lineterm="", n=1))


def preview(cfg: Config, today: dt.date | None = None, progress=lambda *a, **k: None) -> Preview:
    today = today or dt.datetime.now(cfg.tz).date()
    roots = _roots(cfg)
    batches = db.uncatalogued_batches(cfg)
    touched: set[str] = set()
    received: set[str] = set()
    lib_folders: set[str] = set()
    borrowed: dict[str, str] = {}      # session -> source session its flats were borrowed from
    retired: list[str] = []
    for b in batches:
        for o in b["operations"]:
            if o["kind"] == "copy" and o["status"] in ("done", "already-there"):
                parts = o["dst"].split("/")
                if parts[0][:1].isdigit():
                    lib_folders.add("/".join(parts[:-1]))
                else:
                    touched.add("/".join(parts[:2]))
                    received.add("/".join(parts[:2]))
            elif o["kind"] == "retire" and o["status"] == "done":
                retired.append(o["dst"])
        for sess, src in ((b.get("summary") or {}).get("borrowed") or {}).items():
            if sess in received:
                borrowed[sess] = src

    progress(2, "reading the NAS", phase="index")
    index = service.nas_index(cfg)
    sessions_all = sorted({s.rel for s in index.sessions} | touched)

    # sessions whose library match may change because a new dark/bias batch arrived
    progress(10, "checking which sessions new library frames affect", phase="library")
    for folder in sorted(lib_folders):
        parts = folder.split("/")
        cam = parts[1] if len(parts) > 1 else ""
        exp = re.match(r"(\d+) Seconds", parts[2]) if parts[0].startswith("002-") and len(parts) > 2 else None
        for s in sessions_all:
            text = _read_first(roots, f"{s}/PROJECT_INFO.txt")
            if text and f"master-folder name: {cam})" in text and (exp is None or f"For {exp.group(1)}s lights" in text):
                touched.add(s)

    changes: list[Change] = []
    lib = _library(roots)
    gaps, objects_by_target = [], {}
    todo_sessions = sorted(touched)
    for n, s in enumerate(todo_sessions, 1):
        progress(15 + 75 * (n - 1) / max(len(todo_sessions), 1), f"PROJECT_INFO for {s}", phase="sessions",
                 sessions_done=n - 1, sessions_total=len(todo_sessions))
        frames, other, notes = projinfo.scan_session(roots, s)
        text = projinfo.render(s, frames, other, notes, lib, cfg.tz, today)
        rel = f"{s}/PROJECT_INFO.txt"
        if text is None:
            changes.append(Change("project-info", rel, "unchanged", "no light frames: nothing to describe"))
            continue
        old = _read_first(roots, rel)
        d = _unified(old, text, rel)
        status = "create" if old is None else ("update" if d else "unchanged")
        why = "received frames" if s in received else "a new library batch may change its darks/bias"
        changes.append(Change("project-info", rel, status, why, d or None, text))
        gaps += [asdict(g) for g in calneeds.gaps(s, frames, lib)]
        objects_by_target.setdefault(s.split("/")[0], Counter()).update(
            projinfo._f(x.h, "OBJECT") for x in frames if x.kind == "Light" and projinfo._f(x.h, "OBJECT"))

    # new targets: a target folder that received frames but isn't in targets.csv
    rows_text = _read_first(roots, TARGETS_CSV) or ""
    rows = list(csv.DictReader(io.StringIO(rows_text))) if rows_text else []
    known = {r["folder"] for r in rows}
    new_rows = [new_target_row(t, list(objs)) for t, objs in sorted(objects_by_target.items())
                if t not in known and rules.is_target_folder(t)]
    if new_rows:
        new_csv = csv_text(rows + new_rows)
        changes.append(Change("targets-csv", TARGETS_CSV, "update", "new target(s): " +
                              ", ".join(r["folder"] for r in new_rows),
                              "\n".join(difflib.unified_diff(rows_text.splitlines(), new_csv.splitlines(), lineterm="", n=0)),
                              new_csv))

    # multi-night sibling notes (added to every member of a group that includes a touched session)
    changes += _sibling_notes(roots, touched, index)
    for s, src in sorted(borrowed.items()):
        note = f"{s}/.flats_are_copies"
        changes.append(Change("flats-note", note, "unchanged" if _read_first(roots, note) is not None else "create",
                              f"flats borrowed from {src}",
                              text=f"flats in this session are copies from {src.split('/')[1]} "
                                   f"(borrowed by astro-ingest on {today.isoformat()})\n"))

    # index links + ZZ_TARGET_INDEX.md
    root = Path(cfg.astro_root)
    all_rows = rows + new_rows
    progress(92, "checking the index links", phase="links", sessions_done=len(todo_sessions),
             sessions_total=len(todo_sessions))
    want = links.wanted(root, all_rows)
    add, remove = links.diff(root, want)
    # dev: the sandbox holds only part of the share; a link the live share already has isn't "missing"
    for other in roots[1:]:
        add = [a for a in add if not ((other / a).is_symlink() and os.readlink(other / a) == want[a])]
    lcheck = links.check(root)
    gone = {b["link"] for b in lcheck["broken"]}
    changes += [Change("link-remove", r, "remove", "the folder is gone" if r in gone else
                       "no longer matches targets.csv / the sessions") for r in remove]
    changes += [Change("link-add", a, "add", "", target=want[a]) for a in add]
    md = links.index_markdown(all_rows)
    old_md = (root / links.INDEX_MD).read_text() if (root / links.INDEX_MD).is_file() else None
    md_changed = old_md is None or [l for l in old_md.splitlines() if l.startswith("| `")] != \
        [l for l in md.splitlines() if l.startswith("| `")]
    changes.append(Change("index-md", links.INDEX_MD, ("create" if old_md is None else "update") if md_changed
                          else "unchanged", "", None, md))

    log_lines = [f"| {today.isoformat()} | New target `{r['folder']}` added to targets.csv by astro-ingest "
                 f"(NGC {r['ngc'] or '-'}, IC {r['ic'] or '-'}, M {r['messier'] or '-'}). |" for r in new_rows]
    log_lines += [f"| {today.isoformat()} | Damaged NAS copy `{r}` retired to `_to_delete/` and replaced from the "
                  "ASIAIR by astro-ingest. |" for r in retired]
    log_lines += [f"| {today.isoformat()} | New library batch `{f}` ingested by astro-ingest. |" for f in sorted(lib_folders)]
    log_lines += [f"| {today.isoformat()} | `{s}` borrowed flats from `{src}` (approved on Review). |"
                  for s, src in sorted(borrowed.items())]
    return Preview([b["id"] for b in batches], sorted(touched), changes, new_rows, gaps, log_lines, lcheck)


def _sibling_notes(roots: list[Path], touched: set[str], index) -> list[Change]:
    """Groups of sessions of one target, camera, scope and mosaic flag on nights at most 3 days apart."""
    by_setup: dict[tuple, list] = {}
    rels = {s.rel for s in index.sessions} | touched
    for rel in rels:
        t, f = rel.split("/", 1)
        p = rules.parse_session_name(f)
        if p:
            by_setup.setdefault((t, p.camera_token, p.scope_token, p.mosaic), []).append((p.night, rel))
    out = []
    for members in by_setup.values():
        members.sort()
        groups, cur = [], [members[0]]
        for m in members[1:]:
            if (m[0] - cur[-1][0]).days <= SIBLING_GAP_DAYS:
                cur.append(m)
            else:
                groups.append(cur)
                cur = [m]
        groups.append(cur)
        for g in groups:
            if len(g) < 2 or not any(rel in touched for _, rel in g):
                continue
            for i, (_, rel) in enumerate(g, 1):
                others = [r.split("/", 1)[1] for _, r in g if r != rel]
                notes = _read_first(roots, f"{rel}/{projinfo.NOTES}") or ""
                if all(o in notes for o in others):
                    continue   # already cross-referenced
                line = f"Night {i} of {len(g)}. Siblings: " + ", ".join(f"../{o}" for o in others)
                out.append(Change("notes", f"{rel}/{projinfo.NOTES}", "update" if notes else "create",
                                  "multi-night group", f"+ {line}", line))
    return out


def run(cfg: Config, pv: Preview, progress=lambda *a, **k: None) -> dict:
    """Write everything in the preview (under the lock), log it, and mark its batches catalogued."""
    stamp = dt.datetime.now(cfg.tz).strftime("%Y%m%d-%H%M%S")
    log_path = Path(cfg.state_dir) / "logs" / f"catalog-{stamp}.log"
    cfg.check_writable(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    roots = _roots(cfg)
    done = Counter()
    todo = [c for c in pv.changes if c.status != "unchanged"]
    order = {"targets-csv": 0, "project-info": 1, "notes": 2, "flats-note": 3, "link-remove": 4, "link-add": 5, "index-md": 6}
    with fsops.WriteLock(cfg, f"catalog {stamp}"), open(log_path, "a") as log:
        for n, c in enumerate(sorted(todo, key=lambda c: order[c.kind]), 1):
            progress(n / max(len(todo), 1) * 100, f"{c.kind}: {c.path}", written=n - 1, to_write=len(todo))
            if c.kind == "targets-csv":
                status = fsops.replace_source_file(cfg, c.path, c.text, stamp)
            elif c.kind in ("project-info", "index-md"):
                status = fsops.write_generated(cfg, c.path, c.text)
            elif c.kind == "notes":
                status = fsops.append_lines(cfg, c.path, [c.text], existing=_read_first(roots[1:], c.path))
            elif c.kind == "flats-note":
                status = fsops.write_new(cfg, c.path, c.text)
            elif c.kind == "link-remove":
                status = fsops.remove_link(cfg, c.path)
            else:
                status = fsops.add_link(cfg, c.path, c.target)
            done[(c.kind, status)] += 1
            tag = "SKIP" if status in ("clash", "skipped") else "OK"
            log.write(f"{tag}\t{c.kind}\t{c.path}\t{status}{' -> ' + c.target if c.target else ''}\n")
        if pv.log_lines:
            drafts = Path(cfg.state_dir) / "decision-log-drafts.md"
            cfg.check_writable(drafts)
            with open(drafts, "a") as f:
                f.write("\n".join(pv.log_lines) + "\n")
    db.mark_catalogued(cfg, pv.batches)
    return {"writes": sum(done.values()), "by_kind": {f"{k} {s}": v for (k, s), v in sorted(done.items())},
            "batches": pv.batches, "log": str(log_path), "decision_log_drafts": len(pv.log_lines)}

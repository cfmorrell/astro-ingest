"""Command-line entry point: the same library the web app uses, for Claude sessions and scripting."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

from astro_ingest.config import VERSION, Config, ConfigError
from astro_ingest.core import planner as P
from astro_ingest.core.scan import Scan, scan
from astro_ingest.sources.local import LocalDirSource


def _gb(n: int) -> str:
    return f"{n / 1e9:6.2f} GB"


def print_scan(result: Scan, out=sys.stdout) -> None:
    w = lambda s="": print(s, file=out)  # noqa: E731
    w(f"Source: {result.source_label}")
    handled = result.handled()
    w(f"Handled frames: {len(handled)}  ({_gb(sum(f.size_with_thumb for f in handled))} incl. thumbnails)")
    w()
    groups: dict[tuple, list] = defaultdict(list)
    for f in handled:
        n = f.name
        key = (f.folder, f.kind or "?", f.night.isoformat() if f.night else "?",
               f"{n.exposure_s:g}s" if n else "?", n.filter or "-" if n else "?")
        groups[key].append(f)
    w(f"{'folder':34} {'type':8} {'night':10} {'exp':>7} {'filter':6} {'n':>4} {'size':>9}  angles")
    for key in sorted(groups):
        fs = groups[key]
        angles = sorted({f.name.angle for f in fs if f.name and f.name.angle is not None})
        w(f"{key[0][:34]:34} {key[1]:8} {key[2]:10} {key[3]:>7} {key[4]:6} {len(fs):4d} {_gb(sum(f.size_with_thumb for f in fs))}"
          f"  {', '.join(f'{a}°' for a in angles)}")
    w()
    by_top = Counter()
    size_top = Counter()
    for f in result.frames:
        if f.category != "handled":
            top = f.rel.split("/")[0]
            by_top[top] += 1
            size_top[top] += f.size_with_thumb
    if by_top:
        w("Not handled (never ingested or deleted): " +
          ", ".join(f"{t} {n} frames/{_gb(size_top[t]).strip()}" for t, n in sorted(by_top.items())))
    if result.orphan_thumbs:
        w(f"Thumbnails without a .fit: {len(result.orphan_thumbs)}")
    if result.unrecognized:
        w(f"Unrecognized files in Autorun/Plan (offered for cleanup, each needs approval): {len(result.unrecognized)}")
        for e in result.unrecognized:
            w(f"    {e.rel}  ({e.size} bytes)")
    if result.other_files:
        tops = Counter(e.rel.split("/")[0] for e in result.other_files)
        w("Other files (not frames): " + ", ".join(f"{t} {n}" for t, n in sorted(tops.items())))
    warnings: dict[str, list[str]] = defaultdict(list)
    for f in result.frames:
        for msg in f.warnings:
            warnings[msg.split(":")[0]].append(f"{f.rel}: {msg}")
    if warnings:
        w()
        w("Warnings:")
        for kind, items in sorted(warnings.items()):
            w(f"  {len(items):4d} x {kind}")
            for item in items[:3]:
                w(f"         e.g. {item}")


def cmd_scan(args: argparse.Namespace) -> int:
    root = args.source or os.environ.get("ASIAIR_ROOT")
    if not root:
        print("scan: give --source DIR or set ASIAIR_ROOT (SMB sources arrive in phase 3)", file=sys.stderr)
        return 2
    tz = ZoneInfo(os.environ.get("TZ") or "America/New_York")

    def progress(done: int, total: int) -> None:
        if sys.stderr.isatty() and (done % 50 == 0 or done == total):
            print(f"\rreading headers {done}/{total}", end="" if done < total else "\n", file=sys.stderr)

    print_scan(scan(LocalDirSource(root), tz, read_headers=not args.no_headers, progress=progress))
    return 0


def print_plan(plan: P.Plan, out=sys.stdout) -> None:
    w = lambda s="": print(s, file=out)  # noqa: E731
    sm = plan.summary()
    w(f"Source: {plan.source}")
    w(f"To copy: {sm['copy_files']} files, {_gb(sm['copy_bytes']).strip()}   "
      f"sessions: {sm['sessions_new']} new, {sm['sessions_existing']} already on the NAS   "
      f"decisions open: {sm['decisions_open']} of {sm['decisions_total']}")
    w("Actions: " + ", ".join(f"{k} {v}" for k, v in sm["actions"].items()))
    w()
    w("SESSIONS")
    for s in plan.sessions:
        if s.exists and not (s.lights or s.flats):
            continue
        status = "NEW TARGET" if s.new_target else ("new" if not s.exists else "exists")
        w(f"  {status:10} {s.rel}  +{s.lights} lights +{s.flats} flats  {_gb(s.bytes).strip()}")
        for warning in s.warnings:
            w(f"             ! {warning}")
    untouched = sum(1 for s in plan.sessions if s.exists and not (s.lights or s.flats))
    if untouched:
        w(f"  ({untouched} session(s) already on the NAS with nothing new to copy)")
    library = defaultdict(list)
    for i in plan.items:
        if i.dsts and i.dsts[0].startswith(("001-", "002-")) and i.action == P.COPY:
            library[i.dsts[0].rsplit("/", 1)[0]].append(i)
    if library:
        w()
        w("CALIBRATION LIBRARY")
        for folder, items in sorted(library.items()):
            w(f"  new        {folder}  {len(items)} frames")
    if plan.decisions:
        w()
        w("DECISIONS")
        for d in plan.decisions:
            state = f"answer: {d.answer}" if d.answer else (f"default: {d.default}" if d.default else "OPEN")
            w(f"  [{state}] {d.id}")
            w(f"      {d.question}")
            w("      options: " + " | ".join(o["value"] or "(none)" for o in d.options))
    w()
    w("CLEANUP PREVIEW (nothing is deleted in this phase)")
    labels = {"after-verify": "deleted from the ASIAIR after checksum-verified copies + your approval",
              "callout": "offered for deletion, each called out", "blocked": "blocked until you decide",
              "pending": "waiting on a decision", "never": "never touched"}
    for key, v in sm["cleanup"].items():
        w(f"  {v['files']:5d} files {_gb(v['bytes'])}  {labels.get(key, key)}")
    callouts = [i for i in plan.items if i.cleanup == "callout"]
    for i in callouts:
        w(f"        {i.action:13} {i.src}")


def load_config() -> Config | None:
    try:
        return Config.from_env()
    except ConfigError as e:
        print(f"config: {e}", file=sys.stderr)
        return None


def cmd_plan(args: argparse.Namespace) -> int:
    import json

    from astro_ingest.service import SourceUnavailable, open_source, scan_and_plan
    cfg = load_config()
    if cfg is None:
        return 2
    try:
        source = open_source(cfg, args.source)
    except SourceUnavailable as e:
        print(f"plan: {e}", file=sys.stderr)
        return 2
    answers = json.loads(open(args.answers).read()) if args.answers else None
    planned = scan_and_plan(cfg, source, answers)
    if args.json:
        json.dump(planned.plan.to_dict(), sys.stdout, indent=1)
        print()
    else:
        print_plan(planned.plan)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from astro_ingest.api import create_app
    cfg = load_config()
    if cfg is None:
        return 2
    uvicorn.run(create_app(cfg), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="astro-ingest")
    parser.add_argument("--version", action="version", version=f"astro-ingest {VERSION}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("scan", help="inventory a capture source (read-only)")
    p.add_argument("--source", help="local directory to scan (default: $ASIAIR_ROOT)")
    p.add_argument("--no-headers", action="store_true", help="filenames only; don't open any frame")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("plan", help="show what would be copied where (read-only)")
    p.add_argument("--source", help="local directory to plan from (default: $ASIAIR_ROOT)")
    p.add_argument("--answers", help="JSON file of {decision_id: answer} to apply")
    p.add_argument("--json", action="store_true", help="print the full plan as JSON")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("serve", help="run the web app")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

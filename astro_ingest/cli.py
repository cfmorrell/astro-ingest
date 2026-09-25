"""Command-line entry point: the same library the web app uses, for Claude sessions and scripting."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

from astro_ingest.config import VERSION
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="astro-ingest")
    parser.add_argument("--version", action="version", version=f"astro-ingest {VERSION}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("scan", help="inventory a capture source (read-only)")
    p.add_argument("--source", help="local directory to scan (default: $ASIAIR_ROOT)")
    p.add_argument("--no-headers", action="store_true", help="filenames only; don't open any frame")
    p.set_defaults(func=cmd_scan)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

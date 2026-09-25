"""Command-line entry point. Subcommands (scan, plan, apply, …) are added phase by phase."""

from __future__ import annotations

import argparse

from astro_ingest.config import VERSION


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="astro-ingest")
    parser.add_argument("--version", action="version", version=f"astro-ingest {VERSION}")
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

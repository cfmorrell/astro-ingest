#!/usr/bin/env python3
"""Screenshot a page of the running web app with headless Chromium, so Claude can see what the UI looks like.

Usage:  .venv/bin/python dev/screenshot.py [URL] [OUT.png] [--width 1400] [--click TEXT ...] [--full]
Defaults: http://localhost:8000/ -> /tmp/screenshot.png. --click clicks elements containing TEXT, in order, before the
shot (e.g. a stepper step or a "show files" toggle). Prints browser console errors so broken JS shows up.
"""

import argparse
import sys

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default="http://localhost:8000/")
    ap.add_argument("out", nargs="?", default="/tmp/screenshot.png")
    ap.add_argument("--width", type=int, default=1400)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--click", action="append", default=[])
    ap.add_argument("--full", action="store_true", help="capture the full scrollable page")
    args = ap.parse_args()

    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"page error: {e}"))
        page.goto(args.url, wait_until="networkidle")
        for text in args.click:
            page.get_by_text(text, exact=False).first.click()
            page.wait_for_load_state("networkidle")
        page.screenshot(path=args.out, full_page=args.full)
        browser.close()
    print(f"saved {args.out}")
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

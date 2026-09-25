# CLAUDE.md: astro-ingest

## What this is
A web app that ingests astrophotography capture data (first source: ZWO ASIAIR via its SMB share) into Chris's
Astronomy archive and files it by the archive's rules. **Read `docs/ClaudeHandoff.md` in full before writing code**,
then `docs/ORGANIZATION_GUIDE.md` (rules and decision log). `docs/EQUIPMENT.md` has gear details and
`docs/targets.csv` has the target catalog. When the live archive is mounted, the canonical versions of these files are
`/astro/Z95-ClaudeReferences/*` and `/astro/ZZ_EQUIPMENT.md`; if they differ from `docs/`, the live files win,
so tell Chris.

## Chris's decisions (from the handoff Q&A)
1. Source: **ASIAIR via SMB** only, for now.
2. **Always show a review screen**: index the source, show exactly what will be copied where, copy on approval,
   verify (size + checksum), and only then offer to delete the copied files **from the source**.
3. Stop at filing + PROJECT_INFO. **No stacking** (a separate Siril app does that).
4. App state lives **on the share** (`STATE_DIR`, under `Z95-ClaudeReferences/`), so Claude sessions and the app
   share one source of truth.

## Environment (dev container on UnRAID `FractalR5Tower`)
| Container path | Host path | Mode | Purpose |
|---|---|---|---|
| `/workspace` | `/mnt/user/docker_appdata/astro-ingest/src` | rw | this repo |
| `/astro` | `/mnt/user/Astronomy` | **read-only** | the real archive: read for lookups only |
| `/astro-sandbox` | `/mnt/user/astro-sandbox` | rw | test copy of part of the archive. **All dev writes go here** |
| `/asiair` | `/mnt/remotes/ASIAIR` | read-only | ASIAIR SMB share (may be offline; the app must cope) |
| `/home/dev` | `/mnt/user/docker_appdata/astro-ingest/home` | rw | persisted home (Claude Code, ssh keys, git config) |

Web port: container `8000` → host `8090`. Config comes from env vars (see `.env.example`): `ASTRO_ROOT` (write target),
`ASTRO_ARCHIVE`, `ASIAIR_ROOT`, `STATE_DIR`, `TZ`. **Never hard-code paths.**

## Hard rules
- In dev, **write only under `ASTRO_ROOT=/astro-sandbox`**. `/astro` is mounted read-only; don't try to work around that.
- Never delete inside the archive; retire to `_to_delete/`. Never overwrite; skip and report clashes.
  Verify counts and sizes/checksums around every copy or move, and log every operation.
- Deleting from the **source** is allowed only after checksum-verified copies **and** Chris's approval in the UI.
- Don't follow symlinks when walking; skip `100-`…`103-` index folders, `0*` and `Z*` folders, and dotfiles.
- Never create `.DS_Store`, `desktop.ini`, or `._*` files. Write atomically (`*.part` then rename).
- Test fixtures must be tiny, generated FITS files. Never commit real subs (`.gitignore` blocks `*.fit*`).

## Suggested stack (Chris can override)
Python 3.12, FastAPI + Jinja2 + HTMX (server-rendered, no SPA build), SQLite in `STATE_DIR`, `astropy` or the
existing `reference/scripts/fitshdr.py` for headers, pytest. Keep filing logic in a pure, well-tested library
module, separate from the web layer, so it can also run from the CLI.

## Working style
- Use plan mode for anything touching filing rules; propose, then implement in small, reviewable commits.
- Reuse the logic in `reference/scripts/` (night splitting, dark matching, PROJECT_INFO, index links, 10-frame
  calibration cap) instead of reinventing it; port it into the package with tests.
- Commit early and often with clear messages. Don't push secrets. Ask Chris before adding new top-level archive folders.

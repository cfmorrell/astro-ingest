# CLAUDE.md: astro-ingest

## What this is
A web app that ingests astrophotography capture data (first source: ZWO ASIAIR via its SMB share) into Chris's
Astronomy archive and files it by the archive's rules. **Goal: make it simple to pull data off the ASIAIR, organize it,
and keep the ASIAIR clean.** **Read `docs/ClaudeHandoff.md` in full before writing code**,
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

## Chris's decisions (planning session, 2026-09-25)
5. Only the ASIAIR's **`EMMC Images`** share. The app **finds the ASIAIR on `192.168.1.0/24`** and talks SMB to it
   directly (guest). `ASIAIR_ROOT` can point at a local directory instead (dev: `/astro-sandbox/_asiair-sample`).
6. Frames already in the archive: checksum-compare with the archive copy, then offer them for source cleanup. Never re-copy.
7. Frames missing from an existing archived session (e.g. subs Chris once dropped as poor quality): ask per batch,
   default **append**. Chris wants all data for now; frame-quality rejection may come later.
8. `Live`, `Preview`, `Video`, `log`, `GuidingDarkLibrary` are ignored unless Chris asks for them. Never delete them.
9. Source cleanup deletes each `.fit` **and its `_thn.jpg`** thumbnail. Thumbnails are never copied to the archive.
10. Before any flat or dark-flat set is deleted from the source, look for the lights it belongs to (on the ASIAIR and
    in the archive). If none are found, block its deletion until Chris files or explicitly releases it.
11. Night date comes from the local timestamp in the ASIAIR filename (minus 12 h), cross-checked against
    `DATE-OBS` converted with `zoneinfo` (`TZ`). Warn if they disagree; never use a fixed UTC offset.
12. **UI consistency with [astro-stacker](https://github.com/cfmorrell/astro-stacker)**; the apps may merge one day.
    Mirror its stack: plain `static/index.html` + `app.js` + `styles.css` (its CSS as the base), no build step or
    framework, FastAPI JSON API with `StaticFiles` mounted last, stacker-shaped `/jobs` API, and `config.VERSION`
    shown via `/health`, staying under 1.0.

## Environment (dev container on UnRAID `FractalR5Tower`)
| Container path | Host path | Mode | Purpose |
|---|---|---|---|
| `/workspace` | `/mnt/user/docker_appdata/astro-ingest-dev/src` | rw | this repo |
| `/astro` | `/mnt/user/Astronomy` | **read-only** | the real archive: read for lookups only |
| `/astro-sandbox` | `/mnt/user/astro-sandbox` | rw | test copy of part of the archive. **All dev writes go here** |
| `/asiair` | `/mnt/remotes/ASIAIR` | read-only | ASIAIR SMB share (may be offline; the app must cope) |
| `/home/dev` | `/mnt/user/docker_appdata/astro-ingest-dev/home` | rw | persisted home (Claude Code, ssh keys, git config) |

Web port: container `8000` → host `8090`. Config comes from env vars (see `.env.example`; `dev/run-dev.sh` passes
the repo's `.env`): `ASTRO_ROOT` (write target), `ASTRO_ARCHIVE`, `STATE_DIR`, `TZ`, `ASIAIR_ROOT`, `ASIAIR_SUBNET`,
`ASIAIR_SHARE`, `ASIAIR_HOST`. **Never hard-code paths.** Python deps live in `/workspace/.venv`
(`python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`); run tests with `.venv/bin/pytest`.

## Hard rules
- In dev, **write only under `ASTRO_ROOT=/astro-sandbox`**. `/astro` is mounted read-only; don't try to work around that.
- Never delete inside the archive; retire to `_to_delete/`. Never overwrite; skip and report clashes.
  Verify counts and sizes/checksums around every copy or move, and log every operation.
- Deleting from the **source** is allowed only after checksum-verified copies **and** Chris's approval in the UI.
- Don't follow symlinks when walking; skip `100-`…`103-` index folders, `0*` and `Z*` folders, and dotfiles.
- Never create `.DS_Store`, `desktop.ini`, or `._*` files. Write atomically (`*.part` then rename).
- Test fixtures must be tiny, generated FITS files (`tests/fitsgen.py`). Never commit real subs or thumbnails
  (`.gitignore` blocks `*.fit*`, `*.xisf`, `*.jpg`, `*.jpeg`).

## Stack
Python 3.12, FastAPI JSON API + static vanilla-JS frontend (decision 12), `smbprotocol` for the ASIAIR, SQLite in
`STATE_DIR` (rollback journal, not WAL, on the shfs mount), a port of `reference/scripts/fitshdr.py` for headers,
pytest. Keep filing logic in the pure, well-tested `astro_ingest/core/` library, separate from the web layer, so it
can also run from the CLI (`astro-ingest …`). The architecture and phase plan live in `docs/PLAN.md`.

## Working style
- Use plan mode for anything touching filing rules; propose, then implement in small, reviewable commits.
- Reuse the logic in `reference/scripts/` (night splitting, dark matching, PROJECT_INFO, index links, 10-frame
  calibration cap) instead of reinventing it; port it into the package with tests.
- Commit early and often with clear messages. Don't push secrets. Ask Chris before adding new top-level archive folders.

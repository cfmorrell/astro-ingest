# CLAUDE.md: astro-ingest

## What this is
A web app that ingests astrophotography capture data (first source: ZWO ASIAIR via its SMB share) into Chris's
Astronomy share on the NAS and files it by the share's rules. **Goal: make it simple to pull data off the ASIAIR, organize it,
and keep the ASIAIR clean.** **Read `docs/ClaudeHandoff.md` in full before writing code**,
then `docs/ORGANIZATION_GUIDE.md` (rules and decision log). `docs/EQUIPMENT.md` has gear details and
`docs/targets.csv` has the target catalog. When the live share is mounted, the canonical versions of these files are
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
6. Frames already ingested onto the NAS: checksum-compare with the NAS copy, then offer them for source cleanup. Never re-copy.
7. Frames missing from an existing session on the NAS (e.g. subs Chris once dropped as poor quality): ask per batch,
   default **append** — but frame-quality screening (decision 17) still applies to them.
8. `Live`, `Preview`, `Video`, `log`, `GuidingDarkLibrary` are ignored unless Chris asks for them. Never delete them.
9. Source cleanup deletes each `.fit` **and its `_thn.jpg`** thumbnail. Thumbnails are never copied to the NAS.
   Anything else in `Autorun/`/`Plan/` (other tools' files like `astropup-view-scan.json`, `._*`, `.DS_Store`,
   orphan thumbnails) is **included in cleanup but called out one by one** and deleted only with Chris's approval.
   Never silently ignore or silently delete a file.
10. Before any flat or dark-flat set is deleted from the source, look for the lights it belongs to (on the ASIAIR and
    on the NAS). If none are found, block its deletion until Chris files or explicitly releases it.
11. Night date comes from the local timestamp in the ASIAIR filename (minus 12 h), cross-checked against
    `DATE-OBS` converted with `zoneinfo` (`TZ`). Warn if they disagree; never use a fixed UTC offset.
12. **UI consistency with [astro-stacker](https://github.com/cfmorrell/astro-stacker)**; the apps may merge one day.
    Mirror its stack: plain `static/index.html` + `app.js` + `styles.css` (its CSS as the base), no build step or
    framework, FastAPI JSON API with `StaticFiles` mounted last, stacker-shaped `/jobs` API, and `config.VERSION`
    shown via `/health`, staying under 1.0.

13. **Ask whenever the target is unclear** (several candidates or none); a unique catalog/name match or frames already
    on the NAS decide it. No special cases for particular scopes.
14. **10-frame cap on ingest:** copy the first 10 frames of each calibration batch; offer the extras for source
    deletion, called out. **Light groups of 3 frames or fewer** are asked about (file them, or treat as test frames).
15. **Temperature suffix** = the batch's mean CCD-TEMP, rounded; none when it rounds to -10 °C.
16. **Damaged NAS copies:** when the NAS copy of a frame is damaged (e.g. truncated) and the ASIAIR has a good one,
    use the better copy: default answer is *replace* (retire the bad NAS copy to `_to_delete/`, then copy).
    Answers to plan decisions live in `STATE_DIR/answers.json` until Phase 4 moves them to SQLite.
17. **Frame-quality screening before ingest** (Phase 2b). Every light frame is scored against its group (the other
    frames of that night/object/camera/scope/filter, plus lights already in the destination session on the NAS)
    with astro-stacker's method: star count, FWHM, eccentricity, SNR, sky background; robust MAD z-scores.
    **Flagged frames are not ingested by default** (red border); one click keeps any of them. Default sensitivity
    **σ 4.0** (stacker uses 3.0; Chris chose 4.0 after it caught all 17 dawn frames he had rejected by hand).
    Rejected frames go on the Clean-up screen as "rejected for quality", called out, deleted only with approval.
18. **Previews the astro-stacker way:** rendered from the FITS with stacker's stretch (lights unlinked + debayer,
    flats `calibration`, darks/bias `noise`), 320 px cards and a 1600 px lightbox. The ASIAIR's `_thn.jpg` is
    not used for display. `core/imaging.py` and `core/quality.py` are ports of stacker's `app/imaging.py` and
    `app/framestats.py` (commit f31cbcb): keep them in step with stacker.

## Terminology
We **ingest** capture data onto the NAS; we don't "archive" it. Say ingest / ingested / already-ingested, "the NAS",
"the Astronomy share", in docs, UI, status names and code (`NasIndex`, `ASTRO_NAS`).

## Environment (dev container on UnRAID `FractalR5Tower`)
| Container path | Host path | Mode | Purpose |
|---|---|---|---|
| `/workspace` | `/mnt/user/docker_appdata/astro-ingest-dev/src` | rw | this repo |
| `/astro` | `/mnt/user/Astronomy` | **read-only** | the live Astronomy share: read for lookups only |
| `/astro-sandbox` | `/mnt/user/astro-sandbox` | rw | test copy of part of the share. **All dev writes go here** |
| `/asiair` | `/mnt/remotes/ASIAIR` | read-only | ASIAIR SMB share (may be offline; the app must cope) |
| `/home/dev` | `/mnt/user/docker_appdata/astro-ingest-dev/home` | rw | persisted home (Claude Code, ssh keys, git config) |

Web port: container `8000` → host `8090`. Config comes from env vars (see `.env.example`; `dev/run-dev.sh` passes
the repo's `.env`): `ASTRO_ROOT` (write target), `ASTRO_NAS` (old name `ASTRO_ARCHIVE` still accepted), `STATE_DIR`, `TZ`, `ASIAIR_ROOT`, `ASIAIR_SUBNET`,
`ASIAIR_SHARE`, `ASIAIR_HOST`, `CACHE_DIR` (disposable renders; default `STATE_DIR/cache`). **Never hard-code paths.** Python deps live in `/workspace/.venv`
(`python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`); run tests with `.venv/bin/pytest`.
To see the UI, run `.venv/bin/astro-ingest serve` and screenshot it with headless Chromium:
`.venv/bin/python dev/screenshot.py http://localhost:8000/ /tmp/shot.png [--click "Review"] [--full]`, then view the
PNG with the Read tool (the script also prints browser console errors).

## Hard rules
- In dev, **write only under `ASTRO_ROOT=/astro-sandbox`**. `/astro` is mounted read-only; don't try to work around that.
- Never delete inside the share; retire to `_to_delete/`. Never overwrite; skip and report clashes.
  Verify counts and sizes/checksums around every copy or move, and log every operation.
- Deleting from the **source** is allowed only after checksum-verified copies **and** Chris's approval in the UI.
- Don't follow symlinks when walking; skip `100-`…`103-` index folders, `0*` and `Z*` folders, and dotfiles.
- Never create `.DS_Store`, `desktop.ini`, or `._*` files. Write atomically (`*.part` then rename).
- Test fixtures must be tiny, generated FITS files (`tests/fitsgen.py`). Never commit real subs or thumbnails
  (`.gitignore` blocks `*.fit*`, `*.xisf`, `*.jpg`, `*.jpeg`).

## Stack
Python 3.12, FastAPI JSON API + static vanilla-JS frontend (decision 12), `smbprotocol` for the ASIAIR,
numpy/astropy/photutils/Pillow for previews and frame scoring (no Siril), SQLite in
`STATE_DIR` (rollback journal, not WAL, on the shfs mount), a port of `reference/scripts/fitshdr.py` for headers,
pytest. Keep filing logic in the pure, well-tested `astro_ingest/core/` library, separate from the web layer, so it
can also run from the CLI (`astro-ingest …`). The architecture and phase plan live in `docs/PLAN.md`.

## Working style
- Use plan mode for anything touching filing rules; propose, then implement in small, reviewable commits.
- Reuse the logic in `reference/scripts/` (night splitting, dark matching, PROJECT_INFO, index links, 10-frame
  calibration cap) instead of reinventing it; port it into the package with tests.
- Commit early and often with clear messages. Don't push secrets. Ask Chris before adding new top-level folders to the share.

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
5. Only the ASIAIR's **`EMMC Images`** share. The app **finds the ASIAIR on the user's home network** and talks SMB to
   it directly (guest). The network is detected (2026-09-27: never hard-code it): the browser's address (the app's
   container only sees Docker's bridge), then the server's own networks, then recent devices; otherwise the page
   asks. `ASIAIR_SUBNET` is only an override. `ASIAIR_ROOT` can point at a local directory instead (dev: `/astro-sandbox/_asiair-sample`).
   **Never rely on the `/asiair` host mount** to find or reach the device (`astro-ingest find` does discovery).
   - **Several devices on the network: Chris picks one.** The choice is remembered in `STATE_DIR/devices.json` by
     **address + a name he gives it**, plus up to 10 **recent devices** (Connect, Rename, Forget) and a `slug` fixed at
     first connect that names its staging folder and Verify results, so a rename moves nothing (every ASIAIR reports the same identity: SMB server id spells "asiair",
     NetBIOS "ASIAIR", MAC hidden), so each ASIAIR should have a DHCP reservation. If the remembered address stops
     answering and another device appears, **ask; never switch devices silently.**
   - **Seestar support comes later:** devices are recognized by share *and* top-level folders
     (`sources/devices.py` KINDS), since the Seestar's share is also called "EMMC Images".
6. Frames already ingested onto the NAS: compare with the NAS copy, then offer them for source cleanup. Never re-copy.
   **Quick check by default** (2026-09-27): size + the first 16 KB (FITS header) + 8 slices of 64 KB, hashed on both
   sides (`fsops.quick_digest`, ~0.5 MB per frame, ~1 min for 520 frames); **Thorough** reads every byte (BLAKE2b).
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
    Answers to plan decisions, per-frame keep/reject/exclude choices and the sensitivity live in
    `STATE_DIR/answers.json` (kept as JSON on purpose: readable by Claude sessions).
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
19. **Select → Stage (phase 4a).** Reads over the ASIAIR's Wi-Fi run ~9–10 MB/s (it stays on Wi-Fi), so each new
    frame is **read from the device exactly once**, into `STAGING_DIR` (its own UnRAID share,
    `/mnt/user/astro-ingest-staging` mounted at `/staging`, outside the Astronomy share), checksummed (BLAKE2b) on the way in and
    re-verified on disk; scoring, previews and filing work from the staged copy (`staging.StagedSource`).
    Before that, the **Select step** shows a plain chronological grid (left→right, top→bottom) of the device's own
    thumbnails per set; Chris can leave out frames or whole sets, with a live rough Wi-Fi time estimate
    (assumed 10 MB/s until a staging run measures the real rate).
20. **Left out on Select** → never read; offered for deletion on Clean-up, called out ("left out by you").
21. **Copy & verify (phase 4b):** one approval copies everything that's ready (staged, no open decision); the batch
    is a snapshot of exact operations in `STATE_DIR/ingest.sqlite3` plus `batches/<id>.tsv` and `logs/copy-<id>.log`.
    `fsops.py` is the only writer to the share: `.part` → fsync → hash must match the staging hash → no-clobber
    rename (hard link); identical file already there = fine, anything else = clash, never overwritten; damaged
    copies retired to `_to_delete/`; single writer via `STATE_DIR/ingest.lock`. Staged copies are cleared as soon
    as their NAS copies verify (rejected/left-out ones when the batch ends); scores and "already read" survive that.
22. **Catalog step (phase 5)** after Copy & verify: PROJECT_INFO, new `targets.csv` rows, index links +
    `ZZ_TARGET_INDEX.md`, sibling-night notes (only added), `.flats_are_copies`, calibration-gap report,
    decision-log drafts (in `STATE_DIR`, not written into the guide). Preview first, one approval.
23. **Cross-night flats:** offered only when target, camera, scope and filter match, nights ≤ 7 days apart, and
    rotation matches mod 180° within ±3° (never the unsolved 79°). **Default: don't borrow**; a bad flat is
    worse than no flat.
24. **Clean up (phase 6)** deletes from the device only what passes every gate: the frame is proven on the NAS by
    BLAKE2b (copied by the app, or checked by the **Verify** job, which reads older frames from the device once);
    Chris ticked it (verified frames ticked by default, callouts unticked, blocked/never not tickable); the device
    is re-listed right before (size + mtime unchanged, nothing written in the last 15 min) and each NAS copy is
    re-hashed; deletes only under `Autorun/`/`Plan/`, `.fit` then `_thn.jpg`, empty object folders removed; the
    device is listed again after and must differ by exactly the deleted files. **`ALLOW_DEVICE_DELETE=1` is
    required for a real device over SMB; it stays off until Chris confirms his ASIAIR and NAS backups are done.**

25. **UI review round 1 (2026-09-27; proposals page https://claude.ai/artifact/RoWP7YarUqbrodWycWEZqi).** Five button kinds
    (main = one filled `primary` per screen, secondary, `.seg` choice, `danger`, `next`); **Next lives in the step bar
    pinned to the bottom** (outlined until the step's action is done, then filled; disabled with a reason). One
    **device pill** in the header (no separate "online" badges). Scan Images groups Autorun/Plan (with "n of m new
    selected") apart from the other folders. **Clean up**: sections Ready to delete / Needs a check first / Not on the
    NAS / Stays on the device; **nothing pre-ticked** ("recommended" + Select recommended); delete runs in four stages
    (list, check every gate, delete, list again); a Done panel stays until dismissed. Previews never read a whole
    frame over Wi-Fi (staged copy, NAS copy, or the device's thumbnail). UI changes that need Chris's decision go on
    a lettered proposals page with mockups first (his standard).

26. **UI review round 2 (2026-09-27).** Connect: recent devices, "Search the network" (disconnects: a new session;
    never picks for you, even when there's one device), Advanced (one address, or another network). Review has no
    clean-up preview; rejected frames stay staged until Copy. **Start over** (header, and beside OK after a
    clean-up) clears staged-not-copied frames, previews, device-frame scores, Select/Review choices, **decision
    answers by default** (a tick keeps them), the session and the connection; it keeps the NAS, logs, recent devices,
    Verify results, σ and the checksums proving copies. **The session** (step, checkmarks, ticks, collapsed decisions)
    lives in `STATE_DIR/session.json`: any window or reload returns to it, and open windows follow each other.

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
| `/staging` | `/mnt/user/astro-ingest-staging` | rw | `STAGING_DIR`: frames read once from the device, until filed |

Web port: container `8000` → host `8090`. Config comes from env vars (see `.env.example`; `dev/run-dev.sh` passes
the repo's `.env`): `ASTRO_ROOT` (write target), `ASTRO_NAS` (old name `ASTRO_ARCHIVE` still accepted), `STATE_DIR`, `TZ`, `ASIAIR_ROOT`, `ASIAIR_SUBNET`,
`ASIAIR_SHARE`, `ASIAIR_HOST`, `CACHE_DIR` (disposable renders; default `STATE_DIR/cache`), `STAGING_DIR`,
`ASSUMED_WIFI_MB_S`, `ALLOW_DEVICE_DELETE` (`ASIAIR_SUBNET` only overrides the detected network). **Never hard-code paths.** Python deps live in `/workspace/.venv`
(`python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`); run tests with `.venv/bin/pytest`. The SMB clean-up test
needs a throwaway SMB server from its own venv (`python3 -m venv .venv-smbtest && .venv-smbtest/bin/pip install
impacket`); **never install impacket into `.venv`** (its `smbclient.py` script shadows the `smbclient` package).
For a full-size clean-up test, point `ASIAIR_ROOT` at `/astro-sandbox/_asiair-scratch` (a `cp -al` hard-link copy of
the sample; recreate it the same way).
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

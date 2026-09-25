# Astronomy Volume: Organization Guide (for future Claude / Cowork sessions)

Read this before touching anything on this volume. It records the conventions and the decisions Chris has made.
Last updated: 2026-09-23.

## 1. Ground rules (non-negotiable)

1. **Never delete anything.** When something should go, move it into a `_to_delete/` folder *in the same parent folder* it came from, so it's easy to revert. Chris empties these himself.
2. **Confirm every action with Chris first, in small groups** (move, rename, "delete" via `_to_delete`). Show a table of exactly what will change, then wait for approval.
3. **Confirm every rename individually or as a short list** before doing it, even when it's derived from FITS headers.
4. Use no-clobber moves (`mv -n`), and count files before and after each move.
5. Reading FITS/XISF headers is allowed and encouraged.
6. Creating new info files (e.g. `PROJECT_INFO.txt`) doesn't need approval. Changing or moving existing data does.

## 2. Top-level layout

| Folder | Purpose |
|---|---|
| `000-FinalizedImages` … `013-…` | Special-purpose folders. **Ignore** for organization work unless told otherwise. |
| `001-MasterBias` | Bias library: `001-MasterBias/<Camera>/<YYYY-MM-DD>/` |
| `002-MasterDarks` | Dark library: `002-MasterDarks/<Camera>/<N> Seconds/<YYYY-MM-DD>/` |
| `100-`/`101-`/`102-By…Number` | Catalog index symlinks (see §2c) |
| `Z96`–`Z99` | Training, hardware and tools. **Ignore**. |
| everything else | One folder per **target** (see §3) |
| `ZZ_EQUIPMENT.md` | Everything known about the gear (Chris's reference) |
| `ZZ_IMAGING_TODO.md` | Calibration frames and captures still needed (Chris's to-do) |
| `Z95-ClaudeReferences/` | **Start here, Claude.** This guide plus `scripts/`. Everything Claude-specific lives here |

### Calibration library rules
- Only **darks and biases** from **ZWO cameras** go in the libraries. Cameras: `ASI2600MM Pro`, `ASI2600MC Pro`, `ASI294MC Pro`, `ASI183MM Pro (PANE)`.
  - `ASI2600MC Pro` is the folder name even though headers say "ASI2600MC Duo". Don't add "Duo".
  - `ASI183MM Pro (PANE)` keeps "(PANE)" because the camera was borrowed from PANE.
- Dark exposure folders are named `<N> Seconds` with a capital S, e.g. `300 Seconds`.
- The date folder is the **local calendar date the calibration set started** (from the filename), `YYYY-MM-DD`. Calibration is often shot in the daytime, so the lights' evening-date rule doesn't apply.
- **Temperature suffix:** add it only when the set isn't at the standard -10 °C, e.g. `2023-04-19 (+14C)` for uncooled or `(-20C)`. Always verify it against the FITS `CCD-TEMP`, never a folder label.
- **Dark flats are NOT darks.** They stay in their session as `darkflats` (294MC/183MM only; see §4). A "Dark" folder whose frames are under about 10 s and match the flat exposures is a dark-flat folder.
- When calibration frames are found duplicated across sessions: move **one** copy to the library, and move every other copy to its session's `_to_delete/`.

## 2b. Calibration frame cap
- For the **cooled ZWO cameras**, every calibration **set** (one exposure + filter within a folder: darks, biases, flats, dark flats) keeps **at most 10 frames**. **DSLR (450D/CR2) calibration is exempt: keep all frames**, since uncooled sensors benefit from more: the first 10 in capture order. Extras go to a `_to_delete/` inside that folder. Tool: `scripts/trim_calibration.py [--apply]` (dry run by default).

## 2c. Catalog index folders
- `100-ByMessierNumber/`, `101-ByNGCNumber/`, `102-ByICNumber/` contain **relative symlinks** (`M031-AndromedaGalaxy -> ../AndromedaGalaxy-M31`), zero-padded so they sort (M = 3 digits, NGC/IC = 4). A target appears under every catalog number it has (e.g. M31 also as NGC0224).
- The single source of truth is `Z95-ClaudeReferences/targets.csv`. After adding or renaming a target, update the CSV and run `scripts/make_index_links.py`, which also rewrites `ZZ_TARGET_INDEX.md` at the root (a plain-text fallback that works everywhere).
- Relative links resolve server-side on UnRAID, so they work over NFS (macOS) and SMB (Windows; Samba follows in-share links by default). Never create absolute `/Volumes/...` links.

- `103-ByDate/` holds one relative symlink **per session (night)**, named exactly like the session folder (`2026-09-13-ElephantTrunkNebula-2600MC-Z61 -> ../ElephantTrunkNebula-IC1396/2026-09-13-…`), so it lists every night in date order. It's built by the same `make_index_links.py`; rerun it after adding, splitting or renaming sessions.

## 3. Target folders

`HumanReadableName-<catalog>`, with no spaces.
- Catalog priority: **Messier number if it exists; otherwise NGC; otherwise IC**. Other catalogs (Sh2, Barnard) only when none of those apply.
- Multi-object targets (e.g. Heart + Soul together) are decided case by case with Chris.
- Examples: `AndromedaGalaxy-M31`, `BubbleNebula-NGC7635`, `HeartNebula-IC1805`.

## 4. Session folders (inside a target)

`YYYY-MM-DD-TargetName-[Mosaic]-Camera-Telescope`
- **One session = one night.** The date is the local evening date the night began.
- **Multi-night data is split into separate dated sessions.** When flats were only shot on one night, copy the flats and darkflats whose filters match each night's lights into that night's session. A hidden `.flats_are_copies` marker records the source. Chris wants every night self-contained, and duplication is fine.
- Each split session's `PROJECT_INFO.txt` notes the sibling night(s) ("night 1 of 2; night 2 is `…`"). Chris decides each case individually.
- Optional token between the name and the camera: `Mosaic`. **No `Mono` token**: an `MM` camera already implies mono/narrowband. Location is **not** in the name; it's recorded in PROJECT_INFO.txt / `.project_notes.txt`.
- Camera tokens: `450D`, `294MC`, `183MM`, `2600MM`, `2600MC`, `SeestarS50`
- Telescope tokens: `Z61` (also written `WO61` in older folders; same scope), `RC6`, `SV503`, `FMA135`
- Sub-folders inside a session are **lowercase**: `lights`, `flats`, `darkflats`, plus `stacked` for outputs. Exposure-split lights use `lights-<N>s` (e.g. `lights-120s`, `lights-586s-SharpCap`). **Nothing loose in a session root** except PROJECT_INFO.txt, `.project_notes.txt` and CR2/XMP files in DSLR sessions. Chris's free-text notes go in `.project_notes.txt`, which is appended to PROJECT_INFO.txt as NOTES. This matches ASIAIR's output. Filter-split folders use a suffix, e.g. `lights-Ha`, `flats-OIII`.
- **Dark flats:** keep them (as `darkflats`) for the **ASI294MC Pro and ASI183MM Pro (PANE)**. For the low-noise **ASI2600MM / ASI2600MC** they are unnecessary: move any to `_to_delete/` and calibrate flats with the master bias instead.
- **DSLR (.CR2) sessions** predate the ZWO cameras. Keep lights, darks, flats, bias and offset **all together in the session**; they are not moved to the libraries.

## 5. Finished images

- A final stacked/processed **.fits** goes in the **target folder root** as `YYYY-MM-TargetName-Camera.fits`, e.g. `ElephantTrunkNebula-IC1396/2026-09-ElephantTrunkNebula-2600MC.fits`. Use the month of capture; for multi-night stacks, the month of the first night.
- The matching **.jpeg/.png** goes in `000-FinalizedImages/` with the same base name.

## 6. Processing leftovers

- **Raw data is sacred; processing output is disposable.** Chris restacks and reprocesses from raw with new techniques, so old PixInsight/WBPP/Siril/AutoIntegrate folders and `.pxiproject` bundles go to `_to_delete/`.
- **`stacked/` rule:** before a processing folder is retired, move its keepers into a `stacked/` subfolder of the session. Keepers are stacked linear masters (`masterLight*.xisf/.fit`, not the `registered/` copies), Siril `result_*.fit` plus starless and starmask versions, and finished TIFs from `Working*` folders.
- DeepSkyStacker (`DSS Stacked` etc.): keep the stacked `.FTS`/`.TIF` and loose `Autosave*.fits` in `stacked/`; the rest goes to `_to_delete`.
- `TRASH`/`BadFrames` (rejected lights) go to `_to_delete`.
- Photoshop `.psd` layer files are kept in `stacked/` (Chris wants to review them).
- CR2 sessions: DSS master TIFs, `.Info.txt`/`.Description.txt` sidecars and logs go to `_to_delete`. TIF conversions of CR2 frames are duplicates and go to `_to_delete`. The CR2s always stay.
- Before retiring, run `scripts/rawcheck.py`. It confirms no raw capture exists **only** inside the folder being retired.

- **Siril artifacts** (`process/`, `masters/`, Siril-prepared `Session N/` symlink trees, `cache`, `drizztmp`) can generally be moved to `_to_delete/` without reviewing their contents.
- **Keep final results** (`result_*.fit`, `starless_*`, `starmask_*`, the final `.fits`/`.jpeg`) in the session.
- PixInsight / WBPP / DSS folders: not decided yet. Ask Chris.

## 7. PROJECT_INFO.txt (one per session)

Generated from FITS/XISF headers. Contents: target and coordinates (mount pointing plus plate-solved center), camera, telescope, focal length and image scale, mount, capture software, approximate site (city), capture nights, a lights table (filter, exposure, gain, offset, temperature, count) with total integration, flats and dark flats, and **the matching master dark and bias folders** (marked `USE ->`, with alternates).
- CR2 (450D) sessions get their PROJECT_INFO.txt from `scripts/cr2info.py <session> "<location>"`, which reads EXIF via PIL. Their capture location is in `.project_notes.txt`.
- Free-text notes for a session go in a hidden `.project_notes.txt` in the session. The generator appends them to PROJECT_INFO.txt as a NOTES section, so they survive regeneration.
- Capture site is looked up from SITELAT/SITELONG against the `SITES` table in `scripts/projinfo.py`. Add new named sites there (e.g. `MHAA Star Party` = Lake Taghkanic State Park parking lot).
- Header caveats: on ASIAIR, `TELESCOP` holds the **mount** name. The telescope comes from the folder token or is inferred from focal length (see ZZ_EQUIPMENT.md).

## 8. Scripts (`Z95-ClaudeReferences/scripts/`)

Run them on Chris's machine with the volume mounted. They find the volume root automatically (two levels up), or you can set `ASTRO_ROOT`.
| Script | Use |
|---|---|
| `fitshdr.py` | Minimal FITS + XISF header reader (no astropy needed) |
| `projinfo.py [--reindex] [-q] <Target/Session> …` | Writes PROJECT_INFO.txt. **Run `--reindex` after any change to 001/002** |
| `batch.sh [--reset]` | Regenerates every session in ~140 s chunks. Re-run until it prints `ALLDONE` |
| `calneeds.py` | Lists missing or mismatched darks and biases across all sessions (feeds ZZ_IMAGING_TODO.md) |
| `survey.py` | One-line header summary per session (equipment survey) |

## 9. Decision log

| Date | Decision |
|---|---|
| 2026-09-23 | Camera folder names fixed as in §2. No "Duo"; keep "(PANE)". |
| 2026-09-23 | Nothing is deleted directly. Everything goes to a local `_to_delete/`. |
| 2026-09-23 | Six mislabeled `Dark` folders (short exposures) are dark flats. To be renamed `DARKFLAT`. |
| 2026-09-23 | ASI294MC Pro library to be normalized to `<N> Seconds/<YYYY-MM-DD>/`. |
| 2026-09-23 | Catalog priority for target names: M, then NGC, then IC. |
| 2026-09-23 | Multi-night sessions are split into one folder per night, and the info files cross-reference each other. |
| 2026-09-23 | Elephant Trunk 2026-09-13: night 1 used a flat panel and night 2 used sky flats, so the flat exposures differ on purpose. Chris normally uses the panel. |
| 2026-09-23 | Group 1 done: ASI2600MC 2026-08-29 300s darks moved to the library; duplicate darks and biases moved to `_to_delete`. |
| 2026-09-23 | Group 2 done: ASI2600MC 2025-04-14 bias and 300s darks moved to the library; duplicates moved to `_to_delete`. |
| 2026-09-23 | Group 3 done: Elephant Trunk 2026 split into `2026-09-13-…-2600MC-Z61` (night 1, panel flats) and `2026-09-14-…-2600MC-Z61` (night 2, sky flats). Siril Session 1/2 and the log moved to `_to_delete`. |
| 2026-09-23 | Final-image naming standard set (§5). Chris applied it to Elephant Trunk 2026. |
| 2026-09-23 | Site naming: 42.09/-73.72 = "MHAA Star Party" (Lake Taghkanic SP). Ghost 2022-09-09 coordinates are a GPS error (it was home). M8/M17 2026-08-28 were at West Point. |
| 2026-09-23 | Claude references (this guide and scripts) live in `Z95-ClaudeReferences/`. `ZZ_EQUIPMENT.md` and `ZZ_IMAGING_TODO.md` stay at the root for Chris. |
| 2026-09-23 | Group 4 done: North America 2024-09-02 per-filter duplicate darks and empty biases moved to `_to_delete`. Elephant Trunk 2026 `Untitled_Project_final.fit` and `run_project.ssf` moved to `_to_delete`. |
| 2026-09-23 | Dark-flat policy: keep for 294MC/183MM; `_to_delete` for 2600MM/2600MC (low noise; use bias). |
| 2026-09-23 | Group 5 done: six 183MM `Dark`/`Dark Flat` folders renamed to `darkflats`. Five 2600MM dark-flat folders moved to `_to_delete/darkflats`. |
| 2026-09-23 | Subfolder standard: lowercase `lights` / `flats` / `darkflats`. The rename plan is in `plans/group6_lowercase_subfolders.tsv`. |
| 2026-09-23 | Group 7a done: 2024–2026 Siril leftovers retired and results moved to `stacked/`. |
| 2026-09-23 | Chris: all PixInsight/Siril processing folders may be retired, since he will restack from raw. The `stacked/` rule was adopted. |
| 2026-09-23 | Group 7b done: 48 PixInsight/WBPP/AutoIntegrate/.pxiproject/Working/CombinedNights folders moved to session `_to_delete/`, and 106 masterLight/TIF keepers moved to `stacked/`. rawcheck.py found no raw-only files. |
| 2026-09-23 | Group 7c done: M101 2023-05-99 WBPP folder retired (combined masters moved to 2023-05-23 `stacked/`). 17 DSS folders retired with stacks kept. 12 TRASH/BadFrames folders retired. |
| 2026-09-23 | Maine Heart and North America 2020 `TIFs/` (TIF conversions of the CR2s) moved to `_to_delete`. All CR2s remain. |
| 2026-09-23 | Group 8 done: 450D/Maine CR2 sessions cleaned up. Stacks and PSDs moved to `stacked/`; WBPP `Processing/`, DSS masters and sidecars moved to `_to_delete`. |
| 2026-09-23 | SV503 confirmed as the scope for the 2023 ASI183MM sessions at 568–569 mm. |
| 2026-09-23 | Group 9: split M101 2022-06-03/04, M31 2021-09 (3 nights), M51 2023-05 (2), Wizard 2023-10/11 (2), M45 mosaic 2023-11 (2), M27 2023-08 (4) and Ghost 2022-09 (4). Filter labels dropped from session names. Flats copied to sibling nights by filter. |
| 2026-09-23 | Ghost 2022-09-09: darkflat files in `darkflats-Ha` and `darkflats-OIII` renamed from `FlatWizard_SII` to match their folder. All three are the same 30 s frames, and flats are 30 s for every filter. Checked Wizard and all other dark-flat folders: no similar mismatch. |
| 2026-09-23 | Group 10 target renames: M31, M81 (BodesGalaxy), M16, M42, M33 and TigersEye have no NGC suffix or special characters. Soul gets IC1848, Horsehead IC434, Rosette NGC2237, Veil becomes one folder `VeilNebula-NGC6960-NGC6992`. New target `HeartAndSoulNebulae-IC1805-IC1848` holds the 2025-10-16 FMA135 wide field. |
| 2026-09-23 | Chris copied M27 flats and darkflats to 08-09/11/13 (verified complete). The M45 11-02 flats copy failed (0 of 40) and must be redone. |
| 2026-09-23 | Pleiades 11-02 flats re-copied by Chris; verified 40/40 flats and 40/40 darkflats. `.flats_are_copies` markers added to the M27 and M45 copies. |
| 2026-09-23 | Group 11: 86 session folders renamed to `YYYY-MM-DD-TargetName-[Mosaic]-[Mono]-Camera-Telescope`. Dates use the **evening** of first light (six folders shifted one day). 2020-09-06 294MC sessions are Z61 (the only scope then). 450D sessions dropped Home/Maine from their names; location moved to notes and PROJECT_INFO. |
| 2026-09-23 | Veil 2020-11-06 panorama (TIFs made from the East and West sessions) moved into `2020-11-06-EasternVeil-294MC-Z61/stacked/VeilPanorama-2020-11-06/`. |
| 2026-09-23 | Group 12: ASI294MC dark library rebuilt as `<N> Seconds/<date>[ (temp)]/`. DSS MasterDark TIFs and emptied Archive/(-10C)/(-20C) folders moved to `_to_delete`. The old "(-20C)" label was wrong: its sets were +14 °C (2023) and -10 °C (2021, per headers). ASI183MM folders changed to "Seconds"; 2023-08-16 sets redated 2023-08-15. |
| 2026-09-23 | 2023 M101 (05-21..26) and M51 lights were shot at -20 °C and have no matching darks, so a reshoot was added to IMAGING_TODO. projinfo.py now penalizes darks more than 5 °C from the lights and flags `!! TEMPERATURE MISMATCH`. |
| 2026-09-23 | 3 "Pliedes" CR2 frames moved from the Heart 450D session into the new `Pleiades-M45/2020-08-27-Pleiades-450D`. |
| 2026-09-23 | ZZ_EQUIPMENT.md rebuilt from Chris's equipment notes plus header data: back-focus trains, image scales and FOV, filters, visual kit. Open questions are listed in its §12. Previous version saved in Z95-ClaudeReferences/_to_delete/. |
| 2026-09-24 | All `_to_delete` folders deleted (Chris approved after a spot-check): ≈ 1.44 TB freed. Three shells (Pacman 2023-09-19, M57 2023-08-31, M51 2023-05-11) are held by `.nfs` placeholders and must be removed once the share releases them. |
| 2026-09-24 | Group 13 tidy: about 1,650 loose root lights moved into `lights/`; exposure folders became `lights-<N>s`; North America 2024-09-02 flattened to `lights-H/O/S/L` and `flats-H/O/S/L`; Seestar/WBPP/DSS outputs moved to `stacked/`; DSS `.Info.txt`/`.stackinfo.txt` sidecars, `apt_thumbs`, ASIAIR `LOGS` and empty folders moved to `_to_delete`. M17 and M8 2026 finals renamed `2026-08-<Target>-2600MC.fits` (JPEGs in 000-FinalizedImages). batch.sh now skips `_to_delete` and `stacked`. |
| 2026-09-24 | Chris's `Notes.txt` (8) and `000-Capture Notes.txt` (1) merged into `.project_notes.txt` and PROJECT_INFO NOTES (multi-night notes copied to every sibling night); originals removed after verification. Group 13's `_to_delete` contents deleted; 22 shells held by `.nfs` locks need an NFS restart. |
| 2026-09-24 | Removed 492 `.DS_Store`/`desktop.ini` files; Spotlight indexing disabled on the share by Chris. |
| 2026-09-24 | `Mono` dropped from the session naming rule; 37 sessions renamed and sibling references in notes updated. |
| 2026-09-24 | Calibration cap of 10 frames per set applied: 132 sets trimmed, 1,683 frames to `_to_delete` inside each calibration folder. |
| 2026-09-24 | Catalog index symlink folders built (67 relative links) from targets.csv; ZZ_TARGET_INDEX.md added. SV503 image circle: APS-C per SVBony (no published figure). |
| 2026-09-24 | ClaudeHandoff.md written for the capture-ingest web app (Claude Code). |
| 2026-09-24 | 10-frame cap limited to cooled cameras; 148 450D CR2 calibration frames restored to their sessions. |
| 2026-09-24 | Root markdown files renamed with a `ZZ_` prefix so they sort last: ZZ_EQUIPMENT.md, ZZ_IMAGING_TODO.md, ZZ_CLEANUP_CHECKLIST.md, ZZ_TARGET_INDEX.md. Any new root doc should follow this. |
| 2026-09-25 | Added `103-ByDate/`: 115 relative session links in date order, generated by make_index_links.py. |
| 2026-09-25 | Starter kit for the ingest app staged at `Z95-ClaudeReferences/astro-ingest-scaffold/` (CLAUDE.md, docs, dev Dockerfile and run script, reference scripts, SETUP.md). Repo planned: cfmorrell/astro-ingest; dev container astro-ingest-dev on port 8090 with the archive mounted read-only and writes going to /mnt/user/astro-sandbox. |

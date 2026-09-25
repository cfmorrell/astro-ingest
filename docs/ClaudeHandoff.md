# ClaudeHandoff: Astrophotography Ingest Web App

**Audience:** Claude Code, building a web app that pulls data from Chris's capture platforms and files it into the Astronomy share according to the established rules.
**Written:** 2026-09-24 by Claude (Cowork), at the end of a two-day reorganization of the share.
**Authority:** if this file and `Z95-ClaudeReferences/ORGANIZATION_GUIDE.md` disagree, the guide wins. Read the guide too; it has the full decision log.

---

## 0. Read these first
| File (relative to the share root) | What it is |
|---|---|
| `Z95-ClaudeReferences/ORGANIZATION_GUIDE.md` | All naming and layout rules plus the decision log. **Canonical.** |
| `Z95-ClaudeReferences/targets.csv` | Target catalog: folder name ↔ Messier/NGC/IC/other IDs. Drives the index links. |
| `Z95-ClaudeReferences/scripts/` | Working Python reference implementations (see §10). Reuse the logic; don't reinvent it. |
| `ZZ_EQUIPMENT.md` | Cameras, scopes, back focus, filters, mounts, sites. Needed for telescope inference. |
| `ZZ_IMAGING_TODO.md` | Calibration gaps still to be shot. The app can update it (§7.6). |
| `ZZ_TARGET_INDEX.md` | Generated table of targets and catalog IDs. |

---

## 1. Environment and where the app should run
- **Storage:** UnRAID server `FractalR5Tower`. Share path on the server is **`/mnt/user/Astronomy`**. The web UI is at `https://unraid.cfmorrell.com`; share-browser links look like `https://unraid.cfmorrell.com/Shares/Browse?dir=/mnt%2Fuser%2FAstronomy%2F<url-encoded path>`.
- Clients: macOS mounts the share over **NFS** (`/Volumes/Astronomy`), and Windows uses **SMB**.
- **Run the app on the server, as an UnRAID Docker container** with `/mnt/user/Astronomy` bind-mounted. Don't write through a Mac NFS mount. Lessons learned the hard way:
  - Writes through a client mount were about **1 MB/s**, versus about 20 MB/s reads. Copying a few GB of flats took hours.
  - Deleting or overwriting files over NFS while anything holds them open leaves **`.nfs.XXXX` silly-rename files**. UnRAID's `shfs` then holds them, and nothing can delete them until `/etc/rc.d/rc.nfsd restart`. Server-side file operations avoid all of this.
- Chris has turned Spotlight indexing **off** for the share. Never create `.DS_Store`, `desktop.ini`, `Thumbs.db` or AppleDouble `._*` files; ignore them if present.
- Chris is also building a Dockerized Siril stacking tool on the same server. Keep the ingest app's output compatible with it: plain FITS in `lights/`, `flats/`, `darkflats/` and the calibration libraries.

---

## 2. Top-level layout (do not change without asking Chris)
```
/mnt/user/Astronomy/
├── 000-FinalizedImages/        finished JPEG/PNG exports:  YYYY-MM-TargetName-Camera.jpeg
├── 001-MasterBias/<Camera>/<YYYY-MM-DD>[ (temp)]/
├── 002-MasterDarks/<Camera>/<N> Seconds/<YYYY-MM-DD>[ (temp)]/
├── 003…013-*/                  special-purpose folders; IGNORE
├── 100-ByMessierNumber/        relative symlinks  M031-AndromedaGalaxy -> ../AndromedaGalaxy-M31
├── 101-ByNGCNumber/            relative symlinks  NGC0224-AndromedaGalaxy -> ../AndromedaGalaxy-M31
├── 102-ByICNumber/             relative symlinks  IC1805-HeartNebula -> ../HeartNebula-IC1805
├── 103-ByDate/                 relative symlinks  <session> -> ../<Target>/<session>  (chronological)
├── <TargetName>-<Catalog>/     one folder per target (see §3)
│   ├── YYYY-MM-TargetName-Camera.fits      final processed image (optional)
│   └── <session>/              one folder per night (see §4)
├── Z95-ClaudeReferences/       Claude docs, targets.csv, scripts, plans/logs
├── Z96…Z99-*/                  IGNORE
├── ZZ_EQUIPMENT.md  ZZ_IMAGING_TODO.md  ZZ_TARGET_INDEX.md  ZZ_CLEANUP_CHECKLIST.md
```
**Enumerating targets:** a target folder is any top-level directory whose name starts with a letter A–Y and isn't a symlink. Skip `0*`, `1*` (the `100-`–`103-` index folders, which contain symlinks that would double-count), `Z*`, and dotfiles. **Never recurse into `100-`–`103-`**: every entry there is a symlink back into the targets.

---

## 3. Target folders
- Format: `HumanReadableName-<Catalog>`, CamelCase, no spaces, and **no apostrophes, `&`, parentheses or other special characters** (e.g. `BodesGalaxy-M81`, `TigersEyeGalaxy-NGC2841`).
- **Catalog priority:** Messier if one exists, else NGC, else IC; else another catalog (Sh2, Barnard), or none (e.g. `MarkariansChain`).
- Multi-object fields get their own target, e.g. `HeartAndSoulNebulae-IC1805-IC1848`, `VeilNebula-NGC6960-NGC6992`. **Ask Chris** before creating a new multi-object target.
- A new target means: create the folder, **add a row to `targets.csv`** (all known M/NGC/IC/other IDs, `;`-separated), run `make_index_links.py`.
- Map capture-software object names to targets through `targets.csv`. Headers use variants such as `M 8`, `M8`, `NGC7000`, `NGC 7000`, `Seven Sisters`, `Great Orion Nebula`, `HeartNebula`, `Sadr`, `ElephantTrunk`. Normalize (strip spaces, uppercase catalog prefixes) and match on catalog ID first, then name. If there's no confident match, **queue for Chris**; never guess.

---

## 4. Session folders (one per night)
**Name:** `YYYY-MM-DD-TargetName-[Mosaic]-Camera-Telescope`
- **Date = the local evening the night began.** Compute it from the first light frame's *local* timestamp minus 12 hours. ASIAIR and N.I.N.A. filenames are in local time. FITS `DATE-OBS` is UTC, so subtract 16 h in EDT / 17 h in EST, or better, convert with the site time zone (America/New_York) and then subtract 12 h.
- **One night = one session.** If an import spans several nights, split it into one session per night (see §7.4).
- `TargetName` = the target folder's name part, e.g. `AndromedaGalaxy`.
- `Mosaic` only for mosaics. **There is no `Mono` token**, since an `MM` camera already implies mono/narrowband. Filter sets (SHO, RGB) are **not** in the name.
- **Camera tokens:** `2600MM`, `2600MC`, `294MC`, `183MM` (borrowed from PANE, returned), `450D` (Canon DSLR), `SeestarS50`.
- **Telescope tokens:** `Z61` (WO ZenithStar 61), `RC6` (iOptron RC6), `SV503` (SVBony 102 ED), `FMA135` (Askar). Seestar and 450D sessions have no telescope token.
- **Telescope inference:** the `TELESCOP` header on ASIAIR holds the **mount** ("ZWO AM5", "iOptron CEM25/CEM60"), not the scope. Infer the scope from `FOCALLEN`:

| FOCALLEN (mm) | Telescope token |
|---|---|
| 130–145 | FMA135 |
| 245–256 | Seestar (no token) |
| 280–292 | Z61 (with the old AT60ED 0.8× reducer) |
| 355–372 | Z61 (with the Flat61A) |
| 555–575 | SV503 (with its flattener) |
| 1360–1395 | RC6 |

  Anything else → ask Chris.

**Inside a session:**
```
lights/            raw light frames. Filter-split variants allowed: lights-Ha, lights-OIII …; mosaic panels: lights-Panel1 …;
                   legacy exposure splits: lights-120s
flats/             flats (filter suffixes allowed as above)
darkflats/         ONLY for ASI294MC and ASI183MM. NOT for ASI2600MM/MC (low noise; flats use the master bias)
stacked/           stacks and outputs you want to keep (masterLight_*, result_*, starless/starmask, Seestar Stacked_*, previews)
PROJECT_INFO.txt   generated (see §6)
.project_notes.txt free-text notes; appended to PROJECT_INFO as NOTES (survives regeneration)
.flats_are_copies  present when flats/darkflats were copied from a sibling night (records the source)
_to_delete/        staging for anything retired; Chris deletes it
```
- **Nothing else loose in the session root.** Lowercase subfolder names only.
- **DSLR (CR2) sessions are the exception:** CR2 lights and calibration stay together in the session root, since there are no libraries for the 450D. They get `PROJECT_INFO.txt` from `cr2info.py`.
- **Darks and biases never live in a session** for ZWO cameras; they go to the libraries.

---

## 5. Calibration libraries
- `001-MasterBias/<Camera>/<YYYY-MM-DD>[ (temp)]/`
- `002-MasterDarks/<Camera>/<N> Seconds/<YYYY-MM-DD>[ (temp)]/`. Capital S; `N` is the integer exposure (`300 Seconds`).
- **Camera folder names are exact:** `ASI2600MM Pro`, `ASI2600MC Pro` (headers say "ZWO ASI2600MC Duo"; **don't** add "Duo"), `ASI294MC Pro`, `ASI183MM Pro (PANE)`. Map from `INSTRUME` by substring: `2600MC`, `2600MM`, `294MC`, `183MM`.
- **Date folder = local calendar date the set started** (from the first frame's filename). Calibration is often shot in the daytime, so the lights' evening rule **doesn't** apply here.
- **Temperature suffix** only when the set isn't at the standard -10 °C: `2023-04-19 (+14C)`, `(-20C)`. **Always take it from the FITS `CCD-TEMP`**, never from a folder label; mislabeled folders caused real errors before.
- **10-frame cap:** keep at most **10 frames per set** (one exposure + filter), the first 10 in capture order. Extras go to `<that folder>/_to_delete/`. This applies to the cooled ZWO cameras (libraries, session `flats*`/`darkflats*`). **DSLR/CR2 calibration is exempt: keep every frame.** Reference: `trim_calibration.py`.
- **Duplicates:** if an incoming dark/bias set already exists in the library (same camera, date, exposure, gain, offset; matched by filename + size), don't store it again.
- **Matching darks/bias to lights** (used in PROJECT_INFO): same camera, exposure, gain; prefer the same offset; prefer within 5 °C of the lights' mean `CCD-TEMP` (flag `!! TEMPERATURE MISMATCH` otherwise); then the closest date. Reference: `projinfo.py pick()`.
- **Standard settings:**
  - 2600MM/MC: gain 100 / offset 50 / -10 °C (ASIAIR). Chris is switching N.I.N.A. to offset 50 too.
  - 294MC: gain 120, offsets 8 (N.I.N.A. 2021–22), 30 (ASIAIR 2023+), 0 (2020).
  - 183MM: gain 111 / offset 10 (ASIAIR), 120 / 8 (N.I.N.A.).

---

## 6. PROJECT_INFO.txt (one per session, generated)
Generated by `projinfo.py` (FITS/XISF) or `cr2info.py` (DSLR). Contents:
- Target and coordinates (mount RA/Dec plus plate-solved `CRVAL`).
- Camera, sensor, telescope (token or focal-length inference), focal length and image scale, guide camera, focuser position, capture tool.
- **Site** (see below).
- Capture nights, and a lights table (filter/exposure/gain/offset/temperature/count/folder) with total integration.
- Flats and dark flats.
- **Matching library darks and bias**, marked `USE ->`, with alternates.
- NOTES (from `.project_notes.txt`).

**Sites** (`SITELAT`/`SITELONG`; nearest within 4 km, else "unrecognized", then ask):

| Lat, Lon | Label |
|---|---|
| 41.430, -74.036 | Cornwall, NY (home) |
| 41.50, -74.017 | Cornwall, NY area (coarse early N.I.N.A. coordinates; home) |
| 41.390, -73.954 and 41.382, -73.975 | West Point, NY |
| 42.0896, -73.720 | **MHAA Star Party** (Lake Taghkanic State Park parking lot) |
| 44.3875, -68.0155 | Gouldsboro/Schoodic Peninsula area, Maine |
| 41.75, -73.917 | known GPS error; actually home |

- ASIAIR sometimes writes a **stale site** on the first frame of a night (a Maine location on a West Point night).
- When frames in one session disagree on site, flag it rather than silently picking one.

**Regenerate** a session's PROJECT_INFO after any change to it. **Reindex** (`projinfo.py --reindex`) after any change to `001`/`002`.

---

## 7. Ingest pipeline (what the app should do)

### 7.1 Sources (to be confirmed with Chris)
| Platform | Notes from existing data |
|---|---|
| **ZWO ASIAIR Plus** (primary, 2023+) | Filenames `Light_<Object>_<exp>s_Bin1_<cam>_[<filter>_]gain<g>_<YYYYMMDD-HHMMSS>_[<rot>deg_]<temp>C_<seq>.fit` (local time); `Flat_…`, `Dark_…`, `Bias_…` alike. Headers: `CREATOR=ZWO ASIAIR Plus`, `TELESCOP`=mount, `GUIDECAM`, `FOCUSPOS`, `SITELAT/LONG`, plate-solve WCS. The Duo guide sensor appears as `GUIDECAM=ZWO ASI220MM Mini`. |
| **N.I.N.A.** (2020–22; 3.1.2 in 2025) | Filenames `YYYY-MM-DD_HH-MM-SS_<Target>_[<filter>_]<exp>s_<gain>gain_<TYPE>_<seq>.fits` (local); FlatWizard flats `…_FlatWizard_<filter>_…`. Some sessions stored `.xisf`. `TELESCOP` is often the scope name ("RC6", "Z61") or "CGEM AND WO Z61". |
| **Seestar S50** | Sub-frames `Light_<Object>_10.0s_<IRCUT/LP>_<date>.fit`; app stacks `Stacked_<n>_<Object>_…fit/.jpg` → `stacked/`. No calibration library. |
| Legacy APT / SharpCap / Canon CR2 | Historical only. |

### 7.2 Classification
- Frame type from `IMAGETYP` (Light / Flat / Dark / Bias / "Dark Flat"), falling back to the filename prefix.
- **A "Dark" whose exposure matches the session's flats (typically < 10 s) is a dark flat**, not a library dark.

### 7.3 Routing
- Lights → `<Target>/<session>/lights[-<filter>]/`
- Flats → `…/flats[-<filter>]/`
- Dark flats → `…/darkflats[-<filter>]/` (294MC/183MM only; for 2600 cameras, send them to `_to_delete`)
- Darks/bias → library (§5), with the 10-frame cap
- App-produced stacks and previews → `stacked/`
- Final processed images → target root as `YYYY-MM-TargetName-Camera.fits` (month of the first night), JPEG/PNG → `000-FinalizedImages/` with the same base name

### 7.4 Multi-night imports
- Split by evening date into separate sessions.
- If flats were shot on only one night, **copy** the flats/dark flats whose filters match each night's lights into that night's session, and write `.flats_are_copies`. Chris wants every night self-contained; the duplication is intended.
- Write `.project_notes.txt` in each: "Night X of N. Siblings: …".
- Combined multi-night stacks go in the **first** night's `stacked/`, with a note in each sibling.

### 7.5 Naming collisions
If the computed session folder already exists:
- Same night, same camera and scope: append into it.
- Anything else: ask.

### 7.6 After ingest
1. Regenerate PROJECT_INFO for touched sessions; reindex if the libraries changed.
2. Run `calneeds.py` and update `ZZ_IMAGING_TODO.md` when gaps open or close.
3. If a target was added, update `targets.csv` and run `make_index_links.py`.
4. Append a line to the decision log in `ORGANIZATION_GUIDE.md` for anything non-routine.

---

## 8. Safety rules (non-negotiable)
1. **Never delete inside the Astronomy share.** Retire to `_to_delete/` in the same parent folder; Chris empties it.
   **Exception (Chris, Q2):** after a copy from the **source** (e.g. the ASIAIR SMB share) has been verified by **size + checksum** at the destination, and Chris has approved that batch on the review screen, the app may delete those files from the source.
2. **Never overwrite.** No-clobber moves/copies; on a name clash, skip and report.
3. **Verify counts** (and sizes, for copies) before and after every move or copy; log each operation (`Z95-ClaudeReferences/plans/*.log` shows the format).
4. **Raw data is sacred; processing output is disposable.** Before retiring any folder, confirm no raw capture exists *only* there (`rawcheck.py`).
5. **Confirm with Chris** before bulk renames or moves, and before creating new targets. Show a table of exactly what will change, in small groups.
6. Don't follow symlinks when walking the tree. Don't write outside `/mnt/user/Astronomy`.
7. Write files with atomic temp-then-rename (`*.part` → final) so a crash never leaves half files.

---

## 9. Index links (decided 2026-09-24)
- **Relative** symlinks (`../<TargetFolder>`) created **server-side**. They resolve on UnRAID, so they work over NFS (macOS) and SMB (Windows; Samba follows in-share links by default). **Never** create absolute links (`/Volumes/...`, `/mnt/...`).
- Names are zero-padded so they sort: `M031-`, `NGC0224-`, `IC0434-`. A target appears under **every** catalog number in `targets.csv` (M31 also as NGC0224; Rosette under NGC 2237/2238/2239/2244/2246; Markarian's Chain under M84/M86 and its NGC members).
- **`103-ByDate/`**: one relative link per session, named like the session folder (`YYYY-MM-DD-…`), pointing at `../<Target>/<session>`. It gives a chronological view of every night.
- `make_index_links.py` is idempotent. It only adds or removes symlinks inside `100-/101-/102-` and rewrites `ZZ_TARGET_INDEX.md`. Run it after any target change.
- **Fallback:** `ZZ_TARGET_INDEX.md` works everywhere even if a client doesn't follow links. If Windows ever shows the links as broken files, check the Samba `follow symlinks` setting before changing the approach.

---

## 10. Reference scripts (`Z95-ClaudeReferences/scripts/`)
| Script | Purpose |
|---|---|
| `fitshdr.py` | Minimal FITS + XISF header reader (no astropy) |
| `projinfo.py [--reindex] [-q] <Target/Session> …` | PROJECT_INFO generator; dark/bias matching; site lookup; telescope inference |
| `cr2info.py <session> "<location>"` | PROJECT_INFO for Canon CR2 sessions (EXIF via PIL) |
| `batch.sh [--reset]` | Regenerate all sessions in 140 s chunks (only needed for the old client-side workflow) |
| `calneeds.py` | Missing/mismatched darks and biases across sessions → IMAGING_TODO |
| `trim_calibration.py [--apply]` | Enforce the 10-frame cap (dry run by default) |
| `make_index_links.py` | Build catalog symlinks + ZZ_TARGET_INDEX.md from targets.csv |
| `split_nights.py`, `rename_sessions.py`, `apply_renames.py`, `extract_and_retire.py`, `rawcheck.py`, `group13_tidy.py` | One-off reorganization tools; useful patterns for split/copy/retire logic |

These were written for a 180-second-per-call remote shell, hence the time budgets and resume files. A server-side app doesn't need that. **Do** keep the idempotence and the logging.

---

## 11. Known quirks and gotchas
- On ASIAIR, `TELESCOP` = mount name; the telescope comes from `FOCALLEN`.
- Duo guide sensor = `ASI220MM Mini` in `GUIDECAM`.
- The filter `LP` in older headers = Baader UHC-S/L-Booster 36 mm (used as luminance), **not** the Optolong L-Pro.
- Some NINA FlatWizard dark flats were saved with the wrong filter label (all "SII"). Exposure matching is what matters for dark flats.
- Uncooled darks exist on purpose (`(+11C)`–`(+14C)`) and match uncooled captures (e.g. the FMA135 294MC session). Don't "fix" them.
- 2023 M101/M51 lights were shot at -20 °C; no matching darks exist yet (IMAGING_TODO).
- Every camera kit is built to **55 mm ± 1 mm ending in M48**, so any camera goes on any scope. Back focus isn't a variable for the app.
- FITS from ASIAIR: `BITPIX 16`, `BZERO 32768`; XISF has headers as `<FITSKeyword>` XML.

---

## 12. Open questions for Chris before building
1. Which platforms should the app pull from (ASIAIR via SMB share or USB export? N.I.N.A. output folder? Seestar app export?), and on what trigger (watch folder, button, schedule)?
Answer- Most data comes from ASIAIR via an SMB share, so let’s build focused on that for now.
2. Auto-file on a confident match, or always show a review screen before moving anything? (The current rules imply review for anything non-routine.)
Answer- Always show a review screen.  Index what’s there, show me what you’re going to copy and where to, and then go back and delete old data when it’s copied.
4. Should the app also run stacking (hand-off to the Siril container), or stop at filing plus PROJECT_INFO?
Answer- Stop at filing plus Project Info.  I’m treating these apps as separate.
6. Where should the app keep its own state or database: inside `Z95-ClaudeReferences/`, or in appdata on the server?
Answer- Since the app will have write access into the NFS, let’s keep data all in one place.  That way it doesn’t matter if I use Claude to organize data or my app, they both get the same starting point.
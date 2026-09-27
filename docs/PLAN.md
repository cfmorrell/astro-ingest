# astro-ingest: architecture and phased plan

## Context
**Goal:** make it simple for Chris to pull data off the ASIAIR, file it, and keep the ASIAIR clean.

The Astronomy share was reorganized by hand over two days (see the `docs/ORGANIZATION_GUIDE.md` decision log). The rules and
reference scripts from that work now become an app with this flow:
1. Find the ASIAIR on the home network and connect to its `EMMC Images` SMB share.
2. Index the share.
3. Show a review screen of exactly what gets copied where.
4. Copy onto the NAS under the share's rules, with checksum verification.
5. Write PROJECT_INFO and index links.
6. After verification and Chris's approval, delete the copied files from the ASIAIR.

There's no stacking. State lives on the share (`STATE_DIR`).

### What the survey found (drives the design)
- **ASIAIR layout** (`EMMC Images` share, same shape as `/astro-sandbox/_asiair-sample`, 56 GB):
  `Autorun/{Light/<Object>,Flat,Dark,Bias}`, `Plan/Light/<Object>`, plus `Live`, `Preview`, `Video`, `log`,
  `GuidingDarkLibrary`, `System Volume Information`, `batch_stack_tmp`. Every `.fit` has a `_thn.jpg` sibling, and
  `.DS_Store`/`._*` files are present. It sits at 192.168.1.43 today (guest SMB).
- **Filenames** follow `<Type>_<Object with spaces>_<exp>(s|ms)_Bin1_<cam>_[<filter>_]gain<g>_<YYYYMMDD-HHMMSS>_[<N>deg_]<temp>C_<seq>.fit`
  in local time. Headers have `DATE-OBS` (UTC), `FOCALLEN`, `OFFSET`, `SITELAT/LONG`, and `TELESCOP` = the mount.
- **Flats all sit in one `Autorun/Flat` folder** with no target, usually shot at dawn after the night. They're matched
  to lights by night + camera + rotation angle (mod 180°, because a meridian flip reports +180°: Soul's 3°/185° lights
  match its 185° flats). **`79deg` looks like a default or no-solve value**, so the angle is a hint that raises a
  warning, not a hard key.
- **447 of the 1,026 Autorun/Plan frames (44%) are already ingested** (matched by filename): Autorun lights,
  5 flat sets, the 2026-08-29 bias and 300 s darks, NGC 7000 2025-07-03, and most of Elephant Trunk 09-13/14 and
  Heart 09-15. A filename can sit in several NAS folders (flats copied to sibling nights: 522 names).
- **Ingest evidence beats catalog matching.** `NGC 6888` matches CrescentNebula-NGC6888 by catalog, but Chris filed
  the 2025-10-16 FMA135 frames under SadrRegion-IC1318 (a wide field that contains it). For already-ingested frames,
  where they are wins. When a target is unclear (several candidates or none), the review screen asks.
- **Orphan thumbnails exist:** 10 `_thn.jpg` for 2026-06-16 300 s darks whose `.fit` is gone.
- **Other tools write to the share:** `Plan/Light/NGC 5907/astropup-view-scan.json` (+ `._` file), `.DS_Store` and
  `._*` files in Autorun/Plan (20 in all), including `._` leftovers of 10 NGC 5907 frames deleted from a Mac. They
  go on the cleanup screen as "unrecognized", each called out and approved individually.
- **Frames missing from sessions already on the NAS:** 9 dawn Elephant Trunk, 9 dawn Heart, and 4 M42 frames. Chris deleted
  these as poor quality after the manual copy.
- **New data:**
  - Soul 2026-09-23 and NGC 5907 06-15 (existing targets).
  - NGC 4565 04-27 and NGC 5982 06-19 (**new targets**).
  - 120 s darks from 2026-04-28 (a new library set).
  - One M13 frame from 2025-06-11.
  - **Orphan flats from 2026-05-16 (4.0 s) and 2026-06-24 (6.2 s).** No lights for those nights are on the ASIAIR
    or on the NAS (`103-ByDate` has nothing between 04-11 and 08-28). The lights may have been lost.

## Decisions from Chris
1. **Already-ingested frames:** checksum-compare each one against its NAS copy. Identical frames are offered for
   source cleanup and never re-copied.
2. **Frames missing from an existing session:** ask per batch. The default is **append**, because Chris wants all data
   for now. Keep a per-frame record so a later quality-analysis feature can reject frames.
3. **`Live`, `Preview`, `Video`, `log`, `GuidingDarkLibrary`:** ignored by default. They're listed as "not handled"
   and never deleted. Leave an opt-in hook for occasional EAA copies, but don't build it yet.
4. **Source delete removes each `.fit` and its `_thn.jpg` together**, for both copied and already-ingested frames.
   The cleanup screen lists both files per frame, and its totals include the thumbnails. Thumbnails left without a
   `.fit` are also offered for deletion. So is anything else inside `Autorun/`/`Plan/` (other tools' files such as
   `astropup-view-scan.json`, `._*` AppleDouble files, `.DS_Store`): each is **listed and called out by name** and
   deleted only when Chris approves it, like everything else. Nothing is silently ignored. Then prune empty directories, but never the ASIAIR's structural folders
   (`Autorun/Light`, `Plan/Light`, …). Thumbnails are never copied to the NAS.
5. **Calibration completeness gate before any delete.** Before a flat or dark-flat set is offered for deletion, the
   app searches for the lights it belongs to. A set that has lights is useful and gets filed with them, as normal.
   - Where it searches: the ASIAIR (any object that night), NAS sessions for the same or adjacent night with a
     matching camera (and angle mod 180), and NAS sessions that have **no flats** but whose camera, angle and
     date fit.
   - **If no lights are found:** the set is flagged "lights not found, possibly lost" and **blocked from cleanup**
     until Chris either files it with a session he points to or explicitly releases it for deletion. The cleanup
     screen has a one-click "release" per set.
   - Chris confirmed the 05-16 and 06-24 lights don't exist, so those two sets are the first ones to release. The
     acceptance test checks that they're blocked until then.
   - The reverse check runs too: lights with no matching flats get a warning on the review screen.
6. **Only the `EMMC Images` share** is supported.
7. **ASIAIR discovery:** the app finds the ASIAIR on `192.168.1.0/24` and connects to `EMMC Images` over SMB itself.
8. **UI consistency with astro-stacker** (github.com/cfmorrell/astro-stacker, surveyed at `f31cbcb`), since the two
   apps may be merged later. That overrides CLAUDE.md's Jinja2 + HTMX suggestion. Mirror the stacker's patterns:
   - **Frontend:** plain `static/index.html` + `app.js` + `styles.css`. No build step and no framework, served by
     FastAPI `StaticFiles` mounted last, talking to a JSON API.
   - **Styling:** copy `styles.css` wholesale as the base. That's the `:root` tokens (`--bg #0a0c12`, `--panel`,
     `--accent #6366f1`, `--warn`, `--danger`, `--success`, `--radius`) and the components: `topbar` with `.brand` and
     `.version-badge`, the health `.badge`, `.card`/`.card-header`/`.card-title`, `.stepper` with `.step`/`.dot`/
     `.connector`, `.field`/`.field-row`, `.hint`, `.help-box`, button `primary`/`ghost`/`small`/`danger-outline`,
     `.chip`/`.checklist`, `.progress-*`, `.log-view`, `.active-jobs-panel`, `.external-job-banner`,
     `.session-mismatch-warning`, and the lightbox. Ingest-only styles go in a clearly marked section at the bottom.
     Record the source commit in a header comment so drift can be diffed later.
   - **JS helpers:** reuse `api()`, `el()`, `pollJob()`, and `setStepBadge()` in the same shapes.
   - **Jobs API:** match the stacker's shape: `/jobs`, `/jobs/{id}` (`status`, `percent_complete`, `current_line`,
     `steps`), and `/jobs/{id}/log`.
   - **Versioning:** `config.VERSION` surfaced through `GET /health` and the header badge, staying under 1.0.
   - **Build:** a single repo and a GitHub Actions `docker-publish` workflow.
   - **Scope boundary:** the stacker's Handoff says importing and sorting belong to *this* app. astro-ingest owns
     "get it off the ASIAIR and onto the NAS"; the stacker reads from the NAS (its read-only `CAPTURES_DIR`).

## Architecture
```
astro_ingest/
  config.py        env: ASTRO_ROOT (write target), ASTRO_NAS (read-only lookups), STATE_DIR, TZ,
                   ASIAIR_SUBNET=192.168.1.0/24, ASIAIR_SHARE="EMMC Images", optional ASIAIR_HOST (pin an IP),
                   optional ASIAIR_ROOT (use a local dir/mount instead of SMB: the sandbox sample in dev).
                   Validates at startup; exposes the write guard.
  sources/         Source interface: walk / stat / open_read / delete. Nothing else touches the source.
    local.py       LocalDirSource (ASIAIR_ROOT: _asiair-sample, a scratch copy, or a kernel mount)
    smb.py         SmbSource via `smbprotocol`/`smbclient` (pure Python, guest auth; no CAP_SYS_ADMIN or kernel
                   mount needed in the container). Streaming reads, reconnect on drop.
    discover.py    concurrent TCP:445 probe of ASIAIR_SUBNET (short timeouts), then guest share enumeration.
                   A host exporting "EMMC Images" is an ASIAIR. Tries the last-known IP (STATE_DIR) first, then
                   scans. The dashboard shows found/online/offline and has a "Find ASIAIR" button.
  core/            PURE library, no web imports, fully unit-tested
    fits.py        port of fitshdr.py (FITS + XISF header reader; reads just the header blocks over SMB)
    asiair.py      filename parser (object with spaces, s/ms, optional filter/deg), header fallback,
                   ignore rules, .fit ↔ _thn.jpg pairing
    rules.py       night date (see "Local time" below), camera tokens/library folder names,
                   FOCALLEN→scope table, temp suffix from CCD-TEMP, site table + 4 km lookup, naming
    targets.py     targets.csv; normalize "M 8"/"NGC 7000"/"SoulNebula"; catalog-ID then name match;
                   unmatched → decision queue; new-target proposals (folder, name, M/NGC/IC/other)
    nas.py         walk rules (no symlinks; skip 0*/1*/Z*/dotfiles/_to_delete); filename→path index;
                   session index (night, camera, scope, angle, has-flats); calibration library index
    planner.py     classify (IMAGETYP, then prefix; short "Dark" matching flat exposure → darkflat), group per
                   target/night/camera/scope, flat↔lights matching + completeness gate (decision 5), darkflat policy
                   (294MC/183MM only), library routing with duplicate check + 10-frame cap, §7.5 collisions,
                   multi-night split + flat copies + notes, already-ingested / missing-from-session detection.
                   PlanItem = (src, dst, action, reason, warnings). Actions: copy | already-ingested | append |
                   needs-decision | calib-without-lights | over-cap | ignored
    fsops.py       the ONLY writer to the share: path guard (under ASTRO_ROOT/STATE_DIR only), streaming copy +
                   BLAKE2b → *.part → fsync → no-clobber rename → re-read + re-hash; never .DS_Store/._*;
                   retire to _to_delete
    cleanup.py     source deletes, only via Source.delete, only verified + approved + gate-passed items
    projinfo.py    port of projinfo.py as a function returning text (nightof via zoneinfo, not a fixed 16 h)
    links.py       port of make_index_links.py parameterized by root (100–103, ZZ_TARGET_INDEX.md)
    calneeds.py    port; report only (ZZ_IMAGING_TODO.md edits stay manual for now)
    oplog.py       append-only TSV logs in STATE_DIR/logs/, in the Z95 plans/*.log style
  state/db.py      SQLite STATE_DIR/ingest.sqlite3, journal_mode=DELETE (no WAL on shfs/FUSE). Tables: asiair
                   (last IP, seen), scans, source_files(path,size,mtime,hash,thumb), batches, plan_items,
                   operations, decisions. Lock file STATE_DIR/ingest.lock so the app and Claude never write
                   at the same time. Each batch is exported to STATE_DIR/batches/<id>.tsv for Claude to read.
  jobs.py          one in-process worker; resumable from the operations table; pauses cleanly if the ASIAIR drops
  cli.py           astro-ingest find | scan | plan | apply | verify | cleanup | projinfo | links | reindex
  api.py           FastAPI JSON API (/health, /asiair, /scan, /batches/*, /decisions, /jobs*, thumbnail proxy);
                   StaticFiles mounted last, as in the stacker
static/            index.html + app.js + styles.css (stacker base + ingest section). Topbar: "astro-ingest",
                   version badge, "pull · file · clean", and an ASIAIR health badge (online @ IP / offline).
                   Stepper: Connect → Scan → Review → Copy & verify → Catalog → Clean up, one .card per step.
                   Review is grouped per destination session: counts, sizes, and dst paths, with decisions as
                   chips/checklists, warnings as .session-mismatch-warning, and the ASIAIR _thn.jpg previews
                   in the stacker's lightbox. Also an active-jobs panel and a log view per job.
tests/             pytest; tiny generated FITS fixtures (hand-written header + 2×2 data) in an ASIAIR-shaped tree,
                   a fixture NAS tree, and a fake Source for discovery/SMB-free tests
```
### Local time (EDT vs EST)
- **Primary source is the filename timestamp.** The ASIAIR writes local wall-clock time (`20260923-211420`), and the
  night date is that timestamp minus 12 h. No UTC offset is involved, so daylight saving can't affect it.
- **Fallback, and a cross-check on every frame:** convert `DATE-OBS` (UTC) with
  `zoneinfo.ZoneInfo(TZ)`, where `TZ=America/New_York`. The tz database picks EDT (−4) or EST (−5) for each date,
  including the changeover nights.
- **If the two disagree by more than a minute or two,** the review screen warns: the ASIAIR clock or time zone is
  wrong (it takes its time from the phone or tablet), and the frame waits for Chris instead of guessing.
- Tests cover a summer night, a winter night, and both DST-change nights.
- The fixed "UTC − 16 h" in the old `projinfo.nightof()` is replaced by this. In winter it lands an hour off, which
  only mis-dates frames taken between 11:00 and 12:00 local, but it's still wrong.

The default `STATE_DIR` in dev is `/astro-sandbox/Z95-ClaudeReferences/ingest/`.

## Phases (each one a small series of reviewable commits)
0. **Repo hygiene and skeleton.**
   - `.gitignore`: `*.fit`, `*.fits`, `*.fts`, `*.xisf`, `*.jpg`, `*.jpeg`, `.env`, `__pycache__/`, `.DS_Store`,
     `._*`, and local state.
   - Remove the committed `.DS_Store` ×2 and the `.pyc`. Add `.env.example`, `pyproject.toml`, the skeleton, pytest,
     the fixture generator, and `config.py`.
   - Dev naming is `astro-ingest-dev` (Chris's change). Update the host paths in CLAUDE.md and SETUP.md to
     `/mnt/user/docker_appdata/astro-ingest-dev/…`, and make `run-dev.sh` pass `--env-file` so the container gets
     `ASTRO_ROOT` and the other variables.
   - Sync `docs/ORGANIZATION_GUIDE.md` with the live copy.
   - Update CLAUDE.md's "Suggested stack" to record today's decisions: the stacker-style static frontend, direct SMB
     plus discovery, the decisions above, and the goal statement.
1. **Core rules library and LocalDirSource (read-only).** Port `fits`, `asiair`, `rules`, `targets` and `nas` (was `archive`),
   with tests for every rule: night date across EDT/EST, FOCALLEN ranges, camera names, temp suffix, site lookup,
   object normalization, thumbnail pairing. `cli scan` inventories `_asiair-sample`.
2. **Planner and review screen (read-only).** This is the first UI commit: the stacker `styles.css` base, the
   `index.html` shell (topbar, stepper, cards), the `/health` endpoint with `VERSION`, then the Review step.
   Acceptance test: the plan for `_asiair-sample` against `/astro` must
   reproduce the survey:
   - already-ingested sets
   - 22 missing-from-session frames, defaulting to append
   - Soul and NGC 5907 sessions
   - two new-target proposals
   - the `120 Seconds/2026-04-28` darks
   - 05-16 and 06-24 flats as `calib-without-lights`
2b. **Previews and frame-quality screening (done 2026-09-25).** Ports of astro-stacker's `app/imaging.py` and
   `app/framestats.py`; frames scored against their group (plus NAS peers) in a background job (stacker-shaped
   `/jobs`), stats in `STATE_DIR/quality.json`, renders in `CACHE_DIR`. Flagged lights are `rejected` by default
   (σ 4.0), keepable per frame; stacker-style cards, lightbox and metric strips. Checked against Chris's own
   hand-rejections: all 17 dawn frames from ET 09-14 / Heart 09-15 flagged at σ 4.0.
   *Network contingency (Chris, 2026-09-25):* scoring reads every light once before copying, and the copy reads it
   again. If SMB from the ASIAIR proves too slow (measure in phase 3), re-sequence: copy whole groups first, score on
   the NAS, then **retire** excluded frames to the session's `_to_delete/` (never delete on the NAS) and let cleanup
   remove them from the ASIAIR as usual. Decide after seeing real throughput.
   *Measured 2026-09-25 (phase 3):* SMB reads from the ASIAIR run at **~10.6 MB/s** (4.9 s per 52 MB frame; two
   parallel reads don't help: the link is the limit). Listing the share takes 20 s, a header scan 39 s. For the
   sample, copying the 559 new files (~29 GB) is ~46 min; scoring first and then copying reads new frames twice
   (~1 h 50 min). Option for phase 4 (to decide): **read each new frame once into a local staging area**
   (checksummed on the way in), score and file from there; rejected frames never enter the Astronomy share.
3. **ASIAIR discovery and SmbSource (read-only against the real device).** Build `discover.py` and `smb.py`, plus the
   dashboard status and Find button. Test with a fake network in unit tests. Against the live ASIAIR, only list and
   read. Check SMB throughput and reconnect behavior.
4a. **Select → Stage (done 2026-09-25).** Select: chronological thumbnail grid per set, leave out frames/sets,
   live Wi-Fi estimate. Stage: each selected frame read once into `STAGING_DIR` (BLAKE2b, `.part` → rename,
   re-verified), resumable, then scored from the staged copies. Real ASIAIR: 10 flats (522 MB) staged at 9.1 MB/s,
   `b2sum`-verified, device listing unchanged.
4b. **Copy & verify (done 2026-09-26).** Sandbox run on the Splinter 06-15 test session: 74 files (3.86 GB) copied
   and verified in ~1 min at 50 MB/s, 0 clashes/failures; all 74 match their staging hashes by independent `b2sum`;
   64 lights + 10 flats; staging cleared (4.54 GB); a second approval copies nothing.
4. **Copy and verify into the sandbox** (now from staging, at disk speed). `fsops`, `jobs`, the op log, approval, and progress. Checksum
   already-ingested items against `ASTRO_NAS`. Test resume-after-kill and source drop mid-copy. Confirm the
   no-clobber rename on shfs.
5. **Catalog (done 2026-09-26; Chris named the step).** After Copy & verify, for every copy batch not yet
   catalogued: PROJECT_INFO.txt for touched sessions (and sessions a new library batch may affect), new
   `targets.csv` rows (old file retired to `_to_delete/`), index links incl. `103-ByDate` and `ZZ_TARGET_INDEX.md`,
   sibling-night lines in `.project_notes.txt` (only added), `.flats_are_copies`, a calibration-gap report, and
   decision-log drafts in `STATE_DIR/decision-log-drafts.md`. Preview with diffs, one approval, fsops writes under
   the lock, log in `STATE_DIR/logs/catalog-*.log`. Ports regression-checked against `/astro`: PROJECT_INFO content
   identical except first-frame lines (the reference used directory order) and stale `_to_delete` alternates; the
   182 live index links reproduced exactly. **Cross-night flats (§7.4)** are planned in Review: a "No flats"
   decision, offered only for the same target/camera/scope/filter within 7 days and rotation mod 180° within ±3°
   (never 79°); **default is don't borrow** ("a bad flat is worse than no flat"). Sandbox run on Splinter 06-15:
   PROJECT_INFO (64 lights, 10 flats, 2026-08-29 library darks/bias), 13 links, re-preview empty.
6. **Clean up (built 2026-09-26; real-device deletes wait for Chris's backups).** Scratch run through the app
   (`ASIAIR_ROOT=/astro-sandbox/_asiair-scratch`, a `cp -al` copy of the sample): Verify read all 520 older frames
   (23.3 GB) and matched every NAS copy by BLAKE2b in 77 min at ~4.5 MB/s, 0 mismatches; Clean up with the default
   ticks deleted exactly the 520 verified `.fit` + 520 `_thn.jpg` (27.1 GB; before/after listing differs by exactly
   those 1,040 files), each NAS copy re-hashed first, 6 emptied object folders removed, type folders kept; callout
   groups untouched; `_asiair-sample` and the share unchanged apart from the app's own state/log files. SMB deletes
   tested against a throwaway impacket server (`tests/test_smb_cleanup.py`). `ALLOW_DEVICE_DELETE=1` required for a
   real device.
   **First real-device run, end to end (2026-09-26, after Chris's backups):** DracoTrio 06-19 only (every other
   set left out on Select, answers restored after). Stage read 82 frames (4.3 GB) at 9.05 MB/s; Review: 65 to copy,
   17 rejected for quality (0001–0007 dusk at 342°, 0044–0045, 0065–0072 running into Chris's own dawn marks
   0073–0082, which were left out and never read); Copy & verify 65 files, all `b2sum`-OK; Catalog wrote
   PROJECT_INFO, the targets.csv row (old file retired), 2 links, ZZ_TARGET_INDEX.md; Clean up deleted exactly the
   92 frames + 92 thumbnails (copied, rejected, left out) from the ASIAIR over SMB, device listing 2312 → 2128 with
   no other change. Everything else stays on the device for the 6b acceptance run.
   Original plan: only verified, approved, gate-passed items. Before deleting, re-check source size and mtime
   against the scan. Delete each `.fit` **and** its `_thn.jpg` plus orphan thumbnails, prune empty non-structural
   dirs, and log every delete. Test against a scratch copy (`/astro-sandbox/_asiair-scratch`) with LocalDirSource.
   Test SMB deletes against a throwaway Samba share before the real ASIAIR is ever touched.
6b. **Full-scale functional test, then a fine-detail review (Chris, 2026-09-25).** Once every step works end to
   end (Connect → Scan → Stage → Review → Copy & verify → Catalog → Clean up), run a full-size ingest of the sample and
   then go through the app screen by screen together for refinements: wording, layout, defaults, and anything that
   only shows up with real data at full scale. Basic functionality first, polish after.
7. **Production.** App Dockerfile, a `docker-publish` workflow (same as the stacker), and a run script (`ASTRO_ROOT=/astro` rw, LAN access for discovery), put the app
   behind Nginx Proxy Manager with authentication (it deletes source data), and confirm the NAS backup first.

## Verification
- Run `pytest` at every phase. The pure library carries most of the coverage.
- **Phase 2:** diff `astro-ingest plan --source /astro-sandbox/_asiair-sample` against a checked-in expected plan.
- **Phase 3:** `astro-ingest find` locates the ASIAIR at 192.168.1.43 and lists `EMMC Images`, and a scan over SMB
  matches the scan of the sample.
- **Phases 4–6:** run end to end on the sandbox: scan → approve → copy → verify (re-hash independently with `b2sum`).
  Check counts before and after. Nothing may be written outside `/astro-sandbox`. After cleanup, no approved `.fit`
  or `_thn.jpg` remains, gate-blocked flats are untouched, and nothing else changed. Walk the UI on port 8090.

## Tell Chris
- The ASIAIR is currently mounted **read-only** in dev (`/asiair`), which is fine: the app talks SMB directly, and
  deletes stay off the real device until phase 6 is proven.

# astro-ingest

A self-hosted web app that pulls astrophotography capture data off a **ZWO ASIAIR** over its SMB share, files it
onto an Astronomy share on a NAS by that share's rules, and then cleans the ASIAIR, deleting only what is proven to
be safely on the NAS, and only after you approve it. It runs in a Docker container on UnRAID.

It is built around one specific share layout (see [`docs/ORGANIZATION_GUIDE.md`](docs/ORGANIZATION_GUIDE.md)): target
folders, dated sessions with `lights/` and `flats/`, master dark and bias libraries, and index folders by catalog
number and date. It stops at filing and cataloguing: stacking and processing belong to a separate app
([astro-stacker](https://github.com/cfmorrell/astro-stacker)), whose look it shares.

## How a run goes

The app walks through seven steps, with a step bar at the bottom of the window and **Next** always in the same place.

1. **Connect** finds the ASIAIR on your home network (worked out from your browser's address) and remembers recent
   devices by address and a name you give them. Several ASIAIRs look identical on the network, so it never
   switches devices on its own.
2. **Scan Images** lists the device and reads frame headers in `Autorun/` and `Plan/` (everything else is left
   alone). Frames already on the NAS are recognized by name and size. A grid of the device's own thumbnails is a
   quick first pass: leave out anything obviously bad before it's read.
3. **Stage** reads each selected frame from the ASIAIR exactly once over Wi-Fi (the slow part, about 9 MB/s) into a
   staging share, checksummed on the way in. Everything after this works from the staged copy at disk speed.
4. **Review** shows what will be copied where. Light frames are scored against the rest of their night (star
   count, FWHM, eccentricity, SNR, sky background; astro-stacker's method) and outliers are recommended to be left
   out. Anything the app can't decide alone becomes a **decision**: an unknown target, frames missing from a
   session already on the NAS, a damaged NAS copy, flats without lights, a very small group. Every decision can
   also release its frames for deletion.
5. **Copy & verify** copies onto the share and checks every copy against the checksum taken off the ASIAIR. Nothing
   on the NAS is ever overwritten or deleted: a damaged copy being replaced is moved to `_to_delete/` first.
6. **Catalog** writes `PROJECT_INFO.txt` for each session (equipment, nights, the matching library darks and bias),
   adds new targets to `targets.csv`, keeps the index links and `ZZ_TARGET_INDEX.md` current, and reports
   calibration gaps.
7. **Clean up** deletes from the ASIAIR only what you tick, nothing is ticked for you. Frames are *recommended*
   once a checksum proves the NAS copy identical (copied by the app, or checked by a quick or thorough **check**
   for frames that reached the NAS earlier). Right before deleting, the device is listed again and each NAS copy is
   re-checked; afterwards the listing must differ by exactly the deleted files.

**Start over** (in the header) clears a run's working state and returns to Connect. It never deletes the last copy of a
frame. The app keeps its place on the server, so any browser window, reload or reconnect comes back to the same step.

## Safety

- The ASIAIR is only read until Clean up, and Clean up deletes only files you approved, only in `Autorun/` and
  `Plan/`, only if they haven't changed since the scan, and never while the device is capturing (nothing written in
  the last 15 minutes). Deleting from the device is switched off unless `ALLOW_DEVICE_DELETE=1`.
- On the NAS the app only adds: no overwrites, no deletes (retired files go to `_to_delete/`), atomic writes, one
  writer at a time, and a log of every operation in `Z95-ClaudeReferences/ingest/logs/`.

## Install on UnRAID

The image is built by GitHub Actions on every push to `main` (after the tests pass) and published as
**`ghcr.io/cfmorrell/astro-ingest`** (`latest`, and `v0.2`-style tags for releases). Install it from the UnRAID
template in [`deploy/unraid/`](deploy/unraid/) or with [`deploy/run.sh`](deploy/run.sh); the full steps are in
[`docs/DEPLOY.md`](docs/DEPLOY.md).

| Container path | Default host path | What |
|---|---|---|
| `/astro` | `/mnt/user/Astronomy` (**read-write**) | The Astronomy share. App state lives in `Z95-ClaudeReferences/ingest/` on it. |
| `/staging` | `/mnt/user/astro-ingest-staging` | Frames read once from the ASIAIR, until filed. |
| `/cache` | `/mnt/user/docker_appdata/astro-ingest/cache` | Rendered previews (disposable). |
| `8000` | `8091` | The web app. |

| Variable | Default | What |
|---|---|---|
| `ALLOW_DEVICE_DELETE` | `0` | `1` lets Clean up delete from the ASIAIR. |
| `TZ` | `America/New_York` | Your time zone: night dates come from the ASIAIR's local time. |
| `ASIAIR_SUBNET` | (detected) | Only to override the home network, e.g. `192.168.1.0/24`. |
| `ASSUMED_WIFI_MB_S` | `10` | Wi-Fi speed for estimates until a staging run measures it. |

## Development

A long-lived dev container on UnRAID holds the repo, a Python venv and Claude Code; it writes only to a sandbox copy
of the share (`/astro-sandbox`) and reads the real one read-only. Setup: [`docs/SETUP.md`](docs/SETUP.md).

```bash
.venv/bin/pytest                       # tests (generated tiny FITS files; the sample test runs when the share is mounted)
.venv/bin/astro-ingest serve           # web app on container port 8000 → http://<unraid>:8090
.venv/bin/astro-ingest plan            # the same flow from the CLI: find, scan, plan, stage, copy, catalog, cleanup
```

- Plain FastAPI + a static vanilla-JS page (no build step), `smbprotocol` for the ASIAIR, numpy/astropy/photutils
  for previews and scoring, SQLite for batches. Filing rules live in the pure `astro_ingest/core/` library.
- Design notes and every decision so far: [`CLAUDE.md`](CLAUDE.md), [`docs/PLAN.md`](docs/PLAN.md),
  [`docs/ClaudeHandoff.md`](docs/ClaudeHandoff.md).
- The version is in `astro_ingest/config.py` (shown in the header and `/health`). It goes up by a minor version
  with each release and stays under 1.0 until the app has been in use for a while.

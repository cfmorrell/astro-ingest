# astro-ingest

A web app that pulls astrophotography capture data (starting with **ZWO ASIAIR over SMB**) and files it into
the Astronomy share on Chris's UnRAID NAS according to the share's established rules. It shows a **review screen**
before anything moves, then writes `PROJECT_INFO.txt`, keeps the calibration libraries and index links current,
and, after verified copies and approval, cleans up the source.

- Spec: [`docs/ClaudeHandoff.md`](docs/ClaudeHandoff.md). Filing rules: [`docs/ORGANIZATION_GUIDE.md`](docs/ORGANIZATION_GUIDE.md).
- Dev environment: [`docs/SETUP.md`](docs/SETUP.md) (UnRAID dev container, GitHub, Claude Code).
- Reference implementations from the share's reorganization: [`reference/scripts/`](reference/scripts/).

Out of scope: stacking and processing (a separate Siril stacking app).

## Run it (dev container)
```bash
.venv/bin/astro-ingest scan            # inventory of the ASIAIR source (read-only)
.venv/bin/astro-ingest plan            # what would be copied where, decisions, clean-up preview (read-only)
.venv/bin/astro-ingest serve           # web app on container port 8000 → http://<unraid>:8090
.venv/bin/pytest                       # tests (the sample acceptance test runs when /astro and the sample are mounted)
```

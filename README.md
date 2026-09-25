# astro-ingest

A web app that pulls astrophotography capture data (starting with **ZWO ASIAIR over SMB**) and files it into
Chris's Astronomy archive on UnRAID according to the archive's established rules. It shows a **review screen**
before anything moves, then writes `PROJECT_INFO.txt`, keeps the calibration libraries and index links current,
and, after verified copies and approval, cleans up the source.

- Spec: [`docs/ClaudeHandoff.md`](docs/ClaudeHandoff.md). Archive rules: [`docs/ORGANIZATION_GUIDE.md`](docs/ORGANIZATION_GUIDE.md).
- Dev environment: [`docs/SETUP.md`](docs/SETUP.md) (UnRAID dev container, GitHub, Claude Code).
- Reference implementations from the archive reorganization: [`reference/scripts/`](reference/scripts/).

Out of scope: stacking and processing (a separate Siril stacking app).

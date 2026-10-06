# OWASP ZAP security scan

`.github/workflows/zap-scan.yml` runs an [OWASP ZAP](https://www.zaproxy.org/) **active** scan against astro-ingest in
GitHub Actions: on every push to `main` that touches the app, every Monday, and on demand.

## How the pieces fit

```
GitHub runner (throwaway VM)
 ├─ pip install .                       the app, as in the image
 ├─ dev/zap/make_fixture.py             fake ASIAIR folder + fake share (generated FITS, targets.csv)
 ├─ astro-ingest serve :8000            the app, pointed only at that fake data (ALLOW_DEVICE_DELETE=0)
 ├─ zaproxy/action-full-scan  ──▶ http://localhost:8000/              spider + AJAX spider, then active attacks
 └─ zaproxy/action-api-scan   ──▶ http://localhost:8000/openapi.json  every API endpoint, active attacks
        │
        ├─ artifacts: zap-full-scan, zap-api-scan (HTML / Markdown / JSON reports), astro-ingest-log
        └─ the run's summary page: both Markdown reports
```

The ZAP actions run ZAP in Docker on the same runner (host networking), so `localhost:8000` is the app. Two scans
because the page is built in JavaScript: a spider alone finds little, while the API scan reads FastAPI's
`/openapi.json` and attacks all 31 endpoints with their real parameters.

**Never point it at the real app.** An active scan sends attack payloads (injection strings, path traversal, oversized
inputs, ...) to every endpoint, including the ones that write to the share, start over, and delete from the ASIAIR.
It only ever runs against the throwaway data on the runner.

## Running it and getting the reports

- **Run it:** GitHub → Actions → *OWASP ZAP security scan* → *Run workflow*. It takes roughly 15 to 40 minutes
  (the active scan dominates).
- **Read it on the page:** open the run; the **Summary** tab shows both reports (alert counts by risk, then each alert
  with its URLs).
- **Download it:** the run's **Artifacts** section has `zap-full-scan` and `zap-api-scan` (each a zip with
  `report_html.html`, the easiest to read, plus `report_md.md` and `report_json.json` for tooling) and
  `astro-ingest-log` (the app's own log during the attack, handy for seeing what an alert actually triggered).

## Reading a report

Each alert has a **risk** (High, Medium, Low, Informational), a **confidence**, a **rule id** (e.g. 10038), the URLs
and parameters it was seen on, and ZAP's evidence and suggested fix. Expect for this app, at least at first:

- missing security headers (Content-Security-Policy, anti-clickjacking `X-Frame-Options`, `X-Content-Type-Options`):
  the app sets none today;
- no authentication: by design on the LAN, and the reason the deploy guide recommends Nginx Proxy Manager with
  authentication in front of it;
- plain HTTP (HTTPS would come from the proxy).

Server errors (HTTP 500) under attack, or anything High, are the ones to look at first.

## Tuning

- **`.zap/rules.tsv`**: one line per rule id: `IGNORE` (reviewed, doesn't apply), `WARN` (the default), or `FAIL`.
- **Fail the run on findings:** set `fail_action: true` in the workflow; with it, alerts make the job fail (and rules
  set to `FAIL` always do).
- **A GitHub issue with the results:** set `allow_issue_writing: true` and add `issues: write` to the workflow's
  `permissions`; ZAP then opens an issue and updates it on each run.

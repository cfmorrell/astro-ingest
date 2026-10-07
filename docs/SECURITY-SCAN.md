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
and parameters it was seen on, and ZAP's evidence and suggested fix.

Server errors (HTTP 500) under attack, or anything High, are the ones to look at first. What the first scan
(2026-10-06) found, and what came of it:

- **a real bug:** a 500 from `POST /api/start-over` racing `PUT /api/session` (both wrote `session.json` through the
  same temporary file). Fixed in 0.6: every state write gets its own temporary name.
- **missing security headers** (CSP, `X-Frame-Options`, `X-Content-Type-Options`, cross-origin policies,
  `Permissions-Policy`): the app sets them on every response since 0.6 (`SECURITY_HEADERS` in `astro_ingest/api.py`).
  The CSP allows scripts and styles from the app itself only, nothing inline: the page has no style attributes
  (`app.js` sets styles through `element.style`, which the CSP allows), checked by a test.
- **High, "Source Code Disclosure - File Inclusion" (43):** a false positive. The flagged requests were answered
  400/404 with no file content. Left at WARN because it's the rule that would catch a real path bug.
- **second scan (0.6):** the header alerts are gone. New: "SQL Injection" (40018) and "Spring4Shell" (40045), both
  false positives from timing: while ZAP's other requests had a job running, the same endpoint answered 409 ("a run
  is in progress") one moment and 400 ("bad value") the next, which ZAP read as its payload changing the result. No
  value it attacked reaches SQL (every query uses bound parameters), and Spring4Shell is a Java bug. "CSP: style-src
  unsafe-inline" (10055) is cleared: the page's inline styles moved into `styles.css` and `'unsafe-inline'` was
  dropped.
- **third scan (0.7):** only "SQL Injection" (40018) and "Source Code Disclosure" (43) left above Low, both false
  positives. Rather than ignore them, the app now gives ZAP no reason to raise them, so both rules stay on for every
  endpoint: input is checked before "a run is in progress", every path that isn't a known frame or thumbnail gets the
  same 404, and a bad answer's value isn't echoed back. Tests pin each of these down.
- **expected for this app**, set to IGNORE with reasons in `.zap/rules.tsv`: the ASIAIR's private IP, capture
  timestamps, Base64-looking names, plain HTTP (HTTPS comes from the proxy), client-error counts, and the
  informational rules.

Not something ZAP can fix: there's no authentication, by design on the LAN, which is why the deploy guide recommends
Nginx Proxy Manager with authentication in front of it.

## Tuning

- **`.zap/rules.tsv`**: one line per rule id: `IGNORE` (reviewed, doesn't apply), `WARN` (the default), or `FAIL`.
  `IGNORE` turns an active rule off, but a passive rule still runs and still appears in ZAP's reports; it just no
  longer counts as a warning or failure in the job's results.
- **One rule on one URL:** a line `<rule id>	OUTOFSCOPE	<URL regex>` ignores that rule's alerts on matching URLs only,
  so it still runs everywhere else. For a confirmed false positive the app can't avoid; prefer fixing the cause.
- **Fail the run on findings:** set `fail_action: true` in the workflow; with it, alerts make the job fail (and rules
  set to `FAIL` always do).
- **A GitHub issue with the results:** set `allow_issue_writing: true` and add `issues: write` to the workflow's
  `permissions`; ZAP then opens an issue and updates it on each run.

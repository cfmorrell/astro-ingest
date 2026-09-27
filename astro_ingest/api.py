"""FastAPI app: a JSON API plus the static frontend, in the same shape as astro-stacker.

Writes only to STATE_DIR / CACHE_DIR (answers, frame-quality stats, rendered previews), except Copy & verify and
Catalog, which write to the share through fsops, and Clean up, the only step that deletes from the device. StaticFiles is mounted last so it
never shadows an API route.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import threading
import time
from pathlib import Path, PurePosixPath

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from astro_ingest import analysis, batch, catalog, cleanup, db, jobs, service, staging, startover, state
from astro_ingest.sources import devices, discover, network
from astro_ingest.config import VERSION, Config
from astro_ingest.core import imaging

STATIC_DIR = Path(__file__).parent / "static"
PREVIEW_SIZES = (analysis.THUMB_SIZE, analysis.FULL_SIZE)
FRAME_KINDS = ("Light", "Flat", "Dark", "Bias", "DarkFlat")
SESSION_FILE = startover.SESSION_FILE   # the page's place in the flow (step, finished steps, ticks), shared by every window
LOG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.log")   # a plain file name in STATE_DIR/logs (no paths)


def _safe_rel(rel: str) -> str:
    p = PurePosixPath(rel)
    if not rel or p.is_absolute() or ".." in p.parts:
        raise HTTPException(400, "bad path")
    return rel


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="astro-ingest", version=VERSION)
    lock = threading.RLock()
    cache: dict[str, service.Planned] = {}

    def source():
        try:
            return service.open_source(cfg)
        except service.SourceUnavailable as e:
            raise HTTPException(503, str(e)) from e

    def planned(refresh: bool = False) -> service.Planned:
        with lock:
            if refresh or "plan" not in cache:
                cache["plan"] = service.scan_and_plan(cfg, source())
            return cache["plan"]

    def replan() -> service.Planned:
        with lock:
            cache["plan"] = service.replan(cfg, planned())
            return cache["plan"]

    def plan_json(p: service.Planned) -> dict:
        rate, rate_kind = service.transfer_rate(cfg)
        store = staging.StagingStore(cfg)
        last_seconds = (store.rate or {}).get("seconds")
        entries = {f.rel: f.entry for f in p.scan.frames}
        slug = getattr(p.source, "slug", None)
        staged = {i.src for i in p.plan.items if i.src in entries and slug and i.action in staging.STAGEABLE
                  and not staging.needs_staging(store, slug, entries[i.src], i.action)}
        # a staged copy on disk, whatever the answer now is: previews keep rendering from it when an answer changes
        on_disk = {i.src for i in p.plan.items if i.src in entries and slug and store.get(slug, entries[i.src])}
        out = {**p.plan.to_dict(), "scanned_at": p.scanned_at.isoformat(timespec="seconds"), "sigma": p.sigma,
               "sigma_default": service.quality.INGEST_ANOMALY_Z_THRESHOLD, "rate_mb_s": rate, "rate_kind": rate_kind, "rate_seconds": last_seconds,
               "stageable_actions": list(staging.STAGEABLE)}
        for item in out["items"]:
            item["staged"] = item["src"] in staged
            item["has_staged_copy"] = item["src"] in on_disk
        return out

    @app.get("/health")
    def health():
        try:
            src = service.open_source(cfg)
            label, online = src.label, True
        except service.SourceUnavailable as e:
            label, online = str(e), False
        return {"status": "ok", "version": VERSION, "source": label, "source_online": online}

    @app.get("/api/plan")
    def get_plan():
        return plan_json(planned())

    @app.post("/api/scan")
    def rescan():
        p = planned(refresh=True)
        return {"summary": p.plan.summary(), "scanned_at": p.scanned_at.isoformat(timespec="seconds")}

    @app.post("/api/answers")
    def post_answers(updates: dict[str, str | None] = Body(...)):
        """Merge answers ({key: value}, null removes): decision answers ({decision id: one of its options, or
        "new:<TargetFolder>"}), per-frame keep/reject ("keep:<source rel>": "keep" | "reject"), Scan-step exclusions
        ("exclude:<source rel>": "1") and the sensitivity ("quality-sigma")."""
        decisions = {d.id: d for d in planned().plan.decisions}
        for key, value in updates.items():
            ok = (key.startswith("keep:") and value in ("keep", "reject", None)) or \
                 (key.startswith("exclude:") and value in ("1", None)) or \
                 (key == analysis.SIGMA_KEY and (value is None or _is_sigma(value))) or \
                 (key in decisions and (value is None or _valid_decision_answer(decisions[key], value)))
            if not ok:
                raise HTTPException(400, f"unsupported answer {key!r}={value!r}")
        service.set_answers(cfg, updates)
        return plan_json(replan())

    # ---------------- capture devices (discovery is read-only on the devices)

    found: dict[str, list] = {}

    def client_hosts(request: Request) -> list[str]:
        # the browser's address: behind a reverse proxy it's the first X-Forwarded-For entry
        fwd = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        return fwd + ([request.client.host] if request.client else [])

    def network_for(request: Request | None) -> dict | None:
        return network.detect(cfg.asiair_subnet, client_hosts(request) if request else [],
                              [r["host"] for r in service.recent_devices(cfg)])

    def devices_json(request: Request | None = None, message: str | None = None) -> dict:
        selected = service.remembered_device(cfg)
        recent = service.recent_devices(cfg)
        known = {r["host"]: r for r in recent}
        return {
            "source_mode": "local" if cfg.asiair_root else "smb",
            "local_root": str(cfg.asiair_root) if cfg.asiair_root else None,
            "remembered": selected,
            "recent": recent,
            "found": [d.to_dict() | {"display": d.display, "known": known.get(d.host)} for d in found.get("devices", [])],
            "network": network_for(request),
            "message": message,
        }

    def connected(record: dict) -> None:
        with lock:
            cache.pop("plan", None)  # a new session on this device: scan it again

    @app.get("/api/devices")
    def get_devices(request: Request):
        return devices_json(request)

    @app.post("/api/devices/find")
    def find_devices(request: Request, subnet: str | None = Body(None, embed=True)):
        """Search a network for devices. It starts a new session: disconnect first, nothing is picked for you (every
        device found gets its own Connect button, even when there's only one)."""
        if busy():
            raise HTTPException(409, f"a {busy()} run is in progress")
        try:
            net = network.valid_subnet(subnet) if subnet else (network_for(request) or {}).get("subnet")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if not net:
            raise HTTPException(400, "which network is the device on? Enter it, e.g. 192.168.1.0/24")
        service.disconnect_device(cfg)
        with lock:
            cache.pop("plan", None)
        d = discover.discover(net, [], None, full=True)
        found["devices"] = d.devices
        n = len(d.devices)
        msg = f"Found {n} device{'s' if n != 1 else ''} on {net}." if n else f"No capture device answered on {net}. Is it powered on and on this network?"
        return devices_json(request, msg) | {"scanned": d.scanned, "seconds": d.seconds, "errors": d.errors, "subnet": net}

    @app.post("/api/devices/select")
    def select_device(request: Request, host: str = Body(...), nickname: str | None = Body(None)):
        """Connect to a device from the last search."""
        if busy():
            raise HTTPException(409, f"a {busy()} run is in progress")
        match = [d for d in found.get("devices", []) if d.host == host]
        if len(match) != 1:
            raise HTTPException(404, "no such device in the last search: search again")
        connected(service.remember_device(cfg, match[0], nickname))
        return devices_json(request)

    @app.post("/api/devices/connect")
    def connect_device(request: Request, host: str = Body(...), nickname: str | None = Body(None)):
        """Connect to a device at one address: a recent device at its last address, or an address typed in. Never
        switches to something else: if nothing (or no capture device) answers there, it says so."""
        if busy():
            raise HTTPException(409, f"a {busy()} run is in progress")
        host = host.strip()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise HTTPException(400, f"{host!r} isn't an IP address") from None
        known = next((r for r in service.recent_devices(cfg) if r["host"] == host), None)
        who = (known.get("nickname") or known.get("label")) if known else "Nothing"
        try:
            devs = devices.identify(host) if discover.port_open(host, timeout=2.0) else []
        except Exception:
            devs = []
        if not devs:
            raise HTTPException(404, f"{who} isn't answering at {host}. It may have a new address: search the network."
                                if known else f"No capture device answered at {host}.")
        connected(service.remember_device(cfg, devs[0], nickname))
        return devices_json(request)

    @app.post("/api/devices/rename")
    def rename_device(request: Request, host: str = Body(...), nickname: str = Body(...)):
        try:
            service.rename_device(cfg, host, nickname)
        except KeyError:
            raise HTTPException(404, "not a recent device") from None
        return devices_json(request, f"Renamed to “{nickname.strip()}”." if nickname.strip() else "Name cleared.")

    @app.post("/api/devices/forget")
    def forget_device(request: Request, host: str = Body(..., embed=True)):
        if busy():
            raise HTTPException(409, f"a {busy()} run is in progress")
        was = service.remembered_device(cfg)
        service.forget_device(cfg, host)
        if was and was["host"] == host:
            with lock:
                cache.pop("plan", None)
        return devices_json(request)

    # ---------------- the device's own thumbnails, for the Select grid (small, fast over Wi-Fi)

    @app.get("/api/thumb")
    def thumb(rel: str = Query(...)):
        rel = _safe_rel(rel)
        if not rel.endswith("_thn.jpg"):
            raise HTTPException(400, "not a thumbnail path")
        p = planned()
        entry = next((f.thumb for f in p.scan.frames if f.thumb and f.thumb.rel == rel), None)
        if entry is None:
            raise HTTPException(404, "no such thumbnail on the source")
        cached = cfg.cache_dir / "thumbs" / f"{hashlib.sha1(analysis.src_key(rel, entry.size, entry.mtime).encode()).hexdigest()}.jpg"
        if not cached.is_file():
            try:
                with p.source.open_read(rel) as f:
                    state.write_bytes(cfg, cached, f.read())
            except OSError as exc:
                raise HTTPException(503, f"couldn't read the thumbnail: {exc}") from exc
        return Response(cached.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    # ---------------- stage: read each selected frame once, then score from the staged copies

    @app.post("/api/stage/run")
    def run_stage():
        if busy():
            raise HTTPException(409, f"a {busy()} run is in progress")
        p = planned()
        src = p.source
        entries = {f.rel: f.entry for f in p.scan.frames}
        store = staging.StagingStore(cfg)
        todo = [entries[i.src] for i in p.plan.items
                if i.src in entries and staging.needs_staging(store, src.slug, entries[i.src], i.action)]

        def work(progress) -> dict:
            result = staging.run_staging(cfg, src.source, src.slug, todo, measure_rate=not src.local,
                                         progress=lambda pct, msg, **st: progress(pct * 0.9, msg, **st))
            src.reload()
            q = replan()
            targets = analysis.targets_by_group(cfg, q.source, q.scan, q.index, q.plan)
            staged_n = result.get("staged", 0) + result.get("already_staged", 0)
            result["quality"] = analysis.run_scoring(cfg, targets, lambda pct, msg, **st: progress(
                90 + pct * 0.1, msg, phase="scoring", staged=staged_n, **st))
            replan()
            return result

        job = jobs.create_python_job(work, Path(cfg.state_dir) / "logs" / "jobs", kind="stage")
        return {"job_id": job.id, "files": len(todo), "bytes": sum(e.size for e in todo)}

    # ---------------- copy & verify: staging -> the share (the only step that writes there)

    def preview_json(pv) -> dict:
        reasons = {}
        for n in pv.not_included:
            reasons.setdefault(n["reason"], []).append(n["src"])
        return pv.summary() | {"not_included": [{"reason": r, "count": len(v), "items": v} for r, v in reasons.items()],
                               "unfinished_batch": db.unfinished_batch(cfg)}

    @app.get("/api/copy/preview")
    def copy_preview():
        return preview_json(batch.preview(planned()))

    @app.post("/api/copy/run")
    def copy_run():
        if busy():
            raise HTTPException(409, f"a {busy()} run is in progress")
        batch_id = db.unfinished_batch(cfg)       # resume an interrupted batch before approving a new one
        if batch_id is None:
            p = planned()
            pv = batch.preview(p)
            if not pv.copy_ops:
                raise HTTPException(400, "nothing is ready to copy")
            batch_id = batch.approve(cfg, p, pv)

        def work(progress) -> dict:
            result = batch.run(cfg, batch_id, progress)
            with lock:
                cache["plan"] = service.reindex(cfg, planned())
            return result

        job = jobs.create_python_job(work, Path(cfg.state_dir) / "logs" / "jobs", kind="copy")
        return {"job_id": job.id, "batch_id": batch_id}

    @app.get("/api/batches")
    def list_batches():
        return {"batches": db.batches(cfg)}

    # ---------------- catalog: PROJECT_INFO, targets.csv, index links, notes (reads the NAS only, not the ASIAIR)

    @app.get("/api/catalog/preview")
    def catalog_preview():
        return catalog.preview(cfg).to_dict()

    @app.post("/api/catalog/run")
    def catalog_run():
        if busy():
            raise HTTPException(409, f"a {busy()} run is in progress")

        def work(progress) -> dict:
            return catalog.run(cfg, catalog.preview(cfg), progress)

        job = jobs.create_python_job(work, Path(cfg.state_dir) / "logs" / "jobs", kind="catalog")
        return {"job_id": job.id}

    # ---------------- clean up: delete from the device what's verified on the NAS, or ticked one by one

    BUSY = ("stage", "quality", "copy", "catalog", "verify", "cleanup")

    def busy() -> str | None:
        return next((k for k in BUSY if jobs.running(k)), None)

    @app.get("/api/cleanup/preview")
    def cleanup_preview():
        p = planned()
        last = db.cleanups(cfg, 1)
        return cleanup.preview(cfg, p).to_dict() | {
            "device": service.remembered_device(cfg) if not cleanup._is_local(p.source) else None,
            "unfinished": db.unfinished_cleanup(cfg), "last": last[0] if last else None}

    @app.post("/api/cleanup/verify")
    def cleanup_verify(method: str = Body("quick", embed=True)):
        if busy():
            raise HTTPException(409, f"a {busy()} run is in progress")
        if method not in cleanup.VERIFY_METHODS:
            raise HTTPException(400, f"method must be one of {cleanup.VERIFY_METHODS}")
        p = planned()

        def work(progress) -> dict:
            result = cleanup.verify(cfg, p, progress, method=method) | {"method": method}
            replan()
            return result

        job = jobs.create_python_job(work, Path(cfg.state_dir) / "logs" / "jobs", kind="verify")
        return {"job_id": job.id}

    @app.post("/api/cleanup/run")
    def cleanup_run(selected: list[str] = Body([], embed=True)):
        if busy():
            raise HTTPException(409, f"a {busy()} run is in progress")
        p = planned()
        cid = db.unfinished_cleanup(cfg)     # resume an interrupted run before approving a new one
        if cid is None:
            try:
                cid = cleanup.approve(cfg, p, selected)
            except cleanup.DeleteRefused as e:
                raise HTTPException(403, str(e)) from e
            except ValueError as e:
                raise HTTPException(400, str(e)) from e

        def work(progress) -> dict:
            result = cleanup.run(cfg, p, cid, progress)
            with lock:
                cache["plan"] = service.scan_and_plan(cfg, source())   # the device changed: scan again
            return result

        job = jobs.create_python_job(work, Path(cfg.state_dir) / "logs" / "jobs", kind="cleanup")
        return {"job_id": job.id, "cleanup_id": cid}

    # ---------------- previews (astro-stacker's rendering, cached in CACHE_DIR)

    @app.get("/api/preview")
    def preview(rel: str | None = Query(None), nas: str | None = Query(None), size: int = Query(analysis.THUMB_SIZE)):
        if size not in PREVIEW_SIZES:
            raise HTTPException(400, f"size must be one of {PREVIEW_SIZES}")
        if (rel is None) == (nas is None):
            raise HTTPException(400, "give exactly one of rel= or nas=")
        p = planned()
        if rel is not None:
            rel = _safe_rel(rel)
            frame = next((f for f in p.scan.frames if f.rel == rel), None)
            item = next((i for i in p.plan.items if i.src == rel), None)
            if frame is None or item is None or item.kind not in FRAME_KINDS:
                raise HTTPException(404, "not a frame on the source")
            key = analysis.src_key(frame.entry.rel, frame.entry.size, frame.entry.mtime)
            src = p.source   # the staged copy when there is one (a plain source() would read the device)
            if not analysis.render_path(cfg, key, size).is_file() and not getattr(src, "is_fast", lambda r: True)(rel):
                # never read a whole frame over the device's Wi-Fi just to show it (left-out frames are never read
                # at all): the page shows the device's own thumbnail instead
                raise HTTPException(409, "not staged: use the device's thumbnail")
            opener, kind = (lambda: src.open_read(rel)), item.kind
        else:
            nas = _safe_rel(nas)
            files = [f for fl in p.index.files.values() for f in fl if f.rel == nas]
            if not files or not nas.lower().endswith((".fit", ".fits")):
                raise HTTPException(404, "not a frame on the NAS")
            key, opener, kind = analysis.nas_key(nas, files[0].size), analysis._nas_opener(cfg, nas), "Light"
        cached = analysis.render_path(cfg, key, size)
        if cached.is_file():
            return Response(cached.read_bytes(), media_type="image/png", headers={"Cache-Control": "max-age=86400"})
        try:
            with opener() as fh:
                data, header = imaging.load_fits(fh)
            png = imaging.render_array(data, header, max_size=size, stretch=imaging.stretch_for_kind(kind),
                                       debayer=kind == "Light" and "BAYERPAT" in header)
        except Exception as exc:
            raise HTTPException(422, f"could not render preview: {exc}") from exc
        state.write_bytes(cfg, cached, png)
        return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=86400"})

    # ---------------- frame-quality scoring job

    @app.post("/api/quality/run")
    def run_quality():
        if jobs.running("quality"):
            raise HTTPException(409, "frame scoring is already running")
        p = planned()
        targets = analysis.targets_by_group(cfg, p.source, p.scan, p.index, p.plan)

        def work(progress) -> dict:
            result = analysis.run_scoring(cfg, targets, progress)
            replan()
            return result

        job = jobs.create_python_job(work, Path(cfg.state_dir) / "logs" / "jobs", kind="quality")
        return {"job_id": job.id}

    # ---------------- jobs (same shape as astro-stacker)

    @app.get("/jobs")
    def list_jobs():
        return {"jobs": [j.snapshot() for j in jobs.list_all_jobs()]}

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str):
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job.snapshot()

    @app.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
    def job_log(job_id: str):
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job.log_path.read_text() if job.log_path.exists() else ""

    # ---------------- the session (proposal K): where the flow is, kept on the server so any window picks it up

    session_path = Path(cfg.state_dir) / SESSION_FILE

    @app.get("/api/session")
    def get_session():
        return state.read_json(session_path, {})

    @app.put("/api/session")
    def put_session(request: Request, data: dict = Body(...), client: str = Body("")):
        if len(json.dumps(data)) > 4 << 20:
            raise HTTPException(413, "session too large")
        record = {"data": data, "client": client, "updated_at": time.time()}
        state.write_json(cfg, session_path, record)
        return {"updated_at": record["updated_at"]}

    # ---------------- start over (proposal J)

    @app.get("/api/start-over")
    def start_over_preview():
        return startover.preview(cfg) | {"busy": busy()}

    @app.post("/api/start-over")
    def start_over(forget_answers: bool = Body(True, embed=True)):
        if busy():
            raise HTTPException(409, f"a {busy()} run is in progress: wait for it to finish")
        try:
            result = startover.run(cfg, forget_answers)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        with lock:
            cache.pop("plan", None)
        found.pop("devices", None)
        return result

    # ---------------- logs (linked from the results of Copy & verify, Catalog and Clean up)

    @app.get("/api/logs/{name}")
    @app.get("/api/logs/jobs/{name}")
    def log_file(name: str, request: Request):
        if not LOG_NAME.fullmatch(name):
            raise HTTPException(404, "no such log")
        folder = Path(cfg.state_dir) / "logs" / ("jobs" if "/logs/jobs/" in request.url.path else "")
        path = folder / name
        if not path.is_file():
            raise HTTPException(404, "no such log")
        return Response(path.read_bytes(), media_type="text/plain; charset=utf-8")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def _is_sigma(value: str) -> bool:
    try:
        return 1.0 <= float(value) <= 10.0
    except ValueError:
        return False


# A target folder as the share names them: CamelCase name, then catalog parts ("NeedleGalaxy-NGC4565",
# "GhostNebula-Sh2-136", "MarkariansChain"); no spaces, apostrophes, & or parentheses (ORGANIZATION_GUIDE §3).
_TARGET_FOLDER = re.compile(r"^[A-Z][A-Za-z0-9]*(-[A-Za-z0-9]+)*$")


def _valid_decision_answer(decision, value: str) -> bool:
    options = [o["value"] for o in decision.options]
    if value in options:
        return True
    return value.startswith("new:") and any(o.startswith("new:") for o in options) and \
        bool(_TARGET_FOLDER.match(value[4:]))

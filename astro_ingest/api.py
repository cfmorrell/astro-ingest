"""FastAPI app: a JSON API plus the static frontend, in the same shape as astro-stacker.

Writes only to STATE_DIR / CACHE_DIR (answers, frame-quality stats, rendered previews). Nothing on the NAS or the
ASIAIR is touched until the copy (phase 4) and cleanup (phase 6) steps exist. StaticFiles is mounted last so it
never shadows an API route.
"""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path, PurePosixPath

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from astro_ingest import analysis, jobs, service, staging, state
from astro_ingest.sources import devices, discover
from astro_ingest.config import VERSION, Config
from astro_ingest.core import imaging

STATIC_DIR = Path(__file__).parent / "static"
PREVIEW_SIZES = (analysis.THUMB_SIZE, analysis.FULL_SIZE)
FRAME_KINDS = ("Light", "Flat", "Dark", "Bias", "DarkFlat")


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
        entries = {f.rel: f.entry for f in p.scan.frames}
        slug = getattr(p.source, "slug", None)
        staged = {i.src for i in p.plan.items if i.src in entries and slug and i.action in staging.STAGEABLE
                  and not staging.needs_staging(store, slug, entries[i.src], i.action)}
        out = {**p.plan.to_dict(), "scanned_at": p.scanned_at.isoformat(timespec="seconds"), "sigma": p.sigma,
               "sigma_default": service.quality.INGEST_ANOMALY_Z_THRESHOLD, "rate_mb_s": rate, "rate_kind": rate_kind,
               "stageable_actions": list(staging.STAGEABLE)}
        for item in out["items"]:
            item["staged"] = item["src"] in staged
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

    def devices_json(choice=None) -> dict:
        remembered = service.remembered_device(cfg)
        return {
            "source_mode": "local" if cfg.asiair_root else "smb",
            "local_root": str(cfg.asiair_root) if cfg.asiair_root else None,
            "remembered": remembered,
            "found": [d.to_dict() | {"display": d.display, "remembered": devices.is_remembered(d, remembered)}
                      for d in found.get("devices", [])],
            "choice": None if choice is None else {"status": choice.status, "message": choice.message,
                                                   "host": choice.device.host if choice.device else None},
            "subnet": cfg.asiair_subnet,
        }

    @app.get("/api/devices")
    def get_devices():
        return devices_json()

    @app.post("/api/devices/find")
    def find_devices(full: bool = Body(False, embed=True)):
        remembered = service.remembered_device(cfg)
        hints = [h for h in ([remembered["host"]] if remembered else []) + [cfg.asiair_host] if h]
        d = discover.discover(cfg.asiair_subnet, hints, remembered, full=full)
        found["devices"] = d.devices
        choice = devices.choose(d.devices, remembered)
        if choice.status == "only-one":
            service.remember_device(cfg, choice.device)
        return devices_json(choice) | {"scanned": d.scanned, "seconds": d.seconds, "errors": d.errors}

    @app.post("/api/devices/select")
    def select_device(host: str = Body(...), nickname: str | None = Body(None)):
        match = [d for d in found.get("devices", []) if d.host == host]
        if len(match) != 1:
            raise HTTPException(404, "no such device in the last search: run Find again")
        service.remember_device(cfg, match[0], (nickname or "").strip() or None)
        with lock:
            cache.pop("plan", None)  # the source may have changed
        return devices_json()

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
        if jobs.running("stage") or jobs.running("quality"):
            raise HTTPException(409, "staging or scoring is already running")
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
            result["quality"] = analysis.run_scoring(cfg, targets, lambda pct, msg: progress(90 + pct * 0.1, msg,
                                                                                               phase="scoring"))
            replan()
            return result

        job = jobs.create_python_job(work, Path(cfg.state_dir) / "logs" / "jobs", kind="stage")
        return {"job_id": job.id, "files": len(todo), "bytes": sum(e.size for e in todo)}

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
            opener, kind = (lambda: source().open_read(rel)), item.kind
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

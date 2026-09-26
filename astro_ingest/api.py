"""FastAPI app: a JSON API plus the static frontend, in the same shape as astro-stacker.

Writes only to STATE_DIR / CACHE_DIR (answers, frame-quality stats, rendered previews). Nothing on the NAS or the
ASIAIR is touched until the copy (phase 4) and cleanup (phase 6) steps exist. StaticFiles is mounted last so it
never shadows an API route.
"""

from __future__ import annotations

import threading
from pathlib import Path, PurePosixPath

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from astro_ingest import analysis, jobs, service, state
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
        return {**p.plan.to_dict(), "scanned_at": p.scanned_at.isoformat(timespec="seconds"), "sigma": p.sigma,
                "sigma_default": service.quality.INGEST_ANOMALY_Z_THRESHOLD}

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
        """Merge answers ({key: value}, null removes). Phase 2b uses it for per-frame keep/reject
        ("keep:<source rel>": "keep" | "reject") and the sensitivity ("quality-sigma")."""
        for key, value in updates.items():
            ok = (key.startswith("keep:") and value in ("keep", "reject", None)) or \
                 (key == analysis.SIGMA_KEY and (value is None or _is_sigma(value)))
            if not ok:
                raise HTTPException(400, f"unsupported answer {key!r}={value!r} in this phase")
        service.set_answers(cfg, updates)
        return plan_json(replan())

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

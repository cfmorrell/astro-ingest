"""FastAPI app: a JSON API plus the static frontend, in the same shape as astro-stacker.

Phase 2 is read-only: the app scans the source, plans, and shows the plan. Nothing is written anywhere.
StaticFiles is mounted last so it never shadows an API route.
"""

from __future__ import annotations

import threading
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from astro_ingest import service
from astro_ingest.config import VERSION, Config
from astro_ingest.core.asiair import THUMB_SUFFIX

STATIC_DIR = Path(__file__).parent / "static"


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="astro-ingest", version=VERSION)
    lock = threading.Lock()
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
        p = planned()
        return {**p.plan.to_dict(), "scanned_at": p.scanned_at.isoformat(timespec="seconds")}

    @app.post("/api/scan")
    def rescan():
        p = planned(refresh=True)
        return {"summary": p.plan.summary(), "scanned_at": p.scanned_at.isoformat(timespec="seconds")}

    @app.get("/api/thumb")
    def thumb(rel: str = Query(...)):
        # Only ASIAIR thumbnails, only by a relative path inside the source
        if not rel.endswith(THUMB_SUFFIX) or PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
            raise HTTPException(400, "not a thumbnail path")
        try:
            with source().open_read(rel) as f:
                data = f.read()
        except (OSError, ValueError) as e:
            raise HTTPException(404, "thumbnail not found") from e
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app

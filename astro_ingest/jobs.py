"""Minimal in-memory background job manager, in the same shape as astro-stacker's app/jobs.py (commit f31cbcb).

Only stacker's Python-callable job type is ported (astro-ingest never runs Siril). Job snapshots have the same
fields, so the frontend's pollJob() and active-jobs panel work the same way in both apps. In-memory, single
process: a restart forgets job history, which is fine because every job's durable result (frame stats, copies)
is written to STATE_DIR by the work itself.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional


@dataclass
class Job:
    id: str
    log_path: Path
    kind: str = ""
    status: str = "pending"  # pending -> running -> succeeded | failed
    return_code: Optional[int] = None
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    current_line: str = ""
    current_command: Optional[str] = None
    percent_complete: Optional[float] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    steps: list = field(default_factory=list)
    current_step_index: Optional[int] = None
    project: str = ""  # stacker's per-project key; astro-ingest has one "project" (the source), kept for shape
    stats: dict = field(default_factory=dict)  # astro-ingest addition: structured progress (e.g. files/bytes/eta)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id, "project": self.project, "kind": self.kind, "status": self.status,
                "return_code": self.return_code, "started_at": self.started_at, "ended_at": self.ended_at,
                "current_command": self.current_command, "percent_complete": self.percent_complete,
                "current_line": self.current_line, "steps": self.steps,
                "current_step_index": self.current_step_index, "result": self.result, "error": self.error,
                "stats": dict(self.stats),
            }


_jobs: Dict[str, Job] = {}
_registry_lock = threading.Lock()


def running(kind: str | None = None) -> Optional[Job]:
    """A job that is currently running (of this kind, if given), so a second one can be refused."""
    with _registry_lock:
        return next((j for j in _jobs.values() if j.status in ("pending", "running")
                     and (kind is None or j.kind == kind)), None)


def list_all_jobs() -> list[Job]:
    with _registry_lock:
        jobs = list(_jobs.values())
    return sorted(jobs, key=lambda j: j.started_at or 0, reverse=True)


def get_job(job_id: str) -> Optional[Job]:
    with _registry_lock:
        return _jobs.get(job_id)


def create_python_job(work: Callable[..., dict], log_dir: Path, kind: str = "", project: str = "") -> Job:
    """Run work(progress) in a background thread. work() calls progress(percent, message, **stats) as it goes and
    returns a dict stored as job.result. Each progress message is also appended to the job's log file; the optional
    keyword stats (numbers for the UI, e.g. files_done, eta_s) are kept on the job snapshot."""
    job_id = uuid.uuid4().hex[:12]
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{job_id}.log"
    log_path.write_text(f"=== job {job_id} ({kind or 'job'}) ===\n"
                        f"started: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n")
    job = Job(id=job_id, log_path=log_path, kind=kind, project=project)
    with _registry_lock:
        _jobs[job_id] = job

    def progress(percent: float, message: str, **stats) -> None:
        with job._lock:
            job.percent_complete = percent
            job.current_line = message
            job.stats.update(stats)
        with log_path.open("a") as f:
            f.write(f"{message}\n")

    def _run() -> None:
        with job._lock:
            job.status = "running"
            job.started_at = time.time()
        try:
            result = work(progress)
            with job._lock:
                job.result, job.status, job.return_code = result, "succeeded", 0
        except Exception as exc:  # the job reports it; the server keeps running
            with job._lock:
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
            with log_path.open("a") as f:
                f.write(f"FAILED: {type(exc).__name__}: {exc}\n")
        finally:
            with job._lock:
                job.ended_at = time.time()
                if job.status == "succeeded":
                    job.percent_complete = 100.0

    threading.Thread(target=_run, daemon=True, name=f"job-{job_id}").start()
    return job

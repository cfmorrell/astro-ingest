"""Copy batches: the approved list of operations from staging onto the NAS, and running it.

A batch is a snapshot taken on approval: every operation (retire a damaged NAS copy, copy a staged frame to a
destination) with the size and BLAKE2b it must have. Running it always executes exactly that list, even if the plan
has moved on since. Everything is recorded in SQLite (db.py) and as plain text: STATE_DIR/batches/<id>.tsv (the
list) and STATE_DIR/logs/copy-<id>.log (one OK/SKIP/FAIL line per operation, in the Z95 plans/*.log style).
"""

from __future__ import annotations

import datetime as dt
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from astro_ingest import db, fsops, staging
from astro_ingest.config import Config
from astro_ingest.core import planner as P

COPYABLE = (P.COPY, P.APPEND)
CLEAR_AFTER = (P.REJECTED, P.EXCLUDED, P.NOT_KEPT, P.OVER_CAP, P.NO_LIGHTS, P.SKIP)  # staged but not going to the NAS


@dataclass
class Preview:
    ops: list[dict]
    destinations: list[dict]                       # [{folder, files, bytes}]
    not_included: list[dict]                       # [{src, reason}]
    clear_after: list[str] = field(default_factory=list)  # staged copies to clear when the batch finishes

    @property
    def copy_ops(self) -> list[dict]:
        return [o for o in self.ops if o["kind"] == "copy"]

    def summary(self) -> dict:
        return {"copies": len(self.copy_ops), "retires": len(self.ops) - len(self.copy_ops),
                "bytes": sum(o["size"] for o in self.copy_ops), "destinations": self.destinations,
                "not_included": len(self.not_included)}


def _dest_folder(dst: str) -> str:
    """The session or library batch a destination belongs to (drop lights/flats sub-folders and the file)."""
    parts = dst.split("/")[:-1]
    if parts and parts[-1].split("-")[0] in ("lights", "flats", "darkflats"):
        parts = parts[:-1]
    return "/".join(parts)


def preview(planned) -> Preview:
    """What approving now would copy, and what isn't included and why."""
    src = planned.source
    store = staging.StagingStore(src.cfg) if hasattr(src, "cfg") else None
    entries = {f.rel: f.entry for f in planned.scan.frames}
    ops, not_included, clear_after = [], [], []
    per_dest: dict[str, list[int]] = defaultdict(list)
    for it in planned.plan.items:
        rec = store.get(src.slug, entries[it.src]) if store and it.src in entries else None
        if it.action in CLEAR_AFTER and rec:
            clear_after.append(str(store.staged_path(src.slug, it.src)))
        if it.action == P.PENDING:
            not_included.append({"src": it.src, "reason": "waits on an open decision"})
            continue
        if it.action not in COPYABLE or not it.dsts:
            continue
        if rec is None:
            not_included.append({"src": it.src, "reason": "not staged yet"})
            continue
        for old in it.retire:
            ops.append({"kind": "retire", "src": it.src, "staged": None, "dst": old, "size": None, "blake2b": None})
        for dst in it.dsts:
            ops.append({"kind": "copy", "src": it.src, "staged": str(store.staged_path(src.slug, it.src)), "dst": dst,
                        "size": rec["size"], "blake2b": rec["blake2b"]})
            per_dest[_dest_folder(dst)].append(rec["size"])
    destinations = [{"folder": k, "files": len(v), "bytes": sum(v)} for k, v in sorted(per_dest.items())]
    return Preview(ops, destinations, not_included, clear_after)


def approve(cfg: Config, planned, pv: Preview) -> str:
    """Record the batch (the exact operation list) and its plain-text copy. Returns the batch id."""
    batch_id = dt.datetime.now(cfg.tz).strftime("%Y%m%d-%H%M%S")
    summary = pv.summary() | {"clear_after": pv.clear_after}
    db.create_batch(cfg, batch_id, planned.source.label, pv.ops, summary)
    tsv = Path(cfg.state_dir) / "batches" / f"{batch_id}.tsv"
    cfg.check_writable(tsv)
    tsv.parent.mkdir(parents=True, exist_ok=True)
    lines = ["kind\tsrc\tstaged\tdst\tsize\tblake2b"] + [
        f"{o['kind']}\t{o['src']}\t{o['staged'] or ''}\t{o['dst']}\t{o['size'] or ''}\t{o['blake2b'] or ''}"
        for o in pv.ops]
    tsv.write_text("\n".join(lines) + "\n")
    return batch_id


def run(cfg: Config, batch_id: str, progress: Callable[..., None]) -> dict:
    """Execute a batch (resumable: finished operations are skipped). Verified copies clear their staged file."""
    log_path = Path(cfg.state_dir) / "logs" / f"copy-{batch_id}.log"
    cfg.check_writable(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ops = db.operations(cfg, batch_id)
    summary = db.batch(cfg, batch_id)["summary"]
    copies = [o for o in ops if o["kind"] == "copy"]
    total = sum(o["size"] or 0 for o in copies if o["status"] == "pending")
    done_bytes, started = 0, time.monotonic()
    root = Path(cfg.astro_root)
    folders = sorted({str(Path(o["dst"]).parent) for o in copies})
    before = {f: fsops.count_files(root / f) for f in folders}
    remaining_per_staged = Counter(o["staged"] for o in copies if o["status"] not in ("done", "already-there"))

    def report(n: int, message: str) -> None:
        elapsed = time.monotonic() - started
        rate = done_bytes / elapsed / 1e6 if elapsed > 1 and done_bytes else None
        eta = (total - done_bytes) / 1e6 / rate if rate else None
        progress(done_bytes / total * 100.0 if total else 100.0, message, files_done=n, files_total=len(copies),
                 bytes_done=done_bytes, bytes_total=total, mb_s=round(rate, 2) if rate else None,
                 eta_s=round(eta) if eta is not None else None)

    def on_bytes(k: int) -> None:
        nonlocal done_bytes
        done_bytes += k

    db.set_batch(cfg, batch_id, "running")
    cleared_bytes = 0
    with fsops.WriteLock(cfg, f"copy batch {batch_id}"), open(log_path, "a") as log:
        n_copy = sum(1 for o in copies if o["status"] in ("done", "already-there"))
        for o in ops:
            if o["status"] != "pending":
                continue
            t0 = time.time()
            if o["kind"] == "retire":
                status, detail = fsops.retire(cfg, o["dst"])
            else:
                report(n_copy, f"copying {o['dst']} ({n_copy + 1}/{len(copies)})")
                status, detail = fsops.copy_verified(cfg, Path(o["staged"]), o["dst"], o["size"], o["blake2b"], on_bytes)
                n_copy += 1
            db.set_operation(cfg, o["id"], status, detail, t0)
            tag = {"done": "OK", "already-there": "OK", "skipped": "SKIP", "clash": "SKIP"}.get(status, "FAIL")
            log.write(f"{tag}\t{o['kind']}\t{o['src']}\t{o['dst']}\t{status}: {detail}\n")
            log.flush()
            if o["kind"] == "copy" and status in ("done", "already-there"):
                remaining_per_staged[o["staged"]] -= 1
                if remaining_per_staged[o["staged"]] == 0:   # every destination of this frame verified
                    cleared_bytes += _clear_staged(cfg, o["staged"])
        report(n_copy, "finished copying")
        for p in summary.get("clear_after", []):   # staged but not going to the NAS: rejected, left out, ...
            cleared_bytes += _clear_staged(cfg, p)

    ops = db.operations(cfg, batch_id)
    after = {f: fsops.count_files(root / f) for f in folders}
    added = Counter(str(Path(o["dst"]).parent) for o in ops if o["kind"] == "copy" and o["status"] == "done")
    count_problems = [f"{f}: {before[f]} files before, {after[f]} after, {added[f]} copied"
                      for f in folders if after[f] != before[f] + added[f]]
    by_status = Counter(o["status"] for o in ops if o["kind"] == "copy")
    result = {
        "batch": batch_id, "copied": by_status["done"], "already_there": by_status["already-there"],
        "clashes": [{"dst": o["dst"], "detail": o["detail"]} for o in ops if o["status"] == "clash"],
        "failed": [{"dst": o["dst"], "detail": o["detail"]} for o in ops if o["status"] == "failed"],
        "retired": [{"dst": o["dst"], "status": o["status"], "detail": o["detail"]} for o in ops if o["kind"] == "retire"],
        "count_problems": count_problems, "bytes": done_bytes, "staging_cleared_bytes": cleared_bytes,
        "seconds": round(time.monotonic() - started, 1), "log": str(log_path),
    }
    ok = not result["failed"] and not count_problems
    db.set_batch(cfg, batch_id, "done" if ok else "failed", summary | {"result": result})
    return result


def _clear_staged(cfg: Config, staged: str) -> int:
    """Delete a staged copy (staging is the app's scratch share, outside the Astronomy share). Returns bytes freed."""
    p = Path(staged)
    if not p.is_file() or not p.resolve().is_relative_to(Path(cfg.staging_dir).resolve()):
        return 0
    size = p.stat().st_size
    p.unlink()
    return size

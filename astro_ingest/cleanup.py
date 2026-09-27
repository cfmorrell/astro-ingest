"""The Clean up step: delete from the capture device what is safely on the NAS, and what Chris approves one by one.

This is the only irreversible thing the app does, so every delete passes these gates (PLAN phase 6):
1. The frame is proven to be on the NAS: a NAS copy has the same BLAKE2b as the device file. Either the app copied
   it (the hash taken when staging read it from the device, checked again at the NAS by Copy & verify), or the
   Verify job read the device file and hashed both (frames that were on the NAS before, decision 6).
   Files that were never copied (rejected, left out, beyond the cap, other files) are called out and need their
   own tick; flats without lights stay blocked (decision 10).
2. Chris ticked it; the approved list is a snapshot (SQLite + cleanups/<id>.tsv), and only that list is deleted.
3. Right before the run the device is listed again: every file must still have the size and mtime it was scanned
   with, and if the device wrote anything in the last 15 minutes (capturing?) the run doesn't start.
4. Right before each delete, the frame's NAS copies are hashed again and must still match.
5. Deletes only under Autorun/ and Plan/ (sources/base.check_deletable), and from a real device over SMB only when
   ALLOW_DEVICE_DELETE=1. Each .fit goes with its _thn.jpg (decision 9); empty object folders are removed.
After the run the device is listed again and compared: exactly the deleted files must be gone.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from astro_ingest import db, fsops
from astro_ingest.config import Config
from astro_ingest.core import asiair
from astro_ingest.core import planner as P
from astro_ingest.sources.base import DeleteRefused, check_deletable
from astro_ingest.staging import CHUNK, StagingStore

RECENT_S = 15 * 60

# group id -> (label, selectable, recommended, note). Nothing is ticked by default (Chris, 2026-09-27): recommended
# groups are marked as such and ticked with one click.
GROUPS = {
    "verified": ("On the NAS, checksum verified", True, True, ""),
    "to-verify": ("On the NAS, not checked yet", False, False, "copied to the NAS before astro-ingest, so there's no "
                  "checksum from the copy: a check compares each one with its NAS copy"),
    "differs": ("NAS copy differs", False, False, "same name and size, different content: not offered for deletion"),
    "not-copied": ("Not copied yet", False, False, "copy these first (Copy & verify)"),
    "rejected": ("Rejected for quality", True, False, "not on the NAS: deleting loses them"),
    "left-out": ("Left out on Select", True, False, "not on the NAS: deleting loses them"),
    "over-cap": ("Beyond the 10-frame cap", True, False, "not on the NAS (the first 10 of the set are)"),
    "not-kept": ("Not kept by the rules", True, False, "not on the NAS"),
    "other": ("Other files", True, False, "not written by the ASIAIR's capture"),
    "orphan-thumb": ("Orphan thumbnails", True, False, "thumbnails whose .fit is gone"),
    "blocked": ("Flats without lights", False, False, "kept until they're filed or released for deletion on Review"),
    "waiting": ("Waiting on a decision", False, False, "answer it in Review"),
    "never": ("Never touched", False, False, "Live, Preview, Video, log, … and files left on the ASIAIR on Review"),
}
CALLOUT_GROUP = {P.REJECTED: "rejected", P.EXCLUDED: "left-out", P.OVER_CAP: "over-cap", P.NOT_KEPT: "not-kept",
                 P.UNRECOGNIZED: "other", P.ORPHAN_THUMB: "orphan-thumb", P.NO_LIGHTS: "blocked",
                 P.PENDING: "waiting", P.IGNORED: "never", P.SKIP: "never", P.COPY: "not-copied",
                 P.APPEND: "not-copied"}


@dataclass
class Candidate:
    rel: str
    size: int
    mtime: float
    group: str
    kind: str                          # frame | file (a thumbnail or other file on its own)
    thumb: tuple[str, int, float] | None = None
    nas: list[list[str]] = field(default_factory=list)   # [[nas rel, blake2b], ...]
    how: str = ""                      # how it was verified / why it's in its group

    @property
    def bytes(self) -> int:
        return self.size + (self.thumb[1] if self.thumb else 0)

    def to_dict(self) -> dict:
        return {"rel": self.rel, "size": self.bytes, "group": self.group, "thumb": self.thumb[0] if self.thumb else None,
                "nas": [n[0] for n in self.nas], "how": self.how}


@dataclass
class Preview:
    source: str
    device_delete_allowed: bool
    candidates: list[Candidate]
    uncatalogued: int = 0

    def groups(self) -> list[dict]:
        by = defaultdict(list)
        for c in self.candidates:
            by[c.group].append(c)
        out = []
        for gid, (label, selectable, recommended, note) in GROUPS.items():
            cs = by.get(gid, [])
            if cs:
                out.append({"id": gid, "label": label, "selectable": selectable, "recommended": recommended,
                            "ticked": False, "note": note,
                            "files": len(cs) + sum(1 for c in cs if c.thumb), "bytes": sum(c.bytes for c in cs),
                            "items": [c.to_dict() for c in cs] if gid != "never" else []})
        return out

    def to_dict(self) -> dict:
        gs = self.groups()
        return {"source": self.source, "device_delete_allowed": self.device_delete_allowed,
                "uncatalogued_batches": self.uncatalogued, "groups": gs,
                "to_verify": {"files": sum(1 for c in self.candidates if c.group == "to-verify"),
                              "bytes": sum(c.size for c in self.candidates if c.group == "to-verify")}}


def _is_local(source) -> bool:
    return bool(getattr(source, "local", False)) or source.label.startswith("local:")


def delete_allowed(cfg: Config, source) -> bool:
    return _is_local(source) or cfg.allow_device_delete


def _device_hashes(cfg: Config, planned) -> dict[str, str]:
    """{device path: BLAKE2b} for files whose hash is known from reading them (staging or Verify), for the exact
    size and mtime the scan saw."""
    slug = planned.source.slug
    store = StagingStore(cfg)
    out = {}
    for f in planned.scan.frames:
        if store.seen(slug, f.entry):
            out[f.rel] = store.files[store.key(slug, f.rel)]["blake2b"]
    return out


def preview(cfg: Config, planned) -> Preview:
    slug = planned.source.slug
    frames = {f.rel: f for f in planned.scan.frames}
    extra = {e.rel: e for e in list(planned.scan.orphan_thumbs) + list(planned.scan.unrecognized)}
    hashes = _device_hashes(cfg, planned)
    ver = db.verified_records(cfg, slug)
    copies = db.copied_frames(cfg, planned.source.label)
    out = []
    for it in planned.plan.items:
        f = frames.get(it.src)
        e = f.entry if f else extra.get(it.src)
        if e is None:
            continue
        thumb = (f.thumb.rel, f.thumb.size, f.thumb.mtime) if f and f.thumb else None
        c = Candidate(it.src, e.size, e.mtime, "never", "frame" if f else "file", thumb, how=it.reason)
        if asiair.folder_category(it.src) != "handled":
            c.group = "never"
        elif it.action == P.ALREADY:
            v = ver.get(it.src)
            fresh = v and v["size"] == e.size and v["mtime"] == int(e.mtime)
            done = [x for x in copies.get(it.src, []) if x["size"] == e.size and x["blake2b"] == hashes.get(it.src)]
            if fresh and v["result"] == "match":
                quick = v["device_blake2b"].startswith(fsops.QUICK_PREFIX)
                c.group, c.nas = "verified", [[v["nas_rel"], v["device_blake2b"]]]
                c.how = f"matches the NAS copy ({'quick check' if quick else 'thorough check'})"
            elif fresh:
                c.group, c.how = "differs", f"{v['result']}: {v['nas_rel'] or 'no NAS copy found'}"
            elif done:
                c.group, c.nas = "verified", [[x["dst"], x["blake2b"]] for x in done]
                c.how = f"copied and verified by batch {done[0]['batch']}"
            else:
                c.group, c.how = "to-verify", "on the NAS (same name and size): " + ", ".join(it.ingested_at[:2])
        else:
            c.group = CALLOUT_GROUP.get(it.action, "never")
        out.append(c)
    return Preview(planned.source.label, delete_allowed(cfg, planned.source), out, len(db.uncatalogued_batches(cfg)))


# ---------------------------------------------------------------- Verify (decision 6)

def _hash_stream(fh, on_bytes=None) -> tuple[str, int]:
    h, n = hashlib.blake2b(), 0
    while chunk := fh.read(CHUNK):
        h.update(chunk)
        n += len(chunk)
        if on_bytes:
            on_bytes(len(chunk))
    return h.hexdigest(), n


VERIFY_METHODS = ("quick", "thorough")


def verify(cfg: Config, planned, progress: Callable[..., None] = lambda *a, **k: None, method: str = "quick") -> dict:
    """Compare each already-ingested frame with its NAS copy (decision 6). `quick` (the default) reads the size, the
    FITS header and 8 slices of each file on both sides (fsops.quick_digest); `thorough` reads every byte and compares
    BLAKE2b checksums. A frame whose full hash is already known from staging is compared in full either way. Results
    are kept per device path, size and mtime. Stops at a read error."""
    if method not in VERIFY_METHODS:
        raise ValueError(f"unknown check {method!r}")
    pv = preview(cfg, planned)
    todo = [c for c in pv.candidates if c.group == "to-verify"]
    items = {i.src: i for i in planned.plan.items}
    known = _device_hashes(cfg, planned)
    per_file = lambda size: size if method == "thorough" else sum(n for _, n in fsops.quick_offsets(size))  # noqa: E731
    total = sum(per_file(c.size) for c in todo if c.rel not in known)
    started, done_bytes = time.monotonic(), 0
    counts = Counter()

    def on_bytes(k: int) -> None:
        nonlocal done_bytes
        done_bytes += k

    for n, c in enumerate(sorted(todo, key=lambda c: c.rel), 1):
        elapsed = time.monotonic() - started
        rate = done_bytes / elapsed / 1e6 if elapsed > 1 and done_bytes else None
        progress(done_bytes / total * 100 if total else n / len(todo) * 100, f"verifying {c.rel} ({n}/{len(todo)})",
                 files_done=n - 1, files_total=len(todo), bytes_done=done_bytes, bytes_total=total,
                 mb_s=round(rate, 2) if rate else None,
                 eta_s=round((total - done_bytes) / 1e6 / rate) if rate else None)
        dev = known.get(c.rel)
        if dev is None:
            try:
                with planned.source.open_read(c.rel) as fh:
                    if method == "thorough":
                        dev, got = _hash_stream(fh, on_bytes)
                    else:
                        dev, got = fsops.quick_digest(fh, c.size, on_bytes), c.size
            except OSError as e:
                counts["read-error"] += 1
                return {"verified": counts["match"], "mismatch": counts["mismatch"], "nas_missing": counts["nas-missing"],
                        "stopped": f"reading {c.rel} failed: {e}", "bytes": done_bytes,
                        "seconds": round(time.monotonic() - started, 1)}
            if got != c.size:
                counts["read-error"] += 1
                continue
        result, nas_rel = "nas-missing", None
        for cand in items[c.rel].ingested_at:
            p = fsops.nas_path(cfg, cand)
            if p is None:
                continue
            nas_rel = cand
            if fsops.digest_matches(p, dev):
                result = "match"
                break
            result = "mismatch"
        db.record_verified(cfg, planned.source.slug, c.rel, c.size, c.mtime, dev, nas_rel, result)
        counts[result] += 1
    return {"verified": counts["match"], "mismatch": counts["mismatch"], "nas_missing": counts["nas-missing"],
            "read_errors": counts["read-error"], "bytes": done_bytes, "seconds": round(time.monotonic() - started, 1)}


# ---------------------------------------------------------------- approve and run

def approve(cfg: Config, planned, selected: list[str]) -> str:
    """Snapshot the ticked files (only from selectable groups). Returns the clean-up id."""
    if not delete_allowed(cfg, planned.source):
        raise DeleteRefused("deleting from the device is switched off (ALLOW_DEVICE_DELETE is not 1)")
    pv = preview(cfg, planned)
    by_rel = {c.rel: c for c in pv.candidates}
    ops = []
    for rel in dict.fromkeys(selected):
        c = by_rel.get(rel)
        if c is None or not GROUPS[c.group][1]:
            raise ValueError(f"not offered for deletion: {rel} ({GROUPS[c.group][0] if c else 'unknown'})")
        check_deletable(rel)
        ops.append({"kind": c.kind, "rel": c.rel, "size": c.size, "mtime": c.mtime, "group": c.group, "nas": c.nas})
        if c.thumb:
            check_deletable(c.thumb[0])
            ops.append({"kind": "thumb", "rel": c.thumb[0], "size": c.thumb[1], "mtime": c.thumb[2], "group": c.group})
    if not ops:
        raise ValueError("nothing selected")
    cid = base = dt.datetime.now(cfg.tz).strftime("%Y%m%d-%H%M%S")
    taken = {c["id"] for c in db.cleanups(cfg, 5)}
    n = 2
    while cid in taken:
        cid, n = f"{base}-{n}", n + 1
    groups = Counter(o["group"] for o in ops)
    summary = {"files": len(ops), "bytes": sum(o["size"] for o in ops), "groups": dict(groups)}
    db.create_cleanup(cfg, cid, planned.source.label, ops, summary)
    tsv = Path(cfg.state_dir) / "cleanups" / f"{cid}.tsv"
    cfg.check_writable(tsv)
    tsv.parent.mkdir(parents=True, exist_ok=True)
    tsv.write_text("kind\trel\tsize\tmtime\tgroup\tnas\n" + "".join(
        f"{o['kind']}\t{o['rel']}\t{o['size']}\t{o['mtime']}\t{o['group']}\t"
        f"{';'.join(n[0] for n in o.get('nas') or [])}\n" for o in ops))
    return cid


def _listing(source) -> dict[str, tuple[int, float]]:
    return {e.rel: (e.size, e.mtime) for e in source.walk()}


def run(cfg: Config, planned, cleanup_id: str, progress: Callable[..., None] = lambda *a, **k: None,
        now: float | None = None) -> dict:
    source = planned.source
    if not delete_allowed(cfg, source):
        raise DeleteRefused("deleting from the device is switched off (ALLOW_DEVICE_DELETE is not 1)")
    log_path = Path(cfg.state_dir) / "logs" / f"cleanup-{cleanup_id}.log"
    cfg.check_writable(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    progress(0, "listing the device")
    before = _listing(source)
    now = time.time() if now is None else now
    recent = sorted(r for r, (_, m) in before.items() if asiair.folder_category(r) == "handled" and now - m < RECENT_S)
    if recent:
        db.set_cleanup(cfg, cleanup_id, "stopped")
        return {"cleanup": cleanup_id, "stopped": f"the device wrote {len(recent)} file(s) in the last "
                f"{RECENT_S // 60} minutes (capturing?): nothing deleted. Try again when it's idle.",
                "recent": recent[:10], "deleted": 0}
    ops = db.cleanup_ops(cfg, cleanup_id)
    db.set_cleanup(cfg, cleanup_id, "running")
    frame_ok: dict[str, bool] = {}          # a thumbnail follows its frame: kept if the frame was kept
    deleted, freed = [], 0
    with open(log_path, "a") as log:
        for n, o in enumerate(ops, 1):
            if o["status"] != "pending":
                continue
            progress(n / len(ops) * 100, f"{o['rel']} ({n}/{len(ops)})")
            status, detail = _one(cfg, source, o, before, frame_ok)
            db.set_cleanup_op(cfg, o["id"], status, detail)
            log.write(f"{ {'deleted': 'OK', 'gone': 'OK', 'skipped': 'SKIP'}.get(status, 'FAIL') }\t{o['kind']}\t"
                      f"{o['rel']}\t{status}: {detail}\n")
            log.flush()
            if status == "deleted":
                deleted.append(o["rel"])
                freed += o["size"]
        pruned = _prune(source, {o["rel"] for o in ops}, log)
    progress(100, "listing the device again")
    after = _listing(source)
    ops = db.cleanup_ops(cfg, cleanup_id)
    gone_ok = {o["rel"] for o in ops if o["status"] in ("deleted", "gone")}
    vanished = set(before) - set(after)
    unexpected = sorted(vanished - gone_ok)
    still_there = sorted(r for r in gone_ok if r in after)
    by = Counter(o["status"] for o in ops)
    result = {"cleanup": cleanup_id, "deleted": by["deleted"], "already_gone": by["gone"], "bytes_freed": freed,
              "skipped": [{"rel": o["rel"], "detail": o["detail"]} for o in ops if o["status"] == "skipped"],
              "failed": [{"rel": o["rel"], "detail": o["detail"]} for o in ops if o["status"] == "failed"],
              "pruned": pruned, "unexpected_missing": unexpected, "still_there": still_there,
              "new_files": len(set(after) - set(before)), "seconds": round(time.monotonic() - started, 1),
              "log": str(log_path)}
    ok = not result["failed"] and not unexpected and not still_there
    db.set_cleanup(cfg, cleanup_id, "done" if ok else "failed", db.cleanup(cfg, cleanup_id)["summary"] | {"result": result})
    return result


def _one(cfg: Config, source, o: dict, before: dict, frame_ok: dict) -> tuple[str, str]:
    rel = o["rel"]
    if o["kind"] == "thumb":
        fit = asiair.fit_for_thumb(rel)
        if fit in frame_ok and not frame_ok[fit]:
            return "skipped", "its frame was kept"
    now = before.get(rel)
    if now is None:
        frame_ok[rel] = True
        return "gone", "no longer on the device"
    if now[0] != o["size"] or int(now[1]) != int(o["mtime"]):
        frame_ok[rel] = False
        return "skipped", f"changed on the device since the scan ({now[0]} bytes, mtime {int(now[1])})"
    for nas_rel, digest in o["nas"]:
        p = fsops.nas_path(cfg, nas_rel)
        if p is None or not fsops.digest_matches(p, digest):
            frame_ok[rel] = False
            return "skipped", f"NAS copy {nas_rel} is {'missing' if p is None else 'different now'}"
    if o["kind"] != "thumb" and o["group"] == "verified" and not o["nas"]:
        frame_ok[rel] = False
        return "skipped", "no NAS copy recorded"
    try:
        source.delete(rel)
    except (OSError, DeleteRefused) as e:
        frame_ok[rel] = False
        return "failed", str(e)
    if source.stat(rel) is not None:
        frame_ok[rel] = False
        return "failed", "still there after the delete"
    frame_ok[rel] = True
    return "deleted", "verified gone"


def _prune(source, rels: set[str], log) -> list[str]:
    """Remove object folders (Plan/Light/<object>, Autorun/Light/<object>) that this run left empty."""
    pruned = []
    folders = {str(PurePosixPath(r).parent) for r in rels}
    for d in sorted(folders):
        parts = d.split("/")
        if len(parts) != 3 or parts[1] != "Light":
            continue
        if source.listdir(d):
            continue
        try:
            source.rmdir(d)
            pruned.append(d)
            log.write(f"OK\tfolder\t{d}\tremoved (empty)\n")
        except (OSError, DeleteRefused) as e:
            log.write(f"SKIP\tfolder\t{d}\t{e}\n")
    return pruned

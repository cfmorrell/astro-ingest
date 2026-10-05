"""Start over (Chris, 2026-09-27, proposal J): clear this run's working state and go back to Connect.

Cleared: staged frames whose original is still on the device, rendered previews and thumbnails, quality scores of the
device's frames, what was left out on Select and kept or rejected on Review, decision answers (unless kept on
purpose: after starting over on a different device, old answers may not make sense), the session (step, checkmarks,
ticks), and the device connection.
Kept: everything on the NAS and its catalog, logs and batch history, recent devices, Verify results, the
sensitivity (σ), the staging checksums of frames already copied (Clean up's proof that their NAS copies match), and
any staged frame whose original is no longer on the device, or couldn't be checked: it may be the last copy.
Nothing is deleted from a device or from the Astronomy share.
"""

from __future__ import annotations

import datetime as dt
import shutil
import time
from pathlib import Path

from astro_ingest import analysis, db, service, staging, state
from astro_ingest.config import Config
from astro_ingest.sources.local import LocalDirSource

SESSION_FILE = "session.json"


CACHE_SUBDIRS = ("previews", "thumbs")   # what analysis/api render into CACHE_DIR


def _guard(cfg: Config) -> None:
    """Staging and the cache must be the app's own folders: never the share or the state folder, nor a folder that
    contains them (inside them is fine: the default cache lives in STATE_DIR, dev staging may live in the sandbox)."""
    for name, folder in (("STAGING_DIR", Path(cfg.staging_dir)), ("CACHE_DIR", Path(cfg.cache_dir))):
        f = folder.resolve()
        for other in (Path(cfg.astro_root), Path(cfg.astro_nas), Path(cfg.state_dir)):
            o = other.resolve()
            if f == o or f in o.parents:
                raise RuntimeError(f"{name} ({folder}) is or contains {other}: refusing to clear it")


def _staging_slugs(cfg: Config) -> set[str]:
    """The per-device folders the app created in STAGING_DIR (the only things Start over removes there)."""
    slugs = {"local"} | {k.split("|", 1)[0] for k in staging.StagingStore(cfg).files}
    slugs |= {r.get("slug") or staging.device_slug(r, False) for r in service.recent_devices(cfg)}
    return {s for s in slugs if s and "/" not in s and s not in (".", "..")}


def _tree_bytes(root: Path) -> tuple[int, int]:
    files = size = 0
    if root.is_dir():
        for p in root.rglob("*"):
            if p.is_file() and not p.is_symlink():
                files += 1
                size += p.stat().st_size
    return files, size


def _decision_keys(answers: dict) -> list[str]:
    return [k for k in answers if not k.startswith(("exclude:", "keep:")) and k != analysis.SIGMA_KEY]


def _open_device(cfg: Config, slug: str):
    """The source a staging folder came from (a recent device, or the local ASIAIR_ROOT), or None."""
    if slug == "local":
        return LocalDirSource(cfg.asiair_root) if cfg.asiair_root and Path(cfg.asiair_root).is_dir() else None
    if slug.startswith("upload-"):
        # uploaded from the user's computer: the originals are still there (nothing deletes them), so these copies
        # are clearable, as long as the folder's manifest lists them
        from astro_ingest.sources.upload import UploadSource
        try:
            return UploadSource(cfg, slug)
        except FileNotFoundError:
            return None
    rec = next((r for r in service.recent_devices(cfg) if (r.get("slug") or staging.device_slug(r, False)) == slug), None)
    return service._smb(rec["host"], rec["share"]) if rec else None


def staged_files(cfg: Config, opener=_open_device) -> dict:
    """Every file in the app's staging folders, sorted by what Start over may do with it (Chris, 2026-09-27):
    `clear`: its original is still on the device (same size), so the staged copy is just a convenience;
    `gone`: the original is no longer on the device, so the staged copy may be the last one: kept;
    `unchecked`: the device couldn't be reached to check: kept. Unfinished `.part` files are always cleared."""
    out = {"clear": [], "gone": [], "unchecked": [], "unreachable": []}
    for slug in sorted(_staging_slugs(cfg)):
        folder = Path(cfg.staging_dir) / slug
        if not folder.is_dir() or folder.is_symlink():
            continue
        files = [(p.relative_to(folder).as_posix(), p) for p in sorted(folder.rglob("*")) if p.is_file() and not p.is_symlink()]
        if not files:
            continue
        partial = [(r, p) for r, p in files if r.endswith(".part")]
        out["clear"] += [(slug, r, p, p.stat().st_size) for r, p in partial]
        files = [(r, p) for r, p in files if not r.endswith(".part")]
        try:
            device = opener(cfg, slug)
        except Exception:     # offline, refused, ...
            device = None
        if device is None:
            out["unchecked"] += [(slug, r, p, p.stat().st_size) for r, p in files]
            if files:
                out["unreachable"].append(slug)
            continue
        try:
            for r, p in files:
                size = p.stat().st_size
                try:
                    there = device.stat(r)
                except OSError:
                    there = None
                out["clear" if there is not None and there.size == size else "gone"].append((slug, r, p, size))
        finally:
            close = getattr(device, "close", None)
            if close:
                close()
    return out


def _device_name(cfg: Config, slug: str) -> str:
    rec = next((r for r in service.recent_devices(cfg) if r.get("slug") == slug), None)
    return (rec.get("nickname") or rec.get("label")) if rec else slug


def blocked(cfg: Config) -> str | None:
    """Why starting over has to wait, if it does."""
    if db.unfinished_batch(cfg):
        return "a copy batch didn't finish: resume it on Copy & verify first (its staged frames are needed)"
    if db.unfinished_cleanup(cfg):
        return "a clean-up didn't finish: resume it on Clean up first"
    return None


def preview(cfg: Config) -> dict:
    """What starting over would clear, with counts, for the confirmation."""
    answers = service.load_answers(cfg)
    sf = staged_files(cfg)
    staged_files_n, staged_bytes = len(sf["clear"]), sum(x[3] for x in sf["clear"])
    cache = [_tree_bytes(Path(cfg.cache_dir) / s) for s in CACHE_SUBDIRS]
    cache_files, cache_bytes = sum(f for f, _ in cache), sum(b for _, b in cache)
    rate, _ = service.transfer_rate(cfg)
    quality = state.read_json(Path(cfg.state_dir) / analysis.QUALITY_FILE, {}).get("frames", {})
    return {
        "staged": {"files": staged_files_n, "bytes": staged_bytes, "restage_s": round(staged_bytes / 1e6 / rate) if rate else None,
                   "kept_gone": len(sf["gone"]), "kept_unchecked": len(sf["unchecked"]),
                   "unreachable": [_device_name(cfg, s) for s in sf["unreachable"]]},
        "cache": {"files": cache_files, "bytes": cache_bytes},
        "scores": sum(1 for k in quality if k.startswith("src|")),
        "left_out": sum(1 for k in answers if k.startswith("exclude:")),
        "kept_or_rejected": sum(1 for k in answers if k.startswith("keep:")),
        "decision_answers": len(_decision_keys(answers)),
        "device": service.remembered_device(cfg),
        "blocked": blocked(cfg),
    }


def run(cfg: Config, forget_answers: bool = True, opener=_open_device) -> dict:
    why = blocked(cfg)
    if why:
        raise RuntimeError(why)
    before = preview(cfg)
    stamp = dt.datetime.now(cfg.tz).strftime("%Y%m%d-%H%M%S")
    log_path = Path(cfg.state_dir) / "logs" / f"startover-{stamp}.log"
    cfg.check_writable(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []

    _guard(cfg)
    # staged frames: clear those whose original is still on the device; never the last copy of a frame
    sf = staged_files(cfg, opener)
    for slug, rel, path, _ in sf["clear"]:
        path.unlink()
    for slug in _staging_slugs(cfg):   # folders left empty
        folder = Path(cfg.staging_dir) / slug
        for d in sorted((x for x in folder.rglob("*") if x.is_dir() and not x.is_symlink()), key=lambda x: -len(x.parts)) \
                if folder.is_dir() else []:
            if not any(d.iterdir()):
                d.rmdir()
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    lines.append(f"staging: cleared {len(sf['clear'])} staged file(s) whose original is still on the device")
    lines += [f"staging: KEPT {slug}/{rel}: no longer on the device (this may be the last copy)" for slug, rel, _, _ in sf["gone"]]
    lines += [f"staging: KEPT {slug}/{rel}: couldn't reach the device to check" for slug, rel, _, _ in sf["unchecked"]]
    still = {f"{slug}|{rel}" for slug, rel, _, _ in sf["gone"] + sf["unchecked"]}
    store = staging.StagingStore(cfg)
    proof = db.copied_hashes(cfg)
    kept = {k: v for k, v in store.files.items() if v.get("blake2b") in proof or k in still}
    lines.append(f"staging manifest: {len(store.files) - len(kept)} entries cleared, {len(kept)} kept (copies on the NAS, staged files kept)")
    store.files = kept
    store.save()

    # previews and thumbnails (disposable renders)
    for sub in CACHE_SUBDIRS:
        child = Path(cfg.cache_dir) / sub
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
    lines.append(f"cache: cleared {before['cache']['files']} files")

    # quality scores of device frames (NAS-side scores stay: they're about the NAS)
    qpath = Path(cfg.state_dir) / analysis.QUALITY_FILE
    q = state.read_json(qpath, {})
    if q.get("frames"):
        q["frames"] = {k: v for k, v in q["frames"].items() if not k.startswith("src|")}
        state.write_json(cfg, qpath, q)
    lines.append(f"quality: cleared {before['scores']} device-frame scores")

    # choices and answers
    answers = service.load_answers(cfg)
    drop = [k for k in answers if k.startswith(("exclude:", "keep:"))] + (_decision_keys(answers) if forget_answers else [])
    service.set_answers(cfg, {k: None for k in drop})
    lines.append(f"answers: cleared {len(drop)} ({'including' if forget_answers else 'keeping'} decision answers)")

    # the session (other windows follow it back to Connect) and the device connection
    state.write_json(cfg, Path(cfg.state_dir) / SESSION_FILE, {"data": {}, "client": "start-over", "updated_at": time.time()})
    service.disconnect_device(cfg)
    lines.append("session reset; disconnected")

    log_path.write_text("".join(f"{line}\n" for line in lines))
    return {"cleared": before, "forgot_answers": forget_answers, "log": str(log_path),
            "kept_staged": [f"{slug}/{rel}" for slug, rel, _, _ in sf["gone"] + sf["unchecked"]]}

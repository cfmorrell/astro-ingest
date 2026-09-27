"""SQLite state in STATE_DIR: copy batches and their operations, checksum verifications of frames already on the
NAS, and clean-up runs (deletes from the device) with their operations.

Rollback journal, not WAL: the database lives on the UnRAID share (shfs/FUSE), where WAL's shared-memory file is
not safe. Every batch is also exported as plain text (STATE_DIR/batches/<id>.tsv and logs/copy-<id>.log) so a
Claude session can read what happened without SQL.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from astro_ingest.config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    approved_at REAL,
    finished_at REAL,
    status TEXT NOT NULL,            -- approved | running | done | failed
    source TEXT,
    summary_json TEXT
);
CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES batches(id),
    seq INTEGER NOT NULL,            -- order within the batch (retires before their copies)
    kind TEXT NOT NULL,              -- retire | copy
    src TEXT,                        -- source-relative path on the device
    staged TEXT,                     -- staged copy (copy) or the NAS file to retire (retire)
    dst TEXT NOT NULL,               -- share-relative destination
    size INTEGER,
    blake2b TEXT,
    status TEXT NOT NULL,            -- pending | done | already-there | clash | failed | skipped
    detail TEXT,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS operations_batch ON operations(batch_id, seq);
CREATE TABLE IF NOT EXISTS verified (
    slug TEXT NOT NULL,              -- device (staging slug)
    rel TEXT NOT NULL,               -- path on the device
    size INTEGER NOT NULL,
    mtime INTEGER NOT NULL,          -- whole seconds: the device file this result is about
    device_blake2b TEXT NOT NULL,
    nas_rel TEXT,                    -- the NAS copy that matched (or the last one tried)
    result TEXT NOT NULL,            -- match | mismatch | nas-missing
    verified_at REAL NOT NULL,
    PRIMARY KEY (slug, rel)
);
CREATE TABLE IF NOT EXISTS cleanups (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL,            -- approved | running | done | failed | stopped
    source TEXT,
    summary_json TEXT
);
CREATE TABLE IF NOT EXISTS cleanup_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cleanup_id TEXT NOT NULL REFERENCES cleanups(id),
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- frame | thumb | file | companion (Mac metadata of the file before it)
    rel TEXT NOT NULL,               -- path on the device
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,             -- as scanned: must still match before the delete
    grp TEXT NOT NULL,               -- clean-up group (verified, rejected, left-out, ...)
    nas_json TEXT,                   -- [[nas rel, blake2b], ...]: NAS copies re-hashed right before the delete
    status TEXT NOT NULL,            -- pending | deleted | gone | skipped | failed
    detail TEXT,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS cleanup_ops_run ON cleanup_ops(cleanup_id, seq);
"""

DB_FILE = "ingest.sqlite3"


def path(cfg: Config) -> Path:
    return Path(cfg.state_dir) / DB_FILE


@contextmanager
def connect(cfg: Config):
    p = path(cfg)
    cfg.check_writable(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=DELETE")
        con.executescript(SCHEMA)
        if "catalogued_at" not in {r["name"] for r in con.execute("PRAGMA table_info(batches)")}:
            con.execute("ALTER TABLE batches ADD COLUMN catalogued_at REAL")   # phase 5: Catalog done for this batch
        yield con
        con.commit()
    finally:
        con.close()


def create_batch(cfg: Config, batch_id: str, source: str, ops: list[dict], summary: dict) -> None:
    now = time.time()
    with connect(cfg) as con:
        con.execute("INSERT INTO batches (id, created_at, approved_at, status, source, summary_json) "
                    "VALUES (?, ?, ?, 'approved', ?, ?)", (batch_id, now, now, source, json.dumps(summary)))
        con.executemany(
            "INSERT INTO operations (batch_id, seq, kind, src, staged, dst, size, blake2b, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            [(batch_id, n, o["kind"], o.get("src"), o.get("staged"), o["dst"], o.get("size"), o.get("blake2b"))
             for n, o in enumerate(ops)])


def operations(cfg: Config, batch_id: str) -> list[dict]:
    with connect(cfg) as con:
        return [dict(r) for r in con.execute("SELECT * FROM operations WHERE batch_id = ? ORDER BY seq", (batch_id,))]


def set_operation(cfg: Config, op_id: int, status: str, detail: str = "", started_at: float | None = None) -> None:
    with connect(cfg) as con:
        con.execute("UPDATE operations SET status = ?, detail = ?, started_at = COALESCE(?, started_at), "
                    "finished_at = ? WHERE id = ?", (status, detail, started_at, time.time(), op_id))


def set_batch(cfg: Config, batch_id: str, status: str, summary: dict | None = None) -> None:
    with connect(cfg) as con:
        if summary is None:
            con.execute("UPDATE batches SET status = ?, finished_at = CASE WHEN ? IN ('done','failed') "
                        "THEN ? ELSE finished_at END WHERE id = ?", (status, status, time.time(), batch_id))
        else:
            con.execute("UPDATE batches SET status = ?, summary_json = ?, finished_at = CASE WHEN ? IN "
                        "('done','failed') THEN ? ELSE finished_at END WHERE id = ?",
                        (status, json.dumps(summary), status, time.time(), batch_id))


def batches(cfg: Config, limit: int = 20) -> list[dict]:
    with connect(cfg) as con:
        rows = con.execute("SELECT * FROM batches ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = json.loads(d.pop("summary_json") or "{}")
        out.append(d)
    return out


def batch(cfg: Config, batch_id: str) -> dict:
    with connect(cfg) as con:
        r = con.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    d = dict(r)
    d["summary"] = json.loads(d.pop("summary_json") or "{}")
    return d


def unfinished_batch(cfg: Config) -> str | None:
    """A batch that was approved but didn't finish (crash, restart): the next run resumes it."""
    with connect(cfg) as con:
        r = con.execute("SELECT id FROM batches WHERE status IN ('approved','running') "
                        "ORDER BY created_at DESC LIMIT 1").fetchone()
    return r["id"] if r else None


def uncatalogued_batches(cfg: Config) -> list[dict]:
    """Finished copy batches whose Catalog step hasn't run yet, oldest first."""
    with connect(cfg) as con:
        ids = [r["id"] for r in con.execute(
            "SELECT id FROM batches WHERE status = 'done' AND catalogued_at IS NULL ORDER BY created_at")]
    return [batch(cfg, i) | {"operations": operations(cfg, i)} for i in ids]


def mark_catalogued(cfg: Config, batch_ids: list[str]) -> None:
    with connect(cfg) as con:
        con.executemany("UPDATE batches SET catalogued_at = ? WHERE id = ?", [(time.time(), b) for b in batch_ids])


# ---------------------------------------------------------------- verification of frames already on the NAS

def verified_records(cfg: Config, slug: str) -> dict[str, dict]:
    with connect(cfg) as con:
        return {r["rel"]: dict(r) for r in con.execute("SELECT * FROM verified WHERE slug = ?", (slug,))}


def record_verified(cfg: Config, slug: str, rel: str, size: int, mtime: float, device_blake2b: str,
                    nas_rel: str | None, result: str) -> None:
    with connect(cfg) as con:
        con.execute("INSERT OR REPLACE INTO verified VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (slug, rel, size, int(mtime), device_blake2b, nas_rel, result, time.time()))


def copied_frames(cfg: Config, source_label: str) -> dict[str, list[dict]]:
    """{device path: [{dst, size, blake2b, batch}]} for every verified copy made from this source."""
    out: dict[str, list[dict]] = {}
    with connect(cfg) as con:
        for r in con.execute(
                "SELECT o.src, o.dst, o.size, o.blake2b, o.batch_id FROM operations o JOIN batches b ON b.id = o.batch_id "
                "WHERE o.kind = 'copy' AND o.status IN ('done', 'already-there') AND b.source = ?", (source_label,)):
            out.setdefault(r["src"], []).append({"dst": r["dst"], "size": r["size"], "blake2b": r["blake2b"],
                                                 "batch": r["batch_id"]})
    return out


def copied_hashes(cfg: Config) -> set[str]:
    """The BLAKE2b of every frame copied and verified onto the NAS, from any source."""
    with connect(cfg) as con:
        return {r["blake2b"] for r in con.execute(
            "SELECT blake2b FROM operations WHERE kind = 'copy' AND status IN ('done', 'already-there')") if r["blake2b"]}


# ---------------------------------------------------------------- clean-up runs

def create_cleanup(cfg: Config, cleanup_id: str, source: str, ops: list[dict], summary: dict) -> None:
    with connect(cfg) as con:
        con.execute("INSERT INTO cleanups (id, created_at, status, source, summary_json) VALUES (?, ?, 'approved', ?, ?)",
                    (cleanup_id, time.time(), source, json.dumps(summary)))
        con.executemany(
            "INSERT INTO cleanup_ops (cleanup_id, seq, kind, rel, size, mtime, grp, nas_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            [(cleanup_id, n, o["kind"], o["rel"], o["size"], o["mtime"], o["group"], json.dumps(o.get("nas") or []))
             for n, o in enumerate(ops)])


def cleanup_ops(cfg: Config, cleanup_id: str) -> list[dict]:
    with connect(cfg) as con:
        rows = con.execute("SELECT * FROM cleanup_ops WHERE cleanup_id = ? ORDER BY seq", (cleanup_id,)).fetchall()
    return [dict(r) | {"group": r["grp"], "nas": json.loads(r["nas_json"] or "[]")} for r in rows]


def set_cleanup_op(cfg: Config, op_id: int, status: str, detail: str = "") -> None:
    with connect(cfg) as con:
        con.execute("UPDATE cleanup_ops SET status = ?, detail = ?, finished_at = ? WHERE id = ?",
                    (status, detail, time.time(), op_id))


def set_cleanup(cfg: Config, cleanup_id: str, status: str, summary: dict | None = None) -> None:
    with connect(cfg) as con:
        done = status in ("done", "failed", "stopped")
        if summary is None:
            con.execute("UPDATE cleanups SET status = ?, finished_at = CASE WHEN ? THEN ? ELSE finished_at END "
                        "WHERE id = ?", (status, done, time.time(), cleanup_id))
        else:
            con.execute("UPDATE cleanups SET status = ?, summary_json = ?, finished_at = CASE WHEN ? THEN ? "
                        "ELSE finished_at END WHERE id = ?", (status, json.dumps(summary), done, time.time(), cleanup_id))


def cleanups(cfg: Config, limit: int = 20) -> list[dict]:
    with connect(cfg) as con:
        rows = con.execute("SELECT * FROM cleanups ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) | {"summary": json.loads(r["summary_json"] or "{}")} for r in rows]


def cleanup(cfg: Config, cleanup_id: str) -> dict:
    with connect(cfg) as con:
        r = con.execute("SELECT * FROM cleanups WHERE id = ?", (cleanup_id,)).fetchone()
    return dict(r) | {"summary": json.loads(r["summary_json"] or "{}")}


def unfinished_cleanup(cfg: Config) -> str | None:
    with connect(cfg) as con:
        r = con.execute("SELECT id FROM cleanups WHERE status IN ('approved','running') "
                        "ORDER BY created_at DESC LIMIT 1").fetchone()
    return r["id"] if r else None

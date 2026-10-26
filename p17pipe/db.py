"""SQLite schema and thin data-access layer.

Tables follow the project brief: scans, results, profiles, standards,
exceptions, plus a `registry` table that makes sweeps idempotent.
"""
from __future__ import annotations

import io
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS registry (
    path        TEXT PRIMARY KEY,
    size        INTEGER NOT NULL,
    mtime       REAL    NOT NULL,
    sha256      TEXT    NOT NULL,
    seen_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    scan_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    operator      TEXT,
    engineer      TEXT,
    order_no      TEXT,
    site          TEXT,
    source_path   TEXT NOT NULL,
    file_hash     TEXT NOT NULL UNIQUE,
    scanned_at    TEXT,
    ingested_at   TEXT NOT NULL,
    data_file     TEXT,
    recipe        TEXT,
    n_points      INTEGER,
    step_size_um  REAL,
    scan_length_um REAL,
    sampling_rate_hz REAL,
    scan_speed_um_s REAL,
    force_mg      REAL,
    stylus        TEXT,
    z_unit        TEXT NOT NULL DEFAULT 'A',
    x_unit        TEXT NOT NULL DEFAULT 'um',
    header_json   TEXT
);

CREATE TABLE IF NOT EXISTS results (
    scan_id          INTEGER PRIMARY KEY REFERENCES scans(scan_id),
    step_height      REAL,   -- in scans.z_unit (Å)
    ra               REAL,
    rq               REAL,
    rt               REAL,
    leveling_method  TEXT,
    filter_cutoff_um REAL,
    analysis_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    scan_id  INTEGER PRIMARY KEY REFERENCES scans(scan_id),
    x_npy    BLOB NOT NULL,
    z_npy    BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS standards (
    scan_id      INTEGER PRIMARY KEY REFERENCES scans(scan_id),
    standard_id  TEXT NOT NULL,
    nominal      REAL,          -- Å
    measured     REAL NOT NULL, -- Å
    measured_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exceptions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    path     TEXT NOT NULL,
    reason   TEXT NOT NULL,
    seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_exceptions_path ON exceptions(path);
CREATE INDEX IF NOT EXISTS ix_scans_order ON scans(operator, engineer, order_no);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


@contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


# --- numpy <-> BLOB ---------------------------------------------------------

def arr_to_blob(a: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.save(buf, np.asarray(a, dtype=np.float32), allow_pickle=False)
    return buf.getvalue()


def blob_to_arr(b: bytes) -> np.ndarray:
    return np.load(io.BytesIO(b), allow_pickle=False)


# --- registry ---------------------------------------------------------------

def registry_is_current(con: sqlite3.Connection, path: str, size: int, mtime: float) -> bool:
    """True if this exact (path, size, mtime) has already been processed."""
    row = con.execute(
        "SELECT size, mtime FROM registry WHERE path = ?", (path,)
    ).fetchone()
    return bool(row) and row["size"] == size and abs(row["mtime"] - mtime) < 1e-6


def registry_upsert(con: sqlite3.Connection, path: str, size: int, mtime: float, sha256: str) -> None:
    con.execute(
        """INSERT INTO registry(path, size, mtime, sha256, seen_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime,
                                           sha256=excluded.sha256, seen_at=excluded.seen_at""",
        (path, size, mtime, sha256, utcnow()),
    )


def hash_exists(con: sqlite3.Connection, sha256: str) -> bool:
    return con.execute("SELECT 1 FROM scans WHERE file_hash = ?", (sha256,)).fetchone() is not None


def log_exception(con: sqlite3.Connection, path: str, reason: str) -> None:
    con.execute(
        "INSERT INTO exceptions(path, reason, seen_at) VALUES (?,?,?)", (path, reason, utcnow())
    )

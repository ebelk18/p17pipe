"""Two ingestion paths converging on one DB:

  sweep()  — recursive poll of the team tree; metadata from the folder path.
  inbox()  — single drop folder; metadata from the encoded file name, with a
             routed copy placed under route_root/<operator>/<engineer>/<order>.

Both call ingest_file(), which parses, analyses and records a single export.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from . import db
from .analysis import analyze
from .config import Config
from .parser import ParseError, parse_file
from .routing import Meta, RoutingError, meta_from_name, meta_from_path, route_destination

log = logging.getLogger("p17pipe")


@dataclass
class IngestStats:
    seen: int = 0
    ingested: int = 0
    skipped: int = 0
    exceptions: int = 0

    def __str__(self) -> str:
        return f"seen={self.seen} ingested={self.ingested} skipped={self.skipped} exceptions={self.exceptions}"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_export(path: Path, cfg: Config) -> bool:
    name = path.name.lower()
    return any(name.endswith(ext) for ext in cfg.export_extensions)


def _settled(path: Path, cfg: Config) -> bool:
    return (time.time() - path.stat().st_mtime) >= cfg.settle_seconds


def _standard_id(recipe: str | None, cfg: Config) -> str | None:
    if not recipe or not cfg.standard_recipe_pattern.search(recipe):
        return None
    # e.g. "STD_1UM_fast" -> "STD_1UM"; fall back to the whole recipe name
    for sid in cfg.standard_nominals:
        if sid.lower() in recipe.lower():
            return sid
    return re.sub(r"[^A-Za-z0-9_]+", "_", recipe).upper()


def ingest_file(con: sqlite3.Connection, path: Path, meta: Meta, cfg: Config,
                file_hash: str | None = None) -> int | None:
    """Parse + analyse + record one export. Returns scan_id, or None if the
    identical file content is already in the DB."""
    file_hash = file_hash or sha256_of(path)
    if db.hash_exists(con, file_hash):
        return None
    scan = parse_file(path)
    res = analyze(scan.x_um, scan.z, cursor_fraction=cfg.cursor_fraction,
                  cutoff_um=cfg.roughness_cutoff_um)
    with db.transaction(con):
        cur = con.execute(
            """INSERT INTO scans(operator, engineer, order_no, site, source_path, file_hash,
                                 scanned_at, ingested_at, data_file, recipe, n_points,
                                 step_size_um, scan_length_um, sampling_rate_hz,
                                 scan_speed_um_s, force_mg, stylus, z_unit, x_unit, header_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (meta.operator, meta.engineer, meta.order, meta.site, str(path), file_hash,
             scan.header.get("scanned_at"), db.utcnow(), scan.header.get("data_file"),
             scan.recipe, len(scan.z), scan.step_size_um,
             scan.get_float("scan_length_um") or float(scan.x_um[-1] - scan.x_um[0]),
             scan.get_float("sampling_rate_hz"), scan.get_float("scan_speed_um_s"),
             scan.get_float("force_mg"), scan.header.get("stylus"),
             cfg.z_unit, cfg.x_unit, json.dumps(scan.header)),
        )
        scan_id = int(cur.lastrowid)
        con.execute(
            """INSERT INTO results(scan_id, step_height, ra, rq, rt,
                                   leveling_method, filter_cutoff_um, analysis_version)
               VALUES (?,?,?,?,?,?,?,?)""",
            (scan_id, res.step_height, res.ra, res.rq, res.rt,
             res.leveling_method, res.filter_cutoff_um, cfg.analysis_version),
        )
        con.execute(
            "INSERT INTO profiles(scan_id, x_npy, z_npy) VALUES (?,?,?)",
            (scan_id, db.arr_to_blob(scan.x_um), db.arr_to_blob(res.z_leveled)),
        )
        sid = _standard_id(scan.recipe, cfg)
        if sid:
            con.execute(
                """INSERT INTO standards(scan_id, standard_id, nominal, measured, measured_at)
                   VALUES (?,?,?,?,?)""",
                (scan_id, sid, cfg.standard_nominals.get(sid), res.step_height,
                 scan.header.get("scanned_at") or db.utcnow()),
            )
        if "warning" in scan.header:
            db.log_exception(con, str(path), scan.header["warning"])
    return scan_id


def _process(con: sqlite3.Connection, path: Path, meta_fn, cfg: Config, stats: IngestStats) -> Meta | None:
    """Common registry/exception wrapper around ingest_file."""
    stats.seen += 1
    st = path.stat()
    key = str(path.resolve())
    if db.registry_is_current(con, key, st.st_size, st.st_mtime):
        stats.skipped += 1
        return None
    if not _settled(path, cfg):
        stats.skipped += 1
        return None
    file_hash = sha256_of(path)
    try:
        meta = meta_fn(path)
        scan_id = ingest_file(con, path, meta, cfg, file_hash=file_hash)
        if scan_id is None:
            log.info("duplicate content, registry only: %s", path)
            stats.skipped += 1
        else:
            stats.ingested += 1
            log.info("ingested scan_id=%s %s", scan_id, path)
    except (RoutingError, ParseError, ValueError) as e:
        with db.transaction(con):
            db.log_exception(con, key, f"{type(e).__name__}: {e}")
        stats.exceptions += 1
        log.warning("exception for %s: %s", path, e)
        meta = None
    with db.transaction(con):
        db.registry_upsert(con, key, st.st_size, st.st_mtime, file_hash)
    return meta


def sweep(con: sqlite3.Connection, cfg: Config) -> IngestStats:
    """One pass over the team tree. Read-only with respect to the tree."""
    stats = IngestStats()
    root = Path(cfg.team_root)
    for path in sorted(p for p in root.rglob("*") if p.is_file() and _is_export(p, cfg)):
        _process(con, path, lambda p: meta_from_path(p, root), cfg, stats)
    log.info("sweep done: %s", stats)
    return stats


def inbox(con: sqlite3.Connection, cfg: Config, move: bool = False) -> IngestStats:
    """One pass over the drop folder. Files whose name matches the convention
    are ingested and copied (or moved) to route_root/<op>/<eng>/<order>/."""
    stats = IngestStats()
    for path in sorted(p for p in Path(cfg.inbox_dir).iterdir() if p.is_file() and _is_export(p, cfg)):
        stem = path.name.split(".")[0]
        meta = _process(con, path, lambda p: meta_from_name(stem, cfg), cfg, stats)
        if meta is None:
            continue
        dest = route_destination(meta, path.name, cfg.route_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            (shutil.move if move else shutil.copy2)(str(path), str(dest))
            log.info("routed %s -> %s", path.name, dest)
    log.info("inbox done: %s", stats)
    return stats


def run_forever(con: sqlite3.Connection, cfg: Config, do_sweep: bool = True, do_inbox: bool = True) -> None:
    while True:
        if do_sweep:
            sweep(con, cfg)
        if do_inbox:
            inbox(con, cfg)
        time.sleep(cfg.poll_seconds)

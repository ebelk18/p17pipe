"""Parser for KLA Profiler ASCII exports.

The documented layout (P-15/P-7/P-17 lineage) is:

    <data file name>
    <recipe name>
    <number of data points>
    <step size, microns>
    <column heading row, tab-delimited, includes a "Normal" column>
    <data rows ...>

Profiler v9.10 exports (confirmed on a P-17) use a tab-separated header instead:

    Data<TAB>name
    Recipe<TAB>recipe
    Site Name<TAB>
    Points<TAB>N
    X-Resolution<TAB>step_um
    X-Coord<TAB>stage_x     Y-Coord<TAB>stage_y
    <TAB>Raw<TAB>Intermediate<TAB>Normal<TAB>Roughness<TAB>Waviness
    1<TAB>raw<TAB>inter<TAB>normal<TAB>rough<TAB>wav      (no Distance column)

Both layouts are handled. Other builds may carry extra `Key: value` header
lines (scan length, speed, sampling rate, force, stylus ...). This parser is
deliberately tolerant: it reads everything above the first numeric block as
header, assigns the first four non-key/value lines positionally, and stores
any `Key: value` pairs it finds. Swap `POSITIONAL_KEYS` / `KEY_ALIASES` once a
real scrubbed export is available.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

POSITIONAL_KEYS = ("data_file", "recipe", "n_points", "step_size_um")

# Normalised header keys -> candidate labels seen in KLA exports (case-insensitive)
KEY_ALIASES = {
    "data_file": ("data", "data file", "data name"),
    "recipe": ("recipe", "recipe name", "scan recipe"),
    "site_name": ("site name", "site"),
    "x_coord_um": ("x-coord", "x coord", "x position"),
    "y_coord_um": ("y-coord", "y coord", "y position"),
    "scan_length_um": ("scan length", "length", "x scan size"),
    "sampling_rate_hz": ("sampling rate", "sample rate", "frequency"),
    "scan_speed_um_s": ("scan speed", "speed"),
    "force_mg": ("applied force", "stylus force", "force"),
    "stylus": ("stylus", "stylus type", "tip"),
    "n_points": ("number of points", "data points", "points"),
    "step_size_um": ("x-resolution", "x resolution", "step size", "point interval", "spacing"),
    "scanned_at": ("date", "time", "date/time", "scan date"),
}

_NUM_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")
_SPLIT_RE = re.compile(r"[\t,;]|\s{2,}")


class ParseError(ValueError):
    pass


@dataclass
class Scan:
    header: dict[str, str] = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    x_um: np.ndarray = field(default_factory=lambda: np.zeros(0))   # µm
    z: np.ndarray = field(default_factory=lambda: np.zeros(0))      # tool units (Å)
    extra: dict[str, np.ndarray] = field(default_factory=dict)
    source: Path | None = None

    # convenience accessors --------------------------------------------------
    def get_float(self, key: str) -> float | None:
        v = self.header.get(key)
        if v is None:
            return None
        m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", v)
        return float(m.group()) if m else None

    @property
    def recipe(self) -> str | None:
        return self.header.get("recipe")

    @property
    def step_size_um(self) -> float | None:
        v = self.get_float("step_size_um")
        if v is None and len(self.x_um) > 1:
            v = float(np.median(np.diff(self.x_um)))
        return v


def _split(line: str) -> list[str]:
    return [t.strip() for t in _SPLIT_RE.split(line.strip()) if t.strip() != ""]


def _is_numeric_row(tokens: list[str]) -> bool:
    return len(tokens) >= 1 and all(_NUM_RE.match(t) for t in tokens)


def _normalise_key(raw: str) -> str | None:
    k = raw.strip().lower()
    for norm, aliases in KEY_ALIASES.items():
        if k in aliases:
            return norm
    return None




def _split_keyvalue(line: str) -> tuple[str, str] | None:
    """Return (key, value) for 'Key: v', 'Key = v' or 'Key<TAB>v' (v9.x export),
    else None. A tab-separated line counts only if its first field is a known key,
    so a positional line such as a recipe name is not mistaken for one."""
    if ":" in line or "=" in line:
        sep = ":" if ":" in line else "="
        k, v = line.split(sep, 1)
        return k.strip(), v.strip()
    if "\t" in line:
        k, _, v = line.partition("\t")
        if _normalise_key(k):
            return k.strip(), v.strip()
    # 'Site Name' followed by nothing, or key and value separated by spaces
    low = line.strip().lower()
    for aliases in KEY_ALIASES.values():
        for a in aliases:
            if low == a or low.startswith(a + " "):
                return line.strip()[:len(a)], line.strip()[len(a):].strip()
    return None


def parse_text(text: str, source: Path | None = None) -> Scan:
    lines = [ln.rstrip("\r\n") for ln in text.splitlines()]
    lines = [ln for ln in lines if ln.strip() != ""]
    if not lines:
        raise ParseError("empty file")

    # 1. find first numeric data row
    first_data = None
    for i, ln in enumerate(lines):
        if _is_numeric_row(_split(ln)) and len(_split(ln)) >= 2:
            first_data = i
            break
    if first_data is None:
        raise ParseError("no numeric data block found")

    header_lines = lines[:first_data]
    scan = Scan(source=source)

    # 2. column heading = last header line that is not key:value and has >=2 tokens
    col_idx = None
    for i in range(len(header_lines) - 1, -1, -1):
        toks = _split(header_lines[i])
        if len(toks) >= 2 and ":" not in header_lines[i] and "=" not in header_lines[i]:
            col_idx = i
            break
    if col_idx is not None:
        scan.columns = _split(header_lines[col_idx])
        header_lines = header_lines[:col_idx]

    # 3. key/value lines vs positional lines
    positional: list[str] = []
    for ln in header_lines:
        kv = _split_keyvalue(ln)
        if kv is not None:
            k, v = kv
            scan.header[_normalise_key(k) or k.strip().lower()] = v
        else:
            positional.append(ln.strip())
    for key, val in zip(POSITIONAL_KEYS, positional):
        scan.header.setdefault(key, val)
    if len(positional) > len(POSITIONAL_KEYS):
        scan.header["extra_header_lines"] = " | ".join(positional[len(POSITIONAL_KEYS):])

    # 4. data block
    rows = []
    for ln in lines[first_data:]:
        toks = _split(ln)
        if not _is_numeric_row(toks):
            break  # trailing stats / footer
        rows.append([float(t) for t in toks])
    width = min(len(r) for r in rows)
    data = np.array([r[:width] for r in rows], dtype=float)

    # 5. pick x and z columns
    if scan.columns and width == len(scan.columns) + 1:
        scan.columns = ["Index"] + scan.columns  # v9.x: unnamed leading index column
    cols_lower = [c.lower() for c in scan.columns]
    z_col = next((i for i, c in enumerate(cols_lower) if "normal" in c or c in ("z", "height", "profile")), None)
    x_col = next((i for i, c in enumerate(cols_lower) if "dist" in c or c in ("x", "position", "lateral position")), None)
    if z_col is None:
        z_col = 1 if width >= 2 else 0
    if x_col is None and not scan.columns:
        x_col = 0 if z_col != 0 else None

    z = data[:, z_col]
    if x_col is not None and width > 1:
        x = data[:, x_col]
    else:
        step = scan.get_float("step_size_um") or 1.0
        x = np.arange(len(z)) * step
    # if the x column is just an index, rebuild from step size
    if x_col is not None and scan.get_float("step_size_um") and np.allclose(np.diff(x), 1.0):
        x = x * scan.get_float("step_size_um")

    scan.x_um, scan.z = x, z
    # keep the tool's own filtered columns for validation against our Gaussian filter
    for name in ("roughness", "waviness", "raw", "intermediate"):
        j = next((i for i, c in enumerate(cols_lower) if c == name), None)
        if j is not None:
            scan.extra[name] = data[:, j]
    n_hdr = scan.get_float("n_points")
    if n_hdr and abs(n_hdr - len(z)) > 0:
        scan.header["warning"] = f"header n_points={int(n_hdr)} but parsed {len(z)} rows"
    return scan


def parse_file(path: Path | str) -> Scan:
    """z is left in the tool's export unit (Å on the P-17); x is µm."""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    return parse_text(text, source=p)

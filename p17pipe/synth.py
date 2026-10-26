"""Synthetic P-17-style ASCII exports for development, tests and the public
GitHub version (no real customer data).

Writes the documented 4-line header + column row, with an optional block of
`Key: value` lines to exercise the tolerant parser.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

COLUMNS = ("Point", "Distance", "Normal", "Lateral", "Force")


def make_profile(n: int = 2000, step_um: float = 0.5, height: float = 10000.0,
                 edge_frac: float = 0.5, tilt_a_per_mm: float = 3000.0,
                 noise: float = 20.0, roughness: float = 40.0,
                 rng: random.Random | None = None) -> tuple[np.ndarray, np.ndarray]:
    """x in µm, z in Å (tool units)."""
    rng = rng or random.Random(0)
    nrng = np.random.default_rng(rng.randrange(1 << 30))
    x = np.arange(n) * step_um
    z = np.where(x >= x[-1] * edge_frac, height, 0.0)
    z = z + tilt_a_per_mm * x / 1000.0
    z = z + nrng.normal(0, noise, n)
    # low-frequency "roughness" component
    z = z + roughness * np.sin(x / 7.0) * nrng.normal(1, 0.2, n)
    return x, z


def write_export(path: Path, x: np.ndarray, z: np.ndarray, recipe: str,
                 data_file: str, extra_header: bool = True, when: datetime | None = None) -> Path:
    step = float(x[1] - x[0])
    lines = [data_file, recipe, str(len(z)), f"{step:.6f}"]
    if extra_header:
        when = when or datetime.now(timezone.utc)
        lines += [
            f"Date: {when.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Scan Length: {x[-1] - x[0]:.1f} um",
            f"Scan Speed: {max(step * 100, 20):.0f} um/s",
            "Sampling Rate: 200 Hz",
            "Applied Force: 2.0 mg",
            "Stylus: 2um radius",
        ]
    lines.append("\t".join(COLUMNS))
    for i, (xi, zi) in enumerate(zip(x, z)):
        lines.append(f"{i}\t{xi:.4f}\t{zi:.3f}\t{0.0:.4f}\t{2.0:.3f}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_export_v9(path: Path, x: np.ndarray, z: np.ndarray, recipe: str, data_name: str,
                    cutoff_um: float = 80.0, stage_xy: tuple[float, float] = (-1234.56, 7890.12)) -> Path:
    """Profiler v9.10-style export: tab-separated header, unnamed index column,
    z in Å, and the tool's own Roughness/Waviness split (ISO Gaussian)."""
    from .analysis import gaussian_waviness
    step = float(x[1] - x[0])
    z_a = np.asarray(z, dtype=float)
    wav = gaussian_waviness(x, z_a, cutoff_um)
    rough = z_a - wav
    raw = z_a - 70000.0
    inter = raw - raw[0]
    lines = [f"Data\t{data_name}", f"Recipe\t{recipe}", "Site Name\t", f"Points\t{len(z_a)}",
             f"X-Resolution\t{step:.6f}", f"X-Coord\t{stage_xy[0]:.2f}", f"Y-Coord\t{stage_xy[1]:.2f}",
             "\tRaw\tIntermediate\tNormal\tRoughness\tWaviness"]
    for i in range(len(z_a)):
        lines.append(f"{i+1}\t{raw[i]:.6g}\t{inter[i]:.6g}\t{z_a[i]:.6g}\t{rough[i]:.6g}\t{wav[i]:.6g}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return path


def build_team_tree(team_root: Path, inbox_dir: Path | None = None, seed: int = 1,
                    n_orders: int = 3, scans_per_order: int = 4, n_std: int = 20) -> list[Path]:
    """Create <root>/<operator>/<engineer>/<order>/scan_k.txt plus a standards
    series under <root>/<operator>/QC/STD, plus a few inbox files and one
    deliberately mis-filed file to exercise the exceptions table."""
    rng = random.Random(seed)
    written = []
    operators = ["jdoe", "asmith"]
    engineers = ["rlee", "mpatel", "kchen"]
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for op in operators:
        for k in range(n_orders):
            eng = rng.choice(engineers)
            order = f"ORD{rng.randrange(40000, 49999)}"
            for s in range(scans_per_order):
                h = rng.choice([2500.0, 8000.0, 16000.0, 32000.0])
                x, z = make_profile(height=h, rng=rng, tilt_a_per_mm=rng.uniform(-5000, 5000))
                p = team_root / op / eng / order / f"scan_{s+1}.txt"
                if s % 2 == 0:
                    written.append(write_export_v9(p, x, z, "_DELAYERING", f"C{s+1}"))
                else:
                    written.append(write_export(p, x, z, f"STEP_{int(h/10)}nm", f"{order}_{s+1}.dat",
                                                when=t0 + timedelta(days=k, hours=s)))
        # standards series with a slow drift so the SPC rules have something to catch
        for i in range(n_std):
            drift = 0.0 if i < n_std * 0.6 else 40.0 * (i - n_std * 0.6)
            x, z = make_profile(height=10000.0 + drift, noise=15.0, roughness=0.0, rng=rng)
            p = team_root / op / "QC" / "STD" / f"std_{i:03d}.txt"
            written.append(write_export(p, x, z, "STD_1UM_daily", f"STD1UM_{i:03d}.dat",
                                        when=t0 + timedelta(days=i)))
    # mis-filed: directly under operator (too shallow) -> exceptions table
    x, z = make_profile(rng=rng)
    written.append(write_export(team_root / "jdoe" / "stray.txt", x, z, "STEP_1000nm", "stray.dat"))
    # inbox files
    if inbox_dir is not None:
        for name in ("JDOE_RLEE_48123_siteA.txt", "ASMITH_KCHEN_41211_center.txt", "badname.txt"):
            x, z = make_profile(height=rng.choice([5000.0, 20000.0]), rng=rng)
            written.append(write_export(inbox_dir / name, x, z, "STEP_auto", name.replace(".txt", ".dat"),
                                        extra_header=False))
    return written

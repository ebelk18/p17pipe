"""Profile analysis: leveling, step height, roughness/waviness.

These are reimplementations meant to be validated against the tool's own
numbers (see tests and the `validate` CLI command). Keep the algorithms
simple and documented so the comparison is explainable.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ISO 16610-21 Gaussian constant: alpha = sqrt(ln2 / pi)
_ALPHA = float(np.sqrt(np.log(2.0) / np.pi))


@dataclass
class Regions:
    """Cursor regions as (start, end) in the same units as x (µm)."""
    left: tuple[float, float]
    right: tuple[float, float]


def default_regions(x: np.ndarray, fraction: float = 0.15) -> Regions:
    """First/last `fraction` of the scan — the usual two-cursor step setup."""
    x0, x1 = float(x[0]), float(x[-1])
    span = x1 - x0
    return Regions(left=(x0, x0 + fraction * span), right=(x1 - fraction * span, x1))


def _mask(x: np.ndarray, region: tuple[float, float]) -> np.ndarray:
    a, b = region
    return (x >= a) & (x <= b)


def level_two_region(x: np.ndarray, z: np.ndarray, regions: Regions) -> np.ndarray:
    """Least-squares line through BOTH cursor regions, subtracted from z.

    This is the common 'level on the two reference bands' behaviour. For a
    step, both regions sit on different planes, so instead we fit a line
    through the *means* of the two regions only if they are on the same
    level; for step heights use `level_single_region` or `step_height`.
    """
    m = _mask(x, regions.left) | _mask(x, regions.right)
    p = np.polyfit(x[m], z[m], 1)
    return z - np.polyval(p, x)


def level_single_region(x: np.ndarray, z: np.ndarray, region: tuple[float, float]) -> np.ndarray:
    """Fit a line within one reference band (e.g. the substrate) and remove it."""
    m = _mask(x, region)
    p = np.polyfit(x[m], z[m], 1)
    return z - np.polyval(p, x)


def level_least_squares(x: np.ndarray, z: np.ndarray) -> np.ndarray:
    p = np.polyfit(x, z, 1)
    return z - np.polyval(p, x)


def step_height(x: np.ndarray, z: np.ndarray, regions: Regions) -> tuple[float, np.ndarray]:
    """Step height = mean(right band) - mean(left band) after leveling on the
    left band (reference plane). Returns (height, leveled_z)."""
    zl = level_single_region(x, z, regions.left)
    h = float(zl[_mask(x, regions.right)].mean() - zl[_mask(x, regions.left)].mean())
    return h, zl


def gaussian_waviness(x: np.ndarray, z: np.ndarray, cutoff_um: float) -> np.ndarray:
    """ISO 16610-21 Gaussian filter (mean line / waviness).

    s(t) = 1/(alpha*lc) * exp(-pi * (t/(alpha*lc))^2), normalised discretely.
    Edges are handled by renormalising the truncated kernel (no padding).
    """
    dx = float(np.median(np.diff(x)))
    half = int(np.ceil(cutoff_um / dx))  # kernel extends +-1 cutoff
    t = np.arange(-half, half + 1) * dx
    k = np.exp(-np.pi * (t / (_ALPHA * cutoff_um)) ** 2)
    k /= k.sum()
    num = np.convolve(z, k, mode="same")
    den = np.convolve(np.ones_like(z), k, mode="same")
    return num / den


@dataclass
class Roughness:
    """z-valued fields are in the profile's own unit (Å); cutoff is in x units (µm)."""
    ra: float
    rq: float
    rt: float
    cutoff_um: float


def roughness(x: np.ndarray, z_leveled: np.ndarray, cutoff_um: float,
              region: tuple[float, float] | None = None) -> Roughness:
    w = gaussian_waviness(x, z_leveled, cutoff_um)
    r = z_leveled - w
    if region is not None:
        r = r[_mask(x, region)]
    r = r - r.mean()
    return Roughness(
        ra=float(np.mean(np.abs(r))),
        rq=float(np.sqrt(np.mean(r ** 2))),
        rt=float(r.max() - r.min()),
        cutoff_um=cutoff_um,
    )


@dataclass
class AnalysisResult:
    """step_height / ra / rq / rt are in the profile's unit (Å); cutoff in µm."""
    step_height: float
    ra: float
    rq: float
    rt: float
    leveling_method: str
    filter_cutoff_um: float
    z_leveled: np.ndarray


def analyze(x: np.ndarray, z: np.ndarray, regions: Regions | None = None,
            cursor_fraction: float = 0.15, cutoff_um: float = 80.0) -> AnalysisResult:
    """Default analysis used by the sweep: step over default cursor bands,
    roughness evaluated on the left (reference) band."""
    regions = regions or default_regions(x, cursor_fraction)
    h, zl = step_height(x, z, regions)
    rg = roughness(x, zl, cutoff_um, region=regions.left)
    return AnalysisResult(
        step_height=h,
        ra=rg.ra, rq=rg.rq, rt=rg.rt,
        leveling_method="left_band_lsq",
        filter_cutoff_um=cutoff_um,
        z_leveled=zl,
    )

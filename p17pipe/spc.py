"""SPC for step-height-standard scans (instrument-health monitor).

Rules implemented (Western Electric subset):
  R1  one point beyond +-3 sigma
  R4  eight consecutive points on one side of the mean
  BIAS mean deviates from nominal by more than `bias_tol_frac`
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class SpcSummary:
    standard_id: str
    nominal: float | None
    n: int
    mean: float
    sigma: float
    ucl: float
    lcl: float
    bias_frac: float | None
    alerts: list[str] = field(default_factory=list)
    series: list[tuple[str, float]] = field(default_factory=list)  # (measured_at, value)


def _rules(vals: np.ndarray, mean: float, sigma: float, nominal: float | None, bias_tol_frac: float) -> list[str]:
    alerts = []
    if sigma > 0:
        out = np.where(np.abs(vals - mean) > 3 * sigma)[0]
        if out.size:
            alerts.append(f"R1: {out.size} point(s) beyond 3σ at index {out.tolist()}")
    side = np.sign(vals - mean)
    run = 1
    for i in range(1, len(side)):
        run = run + 1 if side[i] == side[i - 1] and side[i] != 0 else 1
        if run == 8:
            alerts.append(f"R4: 8 consecutive points on one side of mean ending at index {i}")
            break
    if nominal:
        bias = (mean - nominal) / nominal
        if abs(bias) > bias_tol_frac:
            alerts.append(f"BIAS: mean {mean:.1f} vs nominal {nominal:.1f} Å ({bias:+.2%})")
    return alerts


def summarize(con: sqlite3.Connection, bias_tol_frac: float = 0.005,
              baseline_n: int | None = None) -> list[SpcSummary]:
    """One summary per standard_id. If `baseline_n` is given, limits are
    computed from the first N points only (frozen baseline); otherwise from all."""
    out = []
    ids = [r[0] for r in con.execute("SELECT DISTINCT standard_id FROM standards ORDER BY 1")]
    for sid in ids:
        rows = con.execute(
            "SELECT measured_at, measured, nominal FROM standards WHERE standard_id=? ORDER BY measured_at",
            (sid,),
        ).fetchall()
        vals = np.array([r["measured"] for r in rows], dtype=float)
        base = vals[:baseline_n] if baseline_n else vals
        mean, sigma = float(base.mean()), float(base.std(ddof=1)) if len(base) > 1 else 0.0
        nominal = rows[0]["nominal"]
        out.append(SpcSummary(
            standard_id=sid, nominal=nominal, n=len(vals), mean=mean, sigma=sigma,
            ucl=mean + 3 * sigma, lcl=mean - 3 * sigma,
            bias_frac=(mean - nominal) / nominal if nominal else None,
            alerts=_rules(vals, mean, sigma, nominal, bias_tol_frac),
            series=[(r["measured_at"], r["measured"]) for r in rows],
        ))
    return out


def plot(summaries: list[SpcSummary], out_dir: Path) -> list[Path]:
    """Write one PNG control chart per standard. Requires matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for s in summaries:
        y = np.array([v for _, v in s.series])
        fig, ax = plt.subplots(figsize=(8, 3.5))
        ax.plot(y, marker="o", lw=1)
        ax.axhline(s.mean, color="k", lw=0.8, label=f"mean {s.mean:.1f} Å")
        ax.axhline(s.ucl, color="r", ls="--", lw=0.8, label="±3σ")
        ax.axhline(s.lcl, color="r", ls="--", lw=0.8)
        if s.nominal:
            ax.axhline(s.nominal, color="g", ls=":", lw=0.8, label=f"nominal {s.nominal}")
        ax.set_title(f"{s.standard_id}  n={s.n}  σ={s.sigma:.1f} Å" + ("  ⚠ OUT OF CONTROL" if s.alerts else "  in control"))
        if s.alerts:
            ax.text(0.01, 0.97, "\n".join(s.alerts), transform=ax.transAxes, va="top", fontsize=7, color="r")
        ax.set_xlabel("scan #"); ax.set_ylabel("step height (Å)")
        ax.legend(loc="best", fontsize=8)
        fig.tight_layout()
        p = out_dir / f"spc_{s.standard_id}.png"
        fig.savefig(p, dpi=120); plt.close(fig)
        paths.append(p)
    return paths

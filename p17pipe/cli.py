"""Command line interface.

    python -m p17pipe synth            # build a synthetic team tree + inbox
    python -m p17pipe sweep [--loop]   # ingest from the team tree
    python -m p17pipe inbox [--move]   # ingest + route from the drop folder
    python -m p17pipe spc [--plot]     # standards control charts + alerts
    python -m p17pipe report           # per-order summary table
    python -m p17pipe exceptions       # files that could not be ingested
    python -m p17pipe parse FILE       # dump what the parser sees (debug a real export)
    python -m p17pipe validate FILE    # compare tool Roughness/Waviness vs our Gaussian filter
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import db, ingest, spc
from .config import Config
from .parser import parse_file


def _cfg(args) -> Config:
    kw = {}
    if args.team_root: kw["team_root"] = Path(args.team_root)
    if args.inbox: kw["inbox_dir"] = Path(args.inbox)
    if args.route_root: kw["route_root"] = Path(args.route_root)
    if args.out: kw["out_dir"] = Path(args.out)
    cfg = Config(**kw)
    if args.settle is not None:
        cfg.settle_seconds = args.settle
    return cfg


def cmd_synth(args, cfg: Config) -> None:
    from .synth import build_team_tree
    files = build_team_tree(cfg.team_root, cfg.inbox_dir)
    print(f"wrote {len(files)} synthetic exports under {cfg.team_root} and {cfg.inbox_dir}")


def cmd_sweep(args, cfg: Config) -> None:
    con = db.connect(cfg.db_path)
    if args.loop:
        ingest.run_forever(con, cfg, do_sweep=True, do_inbox=False)
    else:
        print(ingest.sweep(con, cfg))


def cmd_inbox(args, cfg: Config) -> None:
    con = db.connect(cfg.db_path)
    print(ingest.inbox(con, cfg, move=args.move))


def cmd_spc(args, cfg: Config) -> None:
    con = db.connect(cfg.db_path)
    sums = spc.summarize(con, baseline_n=args.baseline)
    if not sums:
        print("no standards scans in DB yet"); return
    for s in sums:
        print(f"{s.standard_id}: n={s.n} mean={s.mean:.1f} Å σ={s.sigma:.1f} Å "
              f"UCL={s.ucl:.1f} LCL={s.lcl:.1f}"
              + (f" bias={s.bias_frac:+.2%}" if s.bias_frac is not None else ""))
        for a in s.alerts:
            print(f"   ⚠ {a}")
    if args.plot:
        for p in spc.plot(sums, cfg.out_dir / "spc"):
            print("wrote", p)


def cmd_report(args, cfg: Config) -> None:
    con = db.connect(cfg.db_path)
    rows = con.execute("""
        SELECT s.operator, s.engineer, s.order_no, COUNT(*) AS n,
               AVG(r.step_height) AS mean_h, MIN(r.step_height) AS min_h,
               MAX(r.step_height) AS max_h, AVG(r.ra) AS mean_ra
        FROM scans s JOIN results r USING(scan_id)
        GROUP BY 1,2,3 ORDER BY 1,2,3""").fetchall()
    print(f"{'operator':<10}{'engineer':<10}{'order':<12}{'n':>4}{'mean h (Å)':>12}{'min':>10}{'max':>10}{'Ra (Å)':>9}")
    for r in rows:
        print(f"{r['operator']:<10}{r['engineer']:<10}{r['order_no']:<12}{r['n']:>4}"
              f"{r['mean_h']:>12.1f}{r['min_h']:>10.1f}{r['max_h']:>10.1f}{r['mean_ra']:>9.1f}")


def cmd_exceptions(args, cfg: Config) -> None:
    con = db.connect(cfg.db_path)
    for r in con.execute("SELECT seen_at, path, reason FROM exceptions ORDER BY seen_at DESC LIMIT ?", (args.limit,)):
        print(f"{r['seen_at']}  {r['path']}\n    {r['reason']}")


def cmd_parse(args, cfg: Config) -> None:
    scan = parse_file(args.file)
    print("header:")
    for k, v in scan.header.items():
        print(f"  {k}: {v}")
    print("columns:", scan.columns)
    print(f"points: {len(scan.z)}  x[0..2]={scan.x_um[:3]} um  z[0..2]={scan.z[:3]} A")
    if scan.extra:
        print("extra columns:", ", ".join(scan.extra))


def cmd_validate(args, cfg: Config) -> None:
    """Compare the tool's own Roughness/Waviness columns with our Gaussian filter
    at several cutoffs; reports the best-matching cutoff and residual."""
    import numpy as np
    from .analysis import gaussian_waviness
    scan = parse_file(args.file)
    if "waviness" not in scan.extra:
        print("no Waviness column in this export"); return
    x, z, w_tool = scan.x_um, scan.z, scan.extra["waviness"]
    print(f"Normal == Roughness + Waviness: {np.allclose(z, scan.extra['roughness'] + w_tool, atol=1e-3)}")
    best = None
    for cutoff in (args.cutoffs or [8, 25, 80, 250, 800, 2500]):
        w = gaussian_waviness(x, z, float(cutoff))
        core = slice(int(len(x) * 0.1), int(len(x) * 0.9))  # ignore edge handling
        rms = float(np.sqrt(np.mean((w[core] - w_tool[core]) ** 2)))
        print(f"cutoff {cutoff:>7} um: RMS diff vs tool = {rms:8.2f} Å")
        best = min(best, (rms, cutoff)) if best else (rms, cutoff)
    print(f"best: {best[1]} um ({best[0]:.2f} Å RMS)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="p17pipe", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team-root"); ap.add_argument("--inbox"); ap.add_argument("--route-root"); ap.add_argument("--out")
    ap.add_argument("--settle", type=int, help="seconds a file must be unchanged before ingest (0 for tests)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("synth").set_defaults(fn=cmd_synth)
    p = sub.add_parser("sweep"); p.add_argument("--loop", action="store_true"); p.set_defaults(fn=cmd_sweep)
    p = sub.add_parser("inbox"); p.add_argument("--move", action="store_true"); p.set_defaults(fn=cmd_inbox)
    p = sub.add_parser("spc"); p.add_argument("--plot", action="store_true"); p.add_argument("--baseline", type=int); p.set_defaults(fn=cmd_spc)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    p = sub.add_parser("exceptions"); p.add_argument("--limit", type=int, default=50); p.set_defaults(fn=cmd_exceptions)
    p = sub.add_parser("validate"); p.add_argument("file"); p.add_argument("--cutoffs", type=float, nargs="*"); p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("parse"); p.add_argument("file"); p.set_defaults(fn=cmd_parse)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    args.fn(args, _cfg(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# p17pipe

Ingestion, analysis and SPC pipeline for KLA Tencor P-17 stylus-profilometer exports.

Two ingestion paths converge on one SQLite database:

| Path | Where metadata comes from | When to use |
|---|---|---|
| `sweep` | folder structure `<team_root>/<operator>/<engineer>/<order>/…` | today's workflow; polls a network share |
| `inbox` | encoded file name `OPERATOR_ENGINEER_ORDER_site.txt` in one drop folder | once sequence auto-export to a fixed path is confirmed |

Both parse the export, re-analyse the profile in Python, and write `scans` / `results` / `profiles` / `standards` / `exceptions` rows. A `registry` table (path, size, mtime, sha256) makes every pass idempotent; identical content found twice is stored once.

## Quick start (synthetic data, no instrument needed)

```bash
pip install -e ".[plot,dev]"
python -m p17pipe --settle 0 synth     # fake team tree + inbox under ./data
python -m p17pipe --settle 0 sweep     # ingest from the tree
python -m p17pipe --settle 0 inbox     # ingest + route from the drop folder
python -m p17pipe report               # per-order step-height summary
python -m p17pipe spc --plot --baseline 10
python -m p17pipe exceptions
pytest -q
```

`--settle` is the number of seconds a file must be unchanged before it is read (default 20, for files still being written to a share). Set 0 for local testing.

Point it at real locations with flags or env vars: `--team-root` / `P17_TEAM_ROOT`, `--inbox` / `P17_INBOX`, `--route-root` / `P17_ROUTE_ROOT`, `--out` / `P17_OUT`. Run continuously with `sweep --loop` (interval `P17_POLL`, default 180 s).

## Safety rails

- The sweep never writes into the team tree. The DB, charts and logs live under `--out`.
- Inbox routing copies into `--route-root`, which defaults to a **separate** mirror. Point it at the real team tree only after the tool owner signs off; `--move` is opt-in.
- Files that don't fit the path or naming convention go to `exceptions` and are left in place.

## Export formats handled

Two ASCII layouts are supported and auto-detected:

- **Profiler v9.x** (confirmed on a P-17 running 9.10): tab-separated `Key<TAB>value` header (`Data`, `Recipe`, `Site Name`, `Points`, `X-Resolution`, `X-Coord`, `Y-Coord`), then a column row `Raw / Intermediate / Normal / Roughness / Waviness` with an unnamed leading index column and **no Distance column** (x = index × X-Resolution). No date, force, speed or stylus in the header, so the scan timestamp falls back to file mtime.
- **Legacy / documented layout** (P-15/P-7 era): four positional header lines, then a column row containing `Normal` and a `Distance` column.

Units are stored exactly as the tool exports them: **z in Å, x in µm**. Nothing converts. Every `results` and `standards` value is in Å; filter cutoffs and step sizes are in µm. `scans.z_unit` / `scans.x_unit` record this per row.

```bash
python -m p17pipe parse path/to/export.txt      # what the parser recovered
python -m p17pipe validate path/to/export.txt   # tool Roughness/Waviness vs our Gaussian filter
```

`validate` uses the fact that v9.x exports satisfy `Normal = Roughness + Waviness`, so the tool's own filter output is a built-in reference: it sweeps cutoffs and reports which one reproduces the tool's waviness and the residual in Å.

## Analysis

`p17pipe/analysis.py` reimplements the measurements so they can be validated against the tool:

- **Step height** — least-squares level on the left cursor band (reference plane), then mean(right band) − mean(left band). Default bands are the first/last 15 % of the scan (`cursor_fraction`).
- **Roughness/waviness** — ISO 16610-21 Gaussian filter (`roughness_cutoff_um`, default 80 µm) with truncated-kernel edge renormalisation; Ra, Rq, Rt (Å) reported on the reference band.

Every `results` row carries `leveling_method`, `filter_cutoff_um` and `analysis_version` so the numbers are reproducible after the algorithms change.

## SPC

Scans whose recipe matches `standard_recipe_pattern` (default `STD|STANDARD|CAL`) also land in `standards`; nominals are configured in Å (`standard_nominals`). `spc` reports mean, σ, ±3σ limits, bias vs nominal, and flags Western Electric R1 (beyond 3σ) and R4 (8 in a row one side) plus a bias rule. `--baseline N` freezes limits on the first N points so drift shows up instead of inflating σ.

## Layout

```
p17pipe/
  config.py    all site-specific settings (paths, naming regex, cursor defaults, nominals)
  parser.py    tolerant ASCII export parser
  analysis.py  leveling, step height, Gaussian roughness
  routing.py   path → metadata and name → metadata
  db.py        schema + registry helpers
  ingest.py    sweep / inbox / shared ingest
  spc.py       control-chart summaries and plots
  synth.py     synthetic exports for tests and demos
  cli.py       command line
tests/
  test_pipeline.py
```

## Roadmap

- Recipe-specific cursor sets (instead of the 15 % default) stored in config.
- Streamlit dashboard over the SQLite DB.
- SECS/GEM host (`secsgem`) if the GEM option is licensed and approved.

import random
from pathlib import Path

import numpy as np
import pytest

from p17pipe import db, ingest, spc
from p17pipe.analysis import analyze, default_regions, gaussian_waviness, step_height
from p17pipe.config import Config
from p17pipe.parser import parse_text
from p17pipe.routing import RoutingError, meta_from_name, meta_from_path
from p17pipe.synth import build_team_tree, make_profile, write_export


# --- parser ------------------------------------------------------------------

DOC_LAYOUT = """scan001.dat
STEP_1um
5
0.500000
Point\tDistance\tNormal\tLateral\tForce
0\t0.0000\t0.001\t0\t2
1\t0.5000\t0.002\t0\t2
2\t1.0000\t1.001\t0\t2
3\t1.5000\t1.003\t0\t2
4\t2.0000\t0.999\t0\t2
"""


def test_parser_documented_layout():
    s = parse_text(DOC_LAYOUT)
    assert s.header["data_file"] == "scan001.dat"
    assert s.recipe == "STEP_1um"
    assert s.get_float("n_points") == 5
    assert s.step_size_um == pytest.approx(0.5)
    assert s.columns[2] == "Normal"
    assert len(s.z) == 5
    assert s.x_um[1] == pytest.approx(0.5)
    assert s.z[2] == pytest.approx(1.001)


def test_parser_keyvalue_header_and_footer():
    txt = DOC_LAYOUT.replace("Point\tDistance", "Applied Force: 2.0 mg\nSampling Rate: 200 Hz\nPoint\tDistance")
    txt += "Step Height\t1.000\n"  # non-numeric footer must stop the data block
    s = parse_text(txt)
    assert s.get_float("force_mg") == 2.0
    assert s.get_float("sampling_rate_hz") == 200
    assert len(s.z) == 5


def test_parser_two_column_no_header():
    s = parse_text("0\t0.1\n1\t0.2\n2\t0.3\n")
    assert len(s.z) == 3 and s.z[-1] == pytest.approx(0.3)


V9_LAYOUT = (
    "Data\tC21\r\nRecipe\t_DELAYERING\r\nSite Name\t\r\nPoints\t5\r\n"
    "X-Resolution\t0.500751\r\nX-Coord\t-1234.56\r\nY-Coord\t7890.12\r\n"
    "\tRaw\tIntermediate\tNormal\tRoughness\tWaviness\r\n"
    "1\t-64000\t0\t8000\t-10\t8010\r\n"
    "2\t-64000.2\t-0.2\t8001\t-9\t8010\r\n"
    "3\t-63990\t10\t8002\t-8\t8010\r\n"
    "4\t-63980\t20\t8003\t-7\t8010\r\n"
    "5\t-63970\t30\t8004\t-6\t8010\r\n"
)


def test_parser_v9_tab_layout():
    s = parse_text(V9_LAYOUT)
    assert s.header["data_file"] == "C21"
    assert s.recipe == "_DELAYERING"
    assert s.header["site_name"] == ""
    assert s.get_float("n_points") == 5
    assert s.get_float("x_coord_um") == pytest.approx(-1234.56)
    assert s.columns == ["Index", "Raw", "Intermediate", "Normal", "Roughness", "Waviness"]
    assert s.x_um[1] == pytest.approx(0.500751)          # from X-Resolution, no Distance column
    assert s.z[2] == pytest.approx(8002)                  # Normal column, Å as exported
    assert s.extra["waviness"][0] == pytest.approx(8010)
    assert np.allclose(s.z, s.extra["roughness"] + s.extra["waviness"])


def test_v9_synthetic_roundtrip_recovers_step(tmp_path):
    from p17pipe.parser import parse_file
    from p17pipe.synth import write_export_v9
    x, z = make_profile(height=12340.0, tilt_a_per_mm=3000.0, noise=0.0, roughness=0.0)
    p = write_export_v9(tmp_path / "c1.txt", x, z, "_DELAYERING", "C1")
    s = parse_file(p)
    res = analyze(s.x_um, s.z)
    assert res.step_height == pytest.approx(12340.0, abs=0.1)


# --- analysis ----------------------------------------------------------------

def test_step_height_recovers_known_step_with_tilt():
    x, z = make_profile(height=12340.0, tilt_a_per_mm=8000.0, noise=0.0, roughness=0.0)
    h, _ = step_height(x, z, default_regions(x))
    assert h == pytest.approx(12340.0, abs=1e-2)


def test_step_height_with_noise_within_spec():
    x, z = make_profile(height=8000.0, noise=20.0, roughness=0.0, rng=random.Random(3))
    res = analyze(x, z)
    assert res.step_height == pytest.approx(8000.0, abs=50)  # 50 Å: left-band slope noise extrapolated across the scan


def test_gaussian_filter_passes_dc_and_attenuates_cutoff():
    x = np.arange(0, 4000, 0.5)
    cutoff = 80.0
    # DC passes untouched
    assert np.allclose(gaussian_waviness(x, np.full_like(x, 3.0), cutoff), 3.0)
    # a sine at exactly the cutoff is transmitted at ~50 % (ISO 16610-21 definition)
    z = np.sin(2 * np.pi * x / cutoff)
    w = gaussian_waviness(x, z, cutoff)
    mid = slice(len(x) // 4, 3 * len(x) // 4)
    assert np.abs(w[mid]).max() == pytest.approx(0.5, abs=0.02)


# --- routing -----------------------------------------------------------------

def test_meta_from_path(tmp_path):
    root = tmp_path / "team"
    f = root / "jdoe" / "rlee" / "ORD1" / "sub" / "a.txt"
    f.parent.mkdir(parents=True); f.write_text("x")
    m = meta_from_path(f, root)
    assert (m.operator, m.engineer, m.order) == ("jdoe", "rlee", "ORD1")
    with pytest.raises(RoutingError):
        meta_from_path(root / "jdoe" / "a.txt", root)


def test_meta_from_name():
    cfg = Config(team_root=Path("/tmp/x1"), inbox_dir=Path("/tmp/x2"), route_root=Path("/tmp/x3"), out_dir=Path("/tmp/x4"))
    m = meta_from_name("JDOE_SMITH_48213_siteA", cfg)
    assert (m.operator, m.engineer, m.order, m.site) == ("JDOE", "SMITH", "48213", "siteA")
    assert meta_from_name("JDOE_SMITH_48213", cfg).site is None
    with pytest.raises(RoutingError):
        meta_from_name("just_two", cfg)


# --- end to end --------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path):
    c = Config(team_root=tmp_path / "team", inbox_dir=tmp_path / "inbox",
               route_root=tmp_path / "routed", out_dir=tmp_path / "out")
    c.settle_seconds = 0
    return c


def test_sweep_is_idempotent_and_logs_exceptions(cfg):
    files = build_team_tree(cfg.team_root, cfg.inbox_dir, n_orders=1, scans_per_order=2, n_std=5)
    con = db.connect(cfg.db_path)
    s1 = ingest.sweep(con, cfg)
    assert s1.ingested > 0 and s1.exceptions == 1  # the stray file
    n_scans = con.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
    assert n_scans == s1.ingested
    s2 = ingest.sweep(con, cfg)
    assert s2.ingested == 0 and s2.skipped == s1.seen
    assert con.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == n_scans
    exc = con.execute("SELECT path, reason FROM exceptions").fetchall()
    assert len(exc) == 1 and "too shallow" in exc[0]["reason"]
    # standards were recognised
    assert con.execute("SELECT COUNT(*) FROM standards").fetchone()[0] == 10  # 2 operators x 5


def test_duplicate_content_in_two_places_stored_once(cfg):
    x, z = make_profile()
    a = write_export(cfg.team_root / "a" / "e" / "o1" / "s.txt", x, z, "R", "s.dat", when=None)
    b = cfg.team_root / "a" / "e" / "o2" / "copy.txt"
    b.parent.mkdir(parents=True); b.write_bytes(a.read_bytes())
    con = db.connect(cfg.db_path)
    st = ingest.sweep(con, cfg)
    assert st.ingested == 1 and st.skipped == 1


def test_inbox_routes_and_flags_bad_names(cfg):
    build_team_tree(cfg.team_root, cfg.inbox_dir, n_orders=1, scans_per_order=1, n_std=1)
    con = db.connect(cfg.db_path)
    st = ingest.inbox(con, cfg)
    assert st.ingested == 2 and st.exceptions == 1
    assert (cfg.route_root / "JDOE" / "RLEE" / "48123" / "JDOE_RLEE_48123_siteA.txt").exists()
    assert (cfg.inbox_dir / "badname.txt").exists()  # left in place for a human
    row = con.execute("SELECT site FROM scans WHERE order_no='48123'").fetchone()
    assert row["site"] == "siteA"


def test_spc_detects_drift(cfg):
    build_team_tree(cfg.team_root, None, n_orders=1, scans_per_order=1, n_std=20)
    con = db.connect(cfg.db_path)
    ingest.sweep(con, cfg)
    sums = spc.summarize(con, baseline_n=10)
    assert len(sums) == 1 and sums[0].standard_id == "STD_1UM"
    assert sums[0].nominal == 10000.0
    assert any(a.startswith("R1") or a.startswith("R4") for a in sums[0].alerts)

"""Central configuration for the P-17 data pipeline.

Everything site-specific lives here so the rest of the package stays generic.
Override via environment variables or by editing a copy of this file.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    # --- Locations -----------------------------------------------------------
    # Team tree on the site server: <team_root>/<operator>/<engineer>/<order>/files
    team_root: Path = Path(os.environ.get("P17_TEAM_ROOT", "./data/team"))
    # Single drop folder used by the inbox path (sequence auto-export target)
    inbox_dir: Path = Path(os.environ.get("P17_INBOX", "./data/inbox"))
    # Where routed copies go. Defaults to a *separate* mirror so the pipeline
    # never writes into other users' folders. Point it at team_root only once
    # the tool owner has signed off.
    route_root: Path = Path(os.environ.get("P17_ROUTE_ROOT", "./data/routed"))
    # Pipeline outputs (DB, charts, logs) — always outside the team tree
    out_dir: Path = Path(os.environ.get("P17_OUT", "./out"))
    db_path: Path = field(default=None)  # type: ignore[assignment]

    # --- Sweep ---------------------------------------------------------------
    poll_seconds: int = int(os.environ.get("P17_POLL", "180"))
    export_extensions: tuple[str, ...] = (".txt", ".asc", ".csv", ".dat.txt")
    # Minimum age before a file is considered fully written (network shares)
    settle_seconds: int = 20

    # --- Naming convention for inbox mode ------------------------------------
    # Example: JDOE_SMITH_48213_siteA.txt  ->  operator=JDOE engineer=SMITH order=48213
    # Adjust once you have Table 2.3 (allowed special characters) from the tool.
    name_pattern: re.Pattern = re.compile(
        r"^(?P<operator>[A-Za-z0-9]+)_(?P<engineer>[A-Za-z0-9]+)_(?P<order>[A-Za-z0-9-]+)(?:_(?P<site>.+?))?$"
    )

    # Units are stored as the tool exports them: z in Å, x in µm. No conversion.
    z_unit: str = "A"
    x_unit: str = "um"

    # --- Analysis defaults ---------------------------------------------------
    # Fraction of the scan used as the left/right leveling+step regions when a
    # recipe-specific cursor set is not configured.
    cursor_fraction: float = 0.15
    # ISO 16610-21 Gaussian cutoff (µm) for roughness/waviness separation
    roughness_cutoff_um: float = 80.0
    analysis_version: str = "0.1.0"

    # --- Standards / SPC -----------------------------------------------------
    # Recipes whose name matches this are treated as step-height-standard scans
    standard_recipe_pattern: re.Pattern = re.compile(r"(STD|STANDARD|CAL)", re.I)
    # Map standard_id (extracted from recipe name) -> nominal step height (Å)
    standard_nominals: dict[str, float] = field(
        default_factory=lambda: {"STD_1UM": 10000.0, "STD_10UM": 100000.0}
    )

    def __post_init__(self) -> None:
        if self.db_path is None:
            self.db_path = self.out_dir / "p17.sqlite"
        for p in (self.team_root, self.inbox_dir, self.route_root, self.out_dir):
            Path(p).mkdir(parents=True, exist_ok=True)


DEFAULT = Config()

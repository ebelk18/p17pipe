"""Derive (operator, engineer, order, site) metadata from either the folder
path inside the team tree or an encoded sample name.

Both ingestion paths converge on the same `Meta` object.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config


class RoutingError(ValueError):
    """Raised when a file cannot be assigned to an operator/engineer/order."""


@dataclass(frozen=True)
class Meta:
    operator: str
    engineer: str
    order: str
    site: str | None = None

    def relative_dir(self) -> Path:
        return Path(self.operator) / self.engineer / self.order


def meta_from_path(path: Path, team_root: Path) -> Meta:
    """<team_root>/<operator>/<engineer>/<order>/[subdirs/]file -> Meta.

    Anything deeper than order is tolerated (users make subfolders); anything
    shallower is an exception.
    """
    try:
        rel = Path(path).resolve().relative_to(Path(team_root).resolve())
    except ValueError as e:
        raise RoutingError(f"not under team root: {path}") from e
    parts = rel.parts
    if len(parts) < 4:  # operator / engineer / order / file
        raise RoutingError(f"path too shallow ({len(parts)-1} dirs, need 3): {rel}")
    return Meta(operator=parts[0], engineer=parts[1], order=parts[2])


def meta_from_name(stem: str, cfg: Config) -> Meta:
    """Parse an encoded sample/sequence name, e.g. JDOE_SMITH_48213_siteA."""
    m = cfg.name_pattern.match(stem)
    if not m:
        raise RoutingError(f"name does not match convention: {stem!r}")
    return Meta(
        operator=m["operator"],
        engineer=m["engineer"],
        order=m["order"],
        site=m.group("site"),
    )


def route_destination(meta: Meta, filename: str, route_root: Path) -> Path:
    """Where a routed copy of an inbox file should land."""
    return Path(route_root) / meta.relative_dir() / filename

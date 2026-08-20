"""Synthetic multi-floor office buildings.

Generated rather than hand-authored so that accuracy can be measured across many layouts
instead of one lucky one. A solver tuned to a single floor plan is a solver that has
memorised, not one that works.

AP placement matters more than it looks. The default is a ceiling grid, which is both what
real deployments do and the arrangement that makes z unobservable -- every AP sits at the
same height, so the anchors are coplanar and there is no vertical baseline to solve
against. That is not a limitation of the simulator; it is the situation the engine has to
survive, so it is the default.
"""

from __future__ import annotations

from dataclasses import dataclass

from spectra_core.models import AccessPoint, Floor, Site, Vec3, Zone

from .propagation import Segment


@dataclass(frozen=True, slots=True)
class BuildingSpec:
    width_m: float = 40.0
    depth_m: float = 30.0
    floors: int = 3
    floor_height_m: float = 3.5
    ap_grid_cols: int = 3
    ap_grid_rows: int = 2
    ap_mount_height_m: float = 2.9
    rooms_across: int = 4
    rooms_deep: int = 3

    def __post_init__(self) -> None:
        if self.floors < 1:
            raise ValueError("a building needs at least one floor")
        if self.ap_grid_cols < 1 or self.ap_grid_rows < 1:
            raise ValueError("AP grid must have at least one row and column")


def _grid_positions(count: int, extent: float) -> list[float]:
    """Evenly spaced positions inset from the walls, as APs are in practice."""
    if count == 1:
        return [extent / 2.0]
    step = extent / count
    return [step * (i + 0.5) for i in range(count)]


def interior_walls(spec: BuildingSpec) -> tuple[Segment, ...]:
    """Partition walls forming a simple room grid, with a corridor gap in each run.

    The gaps matter: a fully sealed grid makes every path cross the same number of walls,
    which accidentally turns wall attenuation into a constant the solver can absorb into
    the path-loss intercept. Doorways break that symmetry.
    """
    walls: list[Segment] = []
    for x in _grid_positions(spec.rooms_across, spec.width_m)[:-1] if spec.rooms_across > 1 else []:
        gap_lo = spec.depth_m * 0.45
        gap_hi = spec.depth_m * 0.55
        walls.append(((x, 0.0), (x, gap_lo)))
        walls.append(((x, gap_hi), (x, spec.depth_m)))
    for y in _grid_positions(spec.rooms_deep, spec.depth_m)[:-1] if spec.rooms_deep > 1 else []:
        gap_lo = spec.width_m * 0.45
        gap_hi = spec.width_m * 0.55
        walls.append(((0.0, y), (gap_lo, y)))
        walls.append(((gap_hi, y), (spec.width_m, y)))
    return tuple(walls)


def build_site(spec: BuildingSpec | None = None, site_id: str = "sim-site") -> Site:
    spec = spec or BuildingSpec()
    floors = tuple(
        Floor(
            id=f"floor-{level}",
            level=level,
            elevation_m=level * spec.floor_height_m,
            height_m=spec.floor_height_m,
            name=f"Level {level}",
        )
        for level in range(spec.floors)
    )

    xs = _grid_positions(spec.ap_grid_cols, spec.width_m)
    ys = _grid_positions(spec.ap_grid_rows, spec.depth_m)
    aps = tuple(
        AccessPoint(
            id=f"ap-{floor.level}-{ci}-{ri}",
            position=Vec3(x=x, y=y, z=floor.elevation_m + spec.ap_mount_height_m),
            floor_id=floor.id,
        )
        for floor in floors
        for ci, x in enumerate(xs)
        for ri, y in enumerate(ys)
    )

    zone_xs = _grid_positions(spec.rooms_across, spec.width_m)
    zone_ys = _grid_positions(spec.rooms_deep, spec.depth_m)
    half_w = spec.width_m / spec.rooms_across / 2.0
    half_d = spec.depth_m / spec.rooms_deep / 2.0
    zones = tuple(
        Zone(
            id=f"{floor.id}-room-{ci}-{ri}",
            floor_id=floor.id,
            name=f"L{floor.level} Room {ci}{ri}",
            polygon=(
                (cx - half_w, cy - half_d),
                (cx + half_w, cy - half_d),
                (cx + half_w, cy + half_d),
                (cx - half_w, cy + half_d),
            ),
        )
        for floor in floors
        for ci, cx in enumerate(zone_xs)
        for ri, cy in enumerate(zone_ys)
    )

    return Site(id=site_id, name="Simulated Office", floors=floors,
                access_points=aps, zones=zones)


def walls_by_floor(site: Site, spec: BuildingSpec) -> dict[str, tuple[Segment, ...]]:
    shared = interior_walls(spec)
    return {floor.id: shared for floor in site.floors}

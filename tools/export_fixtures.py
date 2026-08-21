"""Export simulator geometry as JSON fixtures for the web client.

The viewer is built against a static file before any server exists. The usual objection
is that this creates a throwaway data path that drifts from the real one — that objection
is defeated by generating the fixture from the *same domain models* the API will serialise,
and by having CI regenerate it and fail on any diff.

The fixture is also not throwaway on its own terms: it stays the offline PWA demo, the
vitest fixture, and the Playwright golden. A viewer that renders correctly with no backend
is a viewer whose rendering bugs are actually rendering bugs.

Axis note, stated once here and once in the TypeScript: the site frame is metres,
right-handed, +z up. Three.js is y-up. The mapping is world = (site.x, elevation, site.y),
applied in exactly one place on the client.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for pkg in ("core", "engine", "sim"):
    sys.path.insert(0, str(ROOT / "packages" / pkg))

from spectra_core.models import Site  # noqa: E402
from spectra_sim.building import (  # noqa: E402
    BuildingSpec,
    build_site,
    interior_walls,
)

OUT_DIR = ROOT / "apps" / "web" / "public" / "fixtures"


def site_to_json(site: Site, spec: BuildingSpec) -> dict[str, Any]:
    """Flatten the site into what the renderer needs, and nothing more.

    Deliberately omits path-loss coefficients and anything else the client has no business
    knowing. The wire format is a projection of the domain model, not a dump of it.
    """
    walls = interior_walls(spec)
    return {
        "schema": 1,
        "site_id": site.id,
        "name": site.name,
        "extent": {"width_m": spec.width_m, "depth_m": spec.depth_m},
        "floors": [
            {
                "id": f.id,
                "level": f.level,
                "name": f.name,
                "elevation_m": f.elevation_m,
                "height_m": f.height_m,
            }
            for f in sorted(site.floors, key=lambda f: f.level)
        ],
        "access_points": [
            {
                "id": ap.id,
                "floor_id": ap.floor_id,
                "x": ap.position.x,
                "y": ap.position.y,
                "z": ap.position.z,
            }
            for ap in site.access_points
        ],
        "zones": [
            {
                "id": z.id,
                "floor_id": z.floor_id,
                "name": z.name,
                "polygon": [list(pt) for pt in z.polygon],
            }
            for z in site.zones
        ],
        # Walls are shared across floors in the generated building, but the client must not
        # assume that -- a real site has different partitions per storey.
        "walls": [
            {"floor_id": f.id, "a": list(seg[0]), "b": list(seg[1])}
            for f in site.floors
            for seg in walls
        ],
    }


def main() -> int:
    spec = BuildingSpec()
    site = build_site(spec)
    payload = site_to_json(site, spec)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "site.json"
    # sort_keys and a trailing newline so the CI freshness check diffs cleanly rather
    # than churning on dict ordering.
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        f"wrote {target.relative_to(ROOT)}: "
        f"{len(payload['floors'])} floors, "
        f"{len(payload['access_points'])} APs, "
        f"{len(payload['zones'])} zones, "
        f"{len(payload['walls'])} wall segments"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

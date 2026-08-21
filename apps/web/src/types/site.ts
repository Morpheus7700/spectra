/**
 * Site geometry as emitted by tools/export_fixtures.py.
 *
 * THE AXIS MAPPING LIVES HERE AND NOWHERE ELSE.
 *
 * The site frame is metres, right-handed, +z up (see Vec3 in spectra_core.models).
 * Three.js is y-up. Every project that leaves this conversion implicit ends up with it
 * re-derived in four components and two of them wrong, so it is declared once, exported,
 * and imported everywhere it is needed.
 */

export interface SiteExtent {
  width_m: number;
  depth_m: number;
}

export interface FloorJson {
  id: string;
  level: number;
  name: string;
  elevation_m: number;
  height_m: number;
}

export interface AccessPointJson {
  id: string;
  floor_id: string;
  x: number;
  y: number;
  z: number;
}

export interface ZoneJson {
  id: string;
  floor_id: string;
  name: string;
  polygon: [number, number][];
}

export interface WallJson {
  floor_id: string;
  a: [number, number];
  b: [number, number];
}

export interface SiteJson {
  schema: 1;
  site_id: string;
  name: string;
  extent: SiteExtent;
  floors: FloorJson[];
  access_points: AccessPointJson[];
  zones: ZoneJson[];
  walls: WallJson[];
}

/** Site (x, y, z) → three.js world (x, y, z). The only place this conversion exists. */
export function toWorld(x: number, y: number, z = 0): [number, number, number] {
  return [x, z, y];
}

/**
 * A rotation about the site's vertical axis, expressed for three.js.
 *
 * Mapping (x, y, z) → (x, z, y) swaps two axes, which flips handedness, so a rotation
 * that is counter-clockwise in the site frame is clockwise about world-Y. The angle must
 * therefore be negated. Test this with an ASYMMETRIC input — a symmetric one passes
 * either sign and hides the bug.
 */
export function toWorldRotationY(siteAngleRad: number): number {
  return -siteAngleRad;
}

/** Floors ordered as a person thinks of them: top of the building first. */
export function floorsTopDown(site: SiteJson): FloorJson[] {
  return [...site.floors].sort((a, b) => b.level - a.level);
}

export function floorById(site: SiteJson, id: string): FloorJson | undefined {
  return site.floors.find((f) => f.id === id);
}

import { useMemo } from "react";
import { DoubleSide } from "three";

/**
 * Radar-style range rings on the floor, and the observer at the centre.
 *
 * Concentric shells have no scale on their own -- a nest of glowing spheres could be 3 m or
 * 30 m across. The rings put a metric grid under them: labelled circles at fixed radii, the
 * way a PPI scope or a depth sounder does. They also anchor the "you are here" read that makes
 * the whole view egocentric rather than a floating abstraction.
 */

const RING_RADII_M = [2, 5, 10, 15, 20];
const SEGMENTS = 96;

export function RangeRings({ maxRadius }: { maxRadius: number }) {
  const rings = useMemo(
    () => RING_RADII_M.filter((r) => r <= maxRadius + 3),
    [maxRadius],
  );

  return (
    <group>
      {/* the observer: a small emissive marker at the origin, this PC */}
      <mesh position={[0, 0.15, 0]}>
        <sphereGeometry args={[0.35, 24, 24]} />
        <meshBasicMaterial color="#eafff5" />
      </mesh>
      <pointLight position={[0, 1, 0]} intensity={8} distance={6} color="#8affc8" />

      {rings.map((radius) => (
        <mesh key={radius} rotation={[-Math.PI / 2, 0, 0]}>
          {/* a thin flat annulus reads as a crisp ring from the oblique camera */}
          <ringGeometry args={[radius - 0.04, radius + 0.04, SEGMENTS]} />
          <meshBasicMaterial color="#1f3a44" side={DoubleSide} transparent opacity={0.9} />
        </mesh>
      ))}
    </group>
  );
}

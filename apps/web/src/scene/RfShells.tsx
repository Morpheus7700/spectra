import { useMemo } from "react";
import { BackSide, DoubleSide, type ColorRepresentation } from "three";

import { useMode } from "../data/mode";
import { bandColor, sigmaSwampsRange, type LiveShell } from "../types/live";

/**
 * One translucent sphere per radio, radius = best-estimate range, centred on the observer.
 *
 * The honest object is a shell, not a point: one receiver gives a distance, and a distance is
 * a sphere. So the sphere IS the datum, not decoration around a point that does not exist.
 *
 * Rendering choices, all in service of reading ~14 overlapping translucent surfaces at once:
 * - additive blending, depthWrite off, BackSide. Overlaps sum, so regions where several shells
 *   coincide glow brighter -- denser evidence reads as brighter, which happens to be true.
 *   BackSide draws the far wall of each sphere, so you see into the nest instead of at a wall.
 * - one shared unit-sphere geometry, scaled per shell. No per-frame allocation (the shell set
 *   changes at 1 Hz; the geometry never does).
 * - the uncertainty band is drawn as a second, fainter sphere at range + sigma. sigma is often
 *   larger than range on this hardware, so that outer sphere can contain the observer -- which
 *   is a true statement about the data ("it could be right here"), not a bug to hide.
 */

const SEGMENTS = 48;

interface ShellMeshProps {
  radius: number;
  color: ColorRepresentation;
  opacity: number;
}

function ShellSurface({ radius, color, opacity }: ShellMeshProps) {
  return (
    <mesh scale={radius}>
      <sphereGeometry args={[1, SEGMENTS, SEGMENTS]} />
      <meshBasicMaterial
        color={color}
        transparent
        opacity={opacity}
        side={BackSide}
        depthWrite={false}
        blending={2 /* AdditiveBlending */}
      />
    </mesh>
  );
}

export function RfShells({ shells }: { shells: LiveShell[] }) {
  const hoveredId = useMode((s) => s.hoveredId);

  // Sort so the user's own router draws last (on top of the additive stack) and reads clearly.
  const ordered = useMemo(
    () => [...shells].sort((a, b) => Number(a.own) - Number(b.own)),
    [shells],
  );

  return (
    <group>
      {ordered.map((shell) => {
        const focused = hoveredId === shell.id;
        const dimmed = hoveredId !== null && !focused;
        const color = shell.own ? "#8affc8" : bandColor(shell.band_ghz);

        // Own router gets a firmer surface; neighbours are fainter and recede when another
        // row is focused, so a selection actually isolates one shell from the nest.
        const base = shell.own ? 0.2 : 0.12;
        const opacity = dimmed ? base * 0.25 : focused ? base * 2.4 : base;
        const outer = shell.range_m + shell.sigma_m;
        const inner = Math.max(shell.range_m - shell.sigma_m, 0);

        return (
          <group key={shell.id}>
            {/* best-estimate surface */}
            <ShellSurface radius={shell.range_m} color={color} opacity={opacity} />
            {/* uncertainty extent -- the outer edge of where the AP might be */}
            <ShellSurface radius={outer} color={color} opacity={opacity * 0.4} />
            {/* inner edge only when sigma does NOT swamp range; otherwise it collapses to a
                dot at the origin and adds nothing but a bright speck on the observer */}
            {inner > 0.5 && !sigmaSwampsRange(shell) && (
              <ShellSurface radius={inner} color={color} opacity={opacity * 0.4} />
            )}
            {/* a crisp equator at exactly range_m. The translucent fog says "somewhere near
                here"; this one bright line says "the measured distance is precisely this",
                which is the number the panel is quoting. */}
            <mesh rotation={[-Math.PI / 2, 0, 0]}>
              <ringGeometry args={[shell.range_m - 0.06, shell.range_m + 0.06, 96]} />
              <meshBasicMaterial
                color={color}
                transparent
                opacity={dimmed ? 0.15 : focused ? 0.9 : 0.45}
                side={DoubleSide}
                depthWrite={false}
              />
            </mesh>
          </group>
        );
      })}
    </group>
  );
}

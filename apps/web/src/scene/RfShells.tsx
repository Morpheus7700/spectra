import { Billboard, Text } from "@react-three/drei";
import { useMemo } from "react";
import { AdditiveBlending, BackSide, DoubleSide, type ColorRepresentation } from "three";

import { useMode } from "../data/mode";
import { bandColor, bandLabel, sigmaSwampsRange, type LiveShell } from "../types/live";

/**
 * One shell per radio: a wireframe sphere at the measured range, centred on the observer.
 *
 * The honest object is a shell, not a point -- one receiver gives a distance, and a distance
 * is a sphere. Earlier this drew only translucent fog, which read as a single fuzzy ball with
 * no structure. A shell has to look like a shell, so each one now carries:
 *   - a wireframe sphere (the lat/long cage), which is what makes it read as a hollow surface
 *     rather than a solid blob;
 *   - a bright equator ring at exactly range_m -- the crisp "the measured distance is THIS"
 *     line the panel is quoting;
 *   - a fainter equator at range + sigma, the outer edge of where the AP might be;
 *   - a faint additive fill for glow and depth;
 *   - a floating label naming the radio and its range, always facing the camera.
 *
 * One shared unit geometry per primitive, scaled per shell. Nothing is allocated per frame;
 * the shell set changes at 1 Hz and the geometry never does.
 */

const LAT = 24;
const LON = 32;

interface Layer {
  radius: number;
  color: ColorRepresentation;
  opacity: number;
}

function WireShell({ radius, color, opacity }: Layer) {
  return (
    <mesh scale={radius}>
      <sphereGeometry args={[1, LON, LAT]} />
      <meshBasicMaterial color={color} wireframe transparent opacity={opacity} depthWrite={false} />
    </mesh>
  );
}

function GlowShell({ radius, color, opacity }: Layer) {
  return (
    <mesh scale={radius}>
      <sphereGeometry args={[1, LON, LAT]} />
      <meshBasicMaterial
        color={color}
        transparent
        opacity={opacity}
        side={BackSide}
        depthWrite={false}
        blending={AdditiveBlending}
      />
    </mesh>
  );
}

function EquatorRing({ radius, color, opacity }: Layer) {
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]}>
      <ringGeometry args={[radius - 0.05, radius + 0.05, 128]} />
      <meshBasicMaterial color={color} transparent opacity={opacity} side={DoubleSide} depthWrite={false} />
    </mesh>
  );
}

export function RfShells({ shells }: { shells: LiveShell[] }) {
  const hoveredId = useMode((s) => s.hoveredId);

  // Own router draws last so its brighter surface sits on top of the additive stack.
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

        const k = dimmed ? 0.25 : focused ? 2.2 : 1;
        const cage = (shell.own ? 0.28 : 0.16) * k;
        const glow = (shell.own ? 0.1 : 0.05) * k;
        const outer = shell.range_m + shell.sigma_m;

        return (
          <group key={shell.id}>
            <GlowShell radius={shell.range_m} color={color} opacity={glow} />
            <WireShell radius={shell.range_m} color={color} opacity={cage} />
            <EquatorRing radius={shell.range_m} color={color} opacity={Math.min(1, cage * 3)} />
            {!sigmaSwampsRange(shell) && (
              <EquatorRing radius={outer} color={color} opacity={cage} />
            )}

            {/* Label only the own router (always) and whatever is hovered. Labelling every
                shell piled the small central ones into an unreadable stack; the panel already
                lists them all, so the 3D label is for the one radio you are attending to. The
                label rides the shell's TOP pole, so nested shells put their labels at different
                heights and never collide. */}
            {(shell.own || focused) && (
              <Billboard position={[0, outer + 0.9, 0]}>
                <Text
                  fontSize={0.95}
                  color={color}
                  anchorX="center"
                  anchorY="bottom"
                  outlineWidth={0.04}
                  outlineColor="#04070a"
                >
                  {`${shell.label.split(" -- ")[0]}  ·  ${shell.range_m.toFixed(1)}m ±${shell.sigma_m.toFixed(1)}  ·  ${bandLabel(shell.band_ghz)}`}
                </Text>
              </Billboard>
            )}
          </group>
        );
      })}
    </group>
  );
}

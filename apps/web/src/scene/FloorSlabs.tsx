import { Line } from "@react-three/drei";
import { useMemo } from "react";

import { useUi } from "../data/store";
import type { FloorJson, SiteJson, WallJson, ZoneJson } from "../types/site";

/**
 * Emphasis, never deletion.
 *
 * Exactly one floor is at full opacity. Others are dimmed rather than removed: deleting
 * them would destroy the vertical mental model, which is the only reason to render a
 * building in 3D at all. Floors above the focus are hidden by default because from an
 * oblique camera they occlude the one being looked at.
 */
function floorOpacity(
  floor: FloorJson,
  focus: FloorJson | undefined,
  showAbove: boolean,
): number {
  if (!focus) return 0.25;
  if (floor.id === focus.id) return 1;
  if (floor.level > focus.level) return showAbove ? 0.05 : 0;
  return 0.08;
}

function ZoneOutline({ zone, y, opacity }: { zone: ZoneJson; y: number; opacity: number }) {
  const points = useMemo(() => {
    const pts = zone.polygon.map(([x, z]) => [x, y, z] as [number, number, number]);
    const first = pts[0];
    return first ? [...pts, first] : pts;
  }, [zone.polygon, y]);

  if (points.length < 2) return null;
  return (
    <Line points={points} color="#39485c" lineWidth={1} transparent opacity={opacity} />
  );
}

function WallSegment({ wall, y, opacity }: { wall: WallJson; y: number; opacity: number }) {
  return (
    <Line
      points={[
        [wall.a[0], y, wall.a[1]],
        [wall.b[0], y, wall.b[1]],
      ]}
      color="#8fa4c0"
      lineWidth={2.5}
      transparent
      opacity={opacity}
    />
  );
}

export function FloorSlabs({ site }: { site: SiteJson }) {
  const focusFloorId = useUi((s) => s.focusFloorId);
  const showAbove = useUi((s) => s.showFloorsAbove);
  const focus = site.floors.find((f) => f.id === focusFloorId);

  const { width_m: w, depth_m: d } = site.extent;

  return (
    <group>
      {site.floors.map((floor) => {
        const opacity = floorOpacity(floor, focus, showAbove);
        if (opacity === 0) return null;
        const y = floor.elevation_m;

        return (
          <group key={floor.id}>
            {/* Slab. Rendered at the floor's own elevation so the stack reads as a
                building rather than a pile of unrelated plans. */}
            <mesh position={[w / 2, y - 0.02, d / 2]} rotation={[-Math.PI / 2, 0, 0]}>
              <planeGeometry args={[w, d]} />
              <meshBasicMaterial
                color="#0e141c"
                transparent
                opacity={opacity * 0.92}
                depthWrite={false}
              />
            </mesh>

            {/* Outer envelope */}
            <Line
              points={[
                [0, y, 0],
                [w, y, 0],
                [w, y, d],
                [0, y, d],
                [0, y, 0],
              ]}
              color="#b8c6d9"
              lineWidth={2}
              transparent
              opacity={opacity}
            />

            {site.zones
              .filter((z) => z.floor_id === floor.id)
              .map((zone) => (
                <ZoneOutline key={zone.id} zone={zone} y={y} opacity={opacity * 0.7} />
              ))}

            {site.walls
              .filter((wl) => wl.floor_id === floor.id)
              .map((wall, i) => (
                <WallSegment key={`${floor.id}-${i}`} wall={wall} y={y} opacity={opacity} />
              ))}
          </group>
        );
      })}
    </group>
  );
}

import { Grid, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useMemo } from "react";

import { outerRadius, type LiveSnapshot } from "../types/live";
import { RangeRings } from "./RangeRings";
import { RfShells } from "./RfShells";

/**
 * The live RF scene: observer at the origin, shells around it, rings for scale.
 *
 * Egocentric by construction. There is no floor plan and none is needed -- with one receiver
 * everything is measured relative to this PC, so the PC is the origin and stays there. The
 * camera frames the whole nest from the largest shell present, so it neither clips the outer
 * uncertainty band nor leaves the small shells as a dot in the middle of an empty scene.
 *
 * WebGL, not WebGPU, deliberately -- Windows headless Chrome cannot capture WebGPU frames, so
 * that choice keeps the scene inspectable on the machine it is built on.
 */
export function RfViewer({ snapshot }: { snapshot: LiveSnapshot | null }) {
  const shells = snapshot?.shells ?? [];

  const extent = useMemo(() => {
    const max = shells.reduce((m, s) => Math.max(m, outerRadius(s)), 0);
    return Math.max(max, 8); // never frame tighter than the innermost few metres
  }, [shells]);

  const cam = extent * 1.7;

  return (
    <Canvas
      camera={{ position: [cam, cam * 0.72, cam], fov: 45, near: 0.1, far: extent * 12 }}
      gl={{ antialias: true }}
    >
      <color attach="background" args={["#06090c"]} />
      <ambientLight intensity={0.4} />

      <RangeRings maxRadius={extent} />
      <RfShells shells={shells} />

      <Grid
        args={[extent * 3, extent * 3]}
        cellSize={1}
        cellColor="#0e1a20"
        sectionSize={5}
        sectionColor="#16303a"
        fadeDistance={extent * 4}
        infiniteGrid
      />

      <OrbitControls
        enableDamping
        dampingFactor={0.08}
        minPolarAngle={Math.PI / 14}
        maxPolarAngle={(Math.PI * 82) / 180}
        target={[0, 0, 0]}
      />
    </Canvas>
  );
}

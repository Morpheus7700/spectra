import { Grid, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";

import { SiteScene } from "./SiteScene";

/**
 * M0: the canvas exists and the camera orbits. Nothing more.
 *
 * Deliberately the default WebGL renderer, not WebGPU. Headless Chrome on Windows
 * cannot capture WebGPU canvas presentation -- screenshots come out black -- which
 * would silently kill visual regression testing on the machine this is built on.
 *
 * Camera distance is set from the building extent (40 x 30 m), not guessed: too close
 * and the floor foreshortens into a wedge. Roughly 40 degrees of elevation reads as a
 * plan you can still see depth in.
 *
 * The camera starts oblique rather than top-down: a top-down view collapses an
 * uncertainty prism into a flat ellipse and destroys the "volume standing on a
 * floor" read that the whole visual language depends on.
 */
export function Viewer() {
  return (
    <Canvas
      camera={{ position: [44, 32, 46], fov: 45, near: 0.1, far: 600 }}
      gl={{ antialias: true }}
    >
      <color attach="background" args={["#0b0d10"]} />
      <ambientLight intensity={0.7} />
      <directionalLight position={[20, 30, 10]} intensity={1.1} />

      <SiteScene />

      <Grid
        args={[40, 30]}
        cellSize={1}
        cellColor="#1c2128"
        sectionSize={5}
        sectionColor="#2d3540"
        fadeDistance={90}
        infiniteGrid
      />

      <OrbitControls
        enableDamping
        dampingFactor={0.08}
        minPolarAngle={Math.PI / 12}
        maxPolarAngle={(Math.PI * 80) / 180}
        screenSpacePanning={false}
        target={[20, 0, 15]}
      />
    </Canvas>
  );
}

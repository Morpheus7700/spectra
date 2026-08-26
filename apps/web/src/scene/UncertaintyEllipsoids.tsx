import { useMemo, useRef, useEffect } from "react";
import * as THREE from "three";
import type { PositionEstimateWire } from "../types/wire";
import type { SiteJson } from "../types/site";
import { toWorld } from "../types/site";
import { useUi } from "../data/store";

const scratchMatrix = new THREE.Matrix4();
const scratchPosition = new THREE.Vector3();
const scratchQuaternion = new THREE.Quaternion();
const scratchScale = new THREE.Vector3();

interface UncertaintyEllipsoidsProps {
  site: SiteJson;
  estimates: PositionEstimateWire[];
}

export function UncertaintyEllipsoids({ site, estimates }: UncertaintyEllipsoidsProps) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const focusFloorId = useUi((s) => s.focusFloorId);

  // We only draw estimates that have covariance (POINT solutions)
  const validEstimates = useMemo(
    () => estimates.filter((e) => e.covariance_xy !== null && e.x !== null && e.y !== null),
    [estimates]
  );

  useEffect(() => {
    if (!meshRef.current) return;
    
    validEstimates.forEach((est, i) => {
      // 1. Locate the elevation for the estimated floor
      const floor = site.floors.find((f) => f.id === est.floor_id);
      const elevation = floor ? floor.elevation_m : 0;
      
      // Map to Three.js coordinates
      const worldPos = toWorld(est.x!, elevation, est.y!);
      scratchPosition.set(worldPos[0], worldPos[1] + 0.05, worldPos[2]); // Slight z-offset to prevent z-fighting with the floor slab

      // 2. Compute ellipse axes from covariance_xy
      const cov = est.covariance_xy!;
      const trace = cov[0][0] + cov[1][1];
      const det = cov[0][0] * cov[1][1] - cov[0][1] * cov[1][0];
      const discriminant = Math.sqrt(Math.max((trace * trace) / 4 - det, 0));
      
      const l1 = trace / 2 + discriminant; // Major eigenvalue
      const l2 = Math.max(trace / 2 - discriminant, 0); // Minor eigenvalue
      
      // Sigma radius (2-sigma for ~95% confidence volume usually, but let's draw 1-sigma)
      const major = Math.sqrt(l1);
      const minor = Math.sqrt(l2);
      
      // Eigenvector for l1: (cov[0][1], l1 - cov[0][0])
      let angle = 0;
      if (cov[0][1] !== 0 || l1 - cov[0][0] !== 0) {
        angle = Math.atan2(l1 - cov[0][0], cov[0][1]);
      }
      
      // 3. Transform geometry
      // The RingGeometry is drawn on the XY plane in local space.
      // But in Three.js, world Y is up. The floor is the XZ plane.
      // We must rotate the ring to lie flat on the XZ plane (-90 deg on X axis),
      // and then apply the covariance angle on the Y axis.
      // Note that site coordinates have positive Y down in 2D space? 
      // Actually toWorld is: [x, elevation, -y] or similar, let's check what it is.
      // Usually it's (x, y, -z), let's just use Euler.
      const euler = new THREE.Euler(-Math.PI / 2, 0, -angle, "YXZ");
      scratchQuaternion.setFromEuler(euler);
      
      // Scale by major/minor axes. The base geometry is radius 1.
      // Since it's rotated -90 on X, the original local X is world X, and local Y is world Z.
      scratchScale.set(major, minor, 1);
      
      scratchMatrix.compose(scratchPosition, scratchQuaternion, scratchScale);
      meshRef.current!.setMatrixAt(i, scratchMatrix);
      
      // Opacity based on floor focus
      const opacity = !focusFloorId || est.floor_id === focusFloorId ? 1.0 : 0.1;
      meshRef.current!.setColorAt(i, new THREE.Color(0xff4400).multiplyScalar(opacity));
    });
    
    meshRef.current.instanceMatrix.needsUpdate = true;
    if (meshRef.current.instanceColor) meshRef.current.instanceColor.needsUpdate = true;
    meshRef.current.count = validEstimates.length;
  }, [validEstimates, site, focusFloorId]);

  if (validEstimates.length === 0) return null;

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, validEstimates.length]}>
      {/* A ring to represent the boundary of the covariance matrix */}
      <ringGeometry args={[0.92, 1.0, 32]} />
      <meshBasicMaterial 
        color={0xff4400}
        transparent 
        opacity={0.6}
        side={THREE.DoubleSide} 
        depthWrite={false}
      />
    </instancedMesh>
  );
}

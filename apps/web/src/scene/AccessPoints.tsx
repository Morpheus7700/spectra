import { useMemo, useRef } from "react";
import * as THREE from "three";

import { useUi } from "../data/store";
import type { SiteJson } from "../types/site";

const UP = new THREE.Object3D();

/**
 * Access points, instanced.
 *
 * There are only 18 in the demo building, so instancing is not needed for performance
 * here — it is needed because a real deployment has hundreds, and the pattern should be
 * established before it matters rather than retrofitted under pressure.
 *
 * APs are drawn at their true mounted height (z ≈ 2.9 m above the floor), which is the
 * visual reminder that the anchors are coplanar on a ceiling plane — the geometric fact
 * that makes vertical position unobservable and forces floor to be classified rather
 * than solved (R7).
 */
export function AccessPoints({ site }: { site: SiteJson }) {
  const focusFloorId = useUi((s) => s.focusFloorId);
  const ref = useRef<THREE.InstancedMesh>(null);

  const visible = useMemo(
    () => site.access_points.filter((ap) => ap.floor_id === focusFloorId),
    [site.access_points, focusFloorId],
  );

  const geometry = useMemo(() => new THREE.SphereGeometry(0.32, 12, 8), []);
  const material = useMemo(
    () => new THREE.MeshBasicMaterial({ color: "#7fd4ff" }),
    [],
  );

  // Instance count changes only when the focused floor changes, so composing the
  // matrices during render is correct here. Anything that moves per frame belongs in
  // useFrame against a typed array instead.
  const key = `${focusFloorId}-${visible.length}`;

  return (
    <instancedMesh
      key={key}
      ref={(node) => {
        ref.current = node;
        if (!node) return;
        visible.forEach((ap, i) => {
          UP.position.set(ap.x, ap.z, ap.y);
          UP.updateMatrix();
          node.setMatrixAt(i, UP.matrix);
        });
        node.count = visible.length;
        node.instanceMatrix.needsUpdate = true;
      }}
      args={[geometry, material, Math.max(visible.length, 1)]}
    />
  );
}

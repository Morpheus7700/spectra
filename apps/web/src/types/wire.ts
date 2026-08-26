export type SolutionKind = "point" | "zone_only" | "unknown";

export interface PositionEstimateWire {
  tenant_id: string;
  target_id: string;
  site_id: string;
  estimated_at: string;
  kind: SolutionKind;
  
  x: number | null;
  y: number | null;
  
  // Raw 2D covariance matrix: ((xx, xy), (yx, yy))
  covariance_xy: [[number, number], [number, number]] | null;
  
  floor_id: string | null;
  floor_confidence: number;
  zone_id: string | null;
  zone_confidence: number;
  
  downgrade_reason: string | null;
}

import { create } from "zustand";

/**
 * Which of the two honest views is on screen.
 *
 * "live"  -- what this one fixed receiver actually measures: shells, not points.
 * "sim"   -- what the same engine does when real infrastructure exists (P0 multilateration).
 *
 * They are never blended. R13: simulator figures describe the simulator. The switch exists so
 * the difference is a deliberate choice the viewer makes, not a thing the UI quietly conflates.
 */
export type ViewMode = "live" | "sim";

interface ModeState {
  mode: ViewMode;
  /** Panel row the pointer is over, or that was clicked. Drives the one-shell highlight. */
  hoveredId: string | null;
  setMode: (mode: ViewMode) => void;
  hover: (id: string | null) => void;
}

export const useMode = create<ModeState>((set) => ({
  mode: "live",
  hoveredId: null,
  setMode: (mode) => set({ mode }),
  hover: (hoveredId) => set({ hoveredId }),
}));

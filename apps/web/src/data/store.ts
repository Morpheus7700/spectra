import { create } from "zustand";

import type { SiteJson } from "../types/site";

/**
 * UI state ONLY.
 *
 * Entity positions never live here. They belong in a typed-array buffer outside React,
 * written directly by the feed with zero setState — 500 entities at 1 Hz through React
 * state would destroy the frame budget, and it is the single most likely performance
 * mistake in this codebase.
 */
interface UiState {
  site: SiteJson | null;
  focusFloorId: string | null;
  selectedId: string | null;
  showFloorsAbove: boolean;

  setSite: (site: SiteJson) => void;
  setFocusFloor: (id: string) => void;
  select: (id: string | null) => void;
  toggleFloorsAbove: () => void;
}

export const useUi = create<UiState>((set) => ({
  site: null,
  focusFloorId: null,
  selectedId: null,
  // Floors above the focused one occlude it from an oblique camera, so they are hidden
  // by default rather than merely dimmed.
  showFloorsAbove: false,

  setSite: (site) =>
    set({
      site,
      focusFloorId:
        [...site.floors].sort((a, b) => a.level - b.level)[0]?.id ?? null,
    }),
  setFocusFloor: (focusFloorId) => set({ focusFloorId }),
  select: (selectedId) => set({ selectedId }),
  toggleFloorsAbove: () => set((s) => ({ showFloorsAbove: !s.showFloorsAbove })),
}));

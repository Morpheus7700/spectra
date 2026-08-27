/**
 * What the panel needs from the feed, and nothing more.
 *
 * The hook exposes raw timestamps; the panel wants an age in seconds. Keeping that derivation
 * here means the panel is a pure function of already-computed values and stays trivial to read.
 */
export {
  bandColor,
  bandLabel,
  sigmaSwampsRange,
  type LiveShell,
  type LiveSnapshot,
} from "../types/live";

export interface LiveFeedLike {
  snapshot: import("../types/live").LiveSnapshot | null;
  status: string;
  detail: string | null;
  ageSeconds: number | null;
}

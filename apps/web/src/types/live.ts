/**
 * The live RF snapshot written by the local collector to /live.json at ~1 Hz.
 *
 * WHY THIS SHAPE HAS NO POSITIONS.
 *
 * This machine has one WiFi receiver, fixed in place. One receiver cannot localise:
 * there is no baseline, so there is no bearing, so there is no point solution. What a
 * single receiver can produce is a per-AP *range* with a large uncertainty. The AP is
 * therefore somewhere on a spherical shell of radius `range_m` and thickness `2 * sigma_m`
 * centred on this PC.
 *
 * Emitting an (x, y) here would be R8 ("never fake a solve") violated at the wire.
 * The contract is fixed by the collector; do not extend it with coordinates.
 */

export interface LiveObserver {
  label: string;
  band_note: string;
}

export interface LiveShell {
  id: string;
  label: string;
  own: boolean;
  band_ghz: number;
  rssi_dbm: number;
  /** Model output, not a measurement. `calibrated` is false for everything today (R9). */
  range_m: number;
  sigma_m: number;
  calibrated: boolean;
  stale: boolean;
}

/** An AP the collector deliberately declined to range. Not an error — R8 working. */
export interface LiveRefusal {
  id: string;
  reason: string;
}

export interface LiveSnapshot {
  measured_at: string;
  observer: LiveObserver;
  shells: LiveShell[];
  refusals: LiveRefusal[];
  notes: string[];
  /** Entries the file contained that failed validation. Surfaced, never silently dropped. */
  malformed: number;
}

/**
 * Hue encodes wavelength, and is the only place band colour is decided.
 *
 * Longer wavelength reads warmer. This is a mnemonic that happens to be true, which is
 * the only kind worth using: 2.4 GHz (~125 mm) amber, 5 GHz (~60 mm) cyan, 6 GHz violet.
 */
export function bandColor(ghz: number): string {
  if (ghz < 3) return "#f7a83c";
  if (ghz < 5.6) return "#62cdff";
  return "#b58cff";
}

export function bandLabel(ghz: number): string {
  return `${Number.isInteger(ghz) ? ghz.toFixed(0) : ghz.toFixed(1)} GHz`;
}

/** Outer edge of a shell's uncertainty band. */
export function outerRadius(s: LiveShell): number {
  return s.range_m + s.sigma_m;
}

/**
 * Inner edge, floored at zero.
 *
 * sigma is frequently LARGER than range on this hardware, which means the band contains
 * the observer: "the AP could be right here" is a legitimate reading of the data. The
 * floor is a geometry guard only — it must never be mistaken for the band being narrower
 * than it is, which is why `sigmaSwampsRange` exists and the UI shouts about it.
 */
export function innerRadius(s: LiveShell): number {
  return Math.max(s.range_m - s.sigma_m, 0);
}

export function sigmaSwampsRange(s: LiveShell): boolean {
  return s.sigma_m >= s.range_m;
}

// ---------------------------------------------------------------------------
// Parsing. /live.json is written by another process: it is a trust boundary and
// gets validated, not cast. A malformed row is dropped and counted, never rendered.
// ---------------------------------------------------------------------------

type Json = Record<string, unknown>;

const isObj = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);

function parseShell(v: unknown): LiveShell | null {
  if (!isObj(v)) return null;
  const id = str(v.id);
  const range_m = num(v.range_m);
  const sigma_m = num(v.sigma_m);
  const rssi_dbm = num(v.rssi_dbm);
  const band_ghz = num(v.band_ghz);
  if (!id || range_m === null || sigma_m === null || rssi_dbm === null || band_ghz === null) return null;
  if (range_m <= 0 || sigma_m < 0) return null;
  return {
    id,
    label: str(v.label, id),
    own: v.own === true,
    band_ghz,
    rssi_dbm,
    range_m,
    sigma_m,
    calibrated: v.calibrated === true,
    stale: v.stale === true,
  };
}

function parseRefusal(v: unknown): LiveRefusal | null {
  if (!isObj(v)) return null;
  const id = str(v.id);
  const reason = str(v.reason);
  return id && reason ? { id, reason } : null;
}

/** Returns null if the payload is not a live snapshot at all. */
export function parseSnapshot(raw: unknown): LiveSnapshot | null {
  if (!isObj(raw) || !Array.isArray(raw.shells)) return null;

  const shells: LiveShell[] = [];
  let malformed = 0;
  for (const entry of raw.shells) {
    const shell = parseShell(entry);
    if (shell) shells.push(shell);
    else malformed += 1;
  }

  const refusals: LiveRefusal[] = [];
  if (Array.isArray(raw.refusals)) {
    for (const entry of raw.refusals) {
      const refusal = parseRefusal(entry);
      if (refusal) refusals.push(refusal);
      else malformed += 1;
    }
  }

  const observer = isObj(raw.observer) ? raw.observer : {};
  const notes = Array.isArray(raw.notes) ? raw.notes.filter((n): n is string => typeof n === "string") : [];

  return {
    measured_at: str(raw.measured_at),
    observer: { label: str(observer.label, "This PC"), band_note: str(observer.band_note) },
    shells,
    refusals,
    notes,
    malformed,
  };
}

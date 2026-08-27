import { useMode } from "../data/mode";
import {
  bandColor,
  bandLabel,
  sigmaSwampsRange,
  type LiveFeedLike,
  type LiveShell,
} from "./livePanelTypes";

/**
 * The instrument readout. Everything the collector measured, in the collector's own terms.
 *
 * It is an HTML overlay, not in-canvas text: readable at any zoom, selectable, and it survives
 * the black-frame WebGL capture failure that would swallow canvas text. The panel is also the
 * primary interface -- hovering a row isolates that one shell in the 3D nest, which is easier
 * than picking a translucent sphere out of a dozen overlapping ones.
 *
 * The refusals section is not an error state. It is R8 working: the system declining to range
 * an AP the data cannot support. It is shown with the same weight as the shells, never hidden.
 */
export function LivePanel({ feed }: { feed: LiveFeedLike }) {
  const hover = useMode((s) => s.hover);
  const { snapshot, status, detail, ageSeconds } = feed;

  return (
    <aside className="rf-panel">
      <header className="rf-panel__head">
        <div className="rf-panel__title">NEARBY WIFI</div>
        <StatusDot status={status} detail={detail} ageSeconds={ageSeconds} />
      </header>

      {snapshot && (
        <div className="rf-panel__observer">
          Heard by {snapshot.observer.label} · {snapshot.observer.band_note}
        </div>
      )}

      {!snapshot && (
        <p className="rf-panel__empty">
          Waiting for the collector. Run <code>uv run python -m tools.live_rf</code> and the
          shells appear here within a second.
        </p>
      )}

      {snapshot && snapshot.shells.length > 0 && (
        <section>
          <h2 className="rf-panel__label">
            Routers placed<span>{snapshot.shells.length}</span>
          </h2>
          <ul className="rf-rows">
            {snapshot.shells.map((shell) => (
              <ShellRow key={shell.id} shell={shell} onHover={hover} />
            ))}
          </ul>
        </section>
      )}

      {snapshot && snapshot.refusals.length > 0 && (
        <section>
          <h2 className="rf-panel__label rf-panel__label--refused">
            Too weak to place<span>{snapshot.refusals.length}</span>
          </h2>
          <p className="rf-refusal-note">
            Heard, but too faint for an honest distance — set aside rather than guessed.
          </p>
        </section>
      )}

      {snapshot && snapshot.notes.length > 0 && (
        <footer className="rf-notes">
          {snapshot.notes.map((note) => (
            <p key={note}>{note}</p>
          ))}
          {snapshot.malformed > 0 && (
            <p className="rf-notes__warn">
              {snapshot.malformed} row(s) in the file failed validation and were dropped.
            </p>
          )}
        </footer>
      )}
    </aside>
  );
}

function ShellRow({ shell, onHover }: { shell: LiveShell; onHover: (id: string | null) => void }) {
  const color = shell.own ? "#8affc8" : bandColor(shell.band_ghz);
  const swamped = sigmaSwampsRange(shell);

  return (
    <li
      className={`rf-row${shell.own ? " rf-row--own" : ""}`}
      onPointerEnter={() => onHover(shell.id)}
      onPointerLeave={() => onHover(null)}
    >
      <span className="rf-row__swatch" style={{ background: color }} aria-hidden />
      <div className="rf-row__body">
        <div className="rf-row__top">
          <span className="rf-row__label">{shell.own ? "Your WiFi" : shell.label}</span>
          <SignalBars rssi={shell.rssi_dbm} />
        </div>
        <div className="rf-row__range">
          <span className="rf-row__dist">
            {shell.range_m.toFixed(1)}<span className="rf-row__unit">m away</span>
          </span>
          <span className="rf-row__pm">give or take {shell.sigma_m.toFixed(1)}</span>
          <span className="rf-row__band">{bandLabel(shell.band_ghz)}</span>
        </div>
        {swamped && (
          <div className="rf-row__flag">so faint it could be right here, or much further</div>
        )}
      </div>
    </li>
  );
}

function SignalBars({ rssi }: { rssi: number }) {
  // The universal WiFi-bars mnemonic instead of a dBm number nobody outside the field reads.
  // -50 and up is full; -90 and below is one bar. Four bars over that range.
  const filled = Math.max(1, Math.min(4, Math.round((rssi + 92) / 11)));
  return (
    <span className="rf-bars" title={`${rssi} dBm`} aria-label={`signal ${filled} of 4`}>
      {[0, 1, 2, 3].map((i) => (
        <span key={i} className={`rf-bars__b${i < filled ? " is-on" : ""}`} />
      ))}
    </span>
  );
}

function StatusDot({
  status,
  detail,
  ageSeconds,
}: {
  status: string;
  detail: string | null;
  ageSeconds: number | null;
}) {
  const text =
    status === "live"
      ? ageSeconds !== null
        ? `live · ${ageSeconds.toFixed(0)}s`
        : "live"
      : status === "stale"
        ? "stale"
        : (detail ?? "waiting");
  return (
    <span className={`rf-status rf-status--${status}`}>
      <span className="rf-status__dot" />
      {text}
    </span>
  );
}

import { useMode, type ViewMode } from "../data/mode";

/**
 * Switch between the two honest views.
 *
 * Framed as what each one is, not as a neutral toggle: "Live RF" is this hardware; "Simulated"
 * is the engine on infrastructure that does not exist here. The label carries the R13 caveat so
 * a viewer is never left thinking the simulated metres describe their flat.
 */
const OPTIONS: { mode: ViewMode; label: string; sub: string }[] = [
  { mode: "live", label: "Live RF", sub: "this receiver" },
  { mode: "sim", label: "Simulated", sub: "engine on real infra" },
];

export function ModeSwitch() {
  const mode = useMode((s) => s.mode);
  const setMode = useMode((s) => s.setMode);

  return (
    <div className="mode-switch" role="tablist" aria-label="View">
      {OPTIONS.map((opt) => (
        <button
          key={opt.mode}
          role="tab"
          aria-selected={mode === opt.mode}
          className={`mode-switch__btn${mode === opt.mode ? " is-active" : ""}`}
          onClick={() => setMode(opt.mode)}
        >
          <span className="mode-switch__label">{opt.label}</span>
          <span className="mode-switch__sub">{opt.sub}</span>
        </button>
      ))}
    </div>
  );
}

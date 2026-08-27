import { useMode } from "./data/mode";
import { RfView } from "./scene/RfView";
import { Viewer } from "./scene/Viewer";
import { FloorSwitcher } from "./ui/FloorSwitcher";
import { ModeSwitch } from "./ui/ModeSwitch";

export function App() {
  const mode = useMode((s) => s.mode);

  return (
    <div className="app-root">
      {mode === "live" ? (
        <RfView />
      ) : (
        <>
          <Viewer />
          <FloorSwitcher />
        </>
      )}
      <ModeSwitch />
    </div>
  );
}

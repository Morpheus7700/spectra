import { Viewer } from "./scene/Viewer";
import { FloorSwitcher } from "./ui/FloorSwitcher";

export function App() {
  return (
    <div style={{ height: "100%", position: "relative" }}>
      <Viewer />
      <FloorSwitcher />
    </div>
  );
}

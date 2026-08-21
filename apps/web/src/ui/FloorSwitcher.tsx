import { useUi } from "../data/store";
import { floorsTopDown } from "../types/site";

/**
 * Top floor at the top.
 *
 * A list ordered level-0-first is itself a disorientation bug: it inverts the building
 * relative to what the user is looking at.
 */
export function FloorSwitcher() {
  const site = useUi((s) => s.site);
  const focusFloorId = useUi((s) => s.focusFloorId);
  const setFocusFloor = useUi((s) => s.setFocusFloor);
  const showAbove = useUi((s) => s.showFloorsAbove);
  const toggleAbove = useUi((s) => s.toggleFloorsAbove);

  if (!site) return null;

  return (
    <div
      style={{
        position: "absolute",
        top: 16,
        left: 16,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      {floorsTopDown(site).map((floor) => {
        const active = floor.id === focusFloorId;
        return (
          <button
            key={floor.id}
            type="button"
            aria-pressed={active}
            onClick={() => setFocusFloor(floor.id)}
            style={{
              padding: "8px 14px",
              minWidth: 96,
              textAlign: "left",
              cursor: "pointer",
              border: `1px solid ${active ? "#7fd4ff" : "#2d3540"}`,
              borderRadius: 6,
              background: active ? "#16222c" : "#11151b",
              color: active ? "#e6e8eb" : "#8b939e",
              font: "inherit",
            }}
          >
            {floor.name || floor.id}
          </button>
        );
      })}
      <button
        type="button"
        onClick={toggleAbove}
        aria-pressed={showAbove}
        style={{
          marginTop: 8,
          padding: "6px 10px",
          cursor: "pointer",
          border: "1px solid #2d3540",
          borderRadius: 6,
          background: "#11151b",
          color: "#8b939e",
          font: "inherit",
          fontSize: 12,
        }}
      >
        {showAbove ? "Hide floors above" : "Show floors above"}
      </button>
    </div>
  );
}

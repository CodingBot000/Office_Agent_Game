import type { AvailableGameAction } from "./types";

export function formatWorldObjectName(name: string): string {
  return name.trim().replace(/\s+/g, "-");
}

function formatWorldObjectId(objectId: string): string {
  return objectId
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("-");
}

export function GameActionLabel({ action }: { action: AvailableGameAction }) {
  const objectName = action.object_id ? formatWorldObjectId(action.object_id) : null;
  if (!objectName) return <>{action.label}</>;

  const actionVerb = {
    pick_up_object: "Pick up",
    break_held_object: "Break",
    drop_held_object: "Drop",
    inspect_object: "Inspect",
    throw_held_object: "Throw",
  }[action.family];
  const targetLabel = action.family === "throw_held_object" ? action.label.split(" at ").slice(1).join(" at ") : "";

  return (
    <>
      <span>{actionVerb} </span>
      <strong className="game-action-object-name">{objectName}</strong>
      {targetLabel && <span> at {targetLabel}</span>}
    </>
  );
}

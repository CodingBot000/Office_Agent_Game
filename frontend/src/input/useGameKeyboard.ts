import { useEffect, useLayoutEffect, useRef } from "react";
import { gameShortcut, isEditableTarget, isEditing, movementKey } from "./keyboardPolicy";

type Options = { movementBlocked: boolean; completed: boolean; onInteract: () => boolean; onInventory: () => void };

export function useGameKeyboard(options: Options) {
  const keys = useRef(new Set<string>());
  const latest = useRef(options);
  latest.current = options;
  useLayoutEffect(() => {
    if (options.movementBlocked || options.completed) keys.current.clear();
  }, [options.movementBlocked, options.completed]);

  useEffect(() => {
    const clear = () => keys.current.clear();
    const down = (event: KeyboardEvent) => {
      const current = latest.current;
      if (current.completed || event.defaultPrevented || isEditing(event)
        || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
      const movement = movementKey(event);
      if (movement && !current.movementBlocked) {
        event.preventDefault();
        keys.current.add(movement);
        return;
      }
      const shortcut = gameShortcut(event, current.movementBlocked);
      if (shortcut === "interact" && current.onInteract()) event.preventDefault();
      if (shortcut === "inventory") { event.preventDefault(); current.onInventory(); }
    };
    const up = (event: KeyboardEvent) => {
      const movement = movementKey(event);
      if (movement) keys.current.delete(movement);
    };
    const focus = (event: FocusEvent) => { if (isEditableTarget(event.target)) clear(); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up, true);
    window.addEventListener("blur", clear);
    document.addEventListener("focusin", focus);
    document.addEventListener("visibilitychange", clear);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up, true);
      window.removeEventListener("blur", clear);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("visibilitychange", clear);
      clear();
    };
  }, []);
  return keys;
}

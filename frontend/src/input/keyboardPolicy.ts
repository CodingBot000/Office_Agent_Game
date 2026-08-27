type Key = Pick<KeyboardEvent, "code" | "key">;

export function movementKey(event: Key): string | null {
  const keys: Record<string, string> = {
    KeyW: "w", KeyA: "a", KeyS: "s", KeyD: "d",
    ArrowUp: "arrowup", ArrowDown: "arrowdown", ArrowLeft: "arrowleft", ArrowRight: "arrowright",
  };
  return keys[event.code] ?? null;
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || Boolean(target.closest(
    'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"]',
  ));
}

export function isEditing(event: KeyboardEvent): boolean {
  return event.composedPath().some(isEditableTarget) || isEditableTarget(document.activeElement);
}

export function gameShortcut(event: KeyboardEvent, movementBlocked: boolean): "interact" | "inventory" | null {
  if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || event.isComposing || event.repeat || isEditing(event)) return null;
  if (event.code === "KeyE" && !movementBlocked) return "interact";
  if (event.code === "KeyI") return "inventory";
  return null;
}

export function isEnter(event: Key): boolean {
  return event.code === "Enter" || event.code === "NumpadEnter" || event.key === "Enter";
}

/** IME confirmation must never fall through into the form's implicit submit. */
export class ImeSubmitGuard {
  composing = false;
  private consumedEnter = false;

  start() { this.composing = true; }
  end() { this.composing = false; }
  reset() { this.composing = false; this.consumedEnter = false; }
  canSubmit() { return !this.composing && !this.consumedEnter; }

  keyDown(event: Pick<KeyboardEvent, "code" | "key" | "isComposing" | "keyCode" | "repeat">): boolean {
    if (!isEnter(event)) return false;
    if (this.composing || event.isComposing || event.keyCode === 229) {
      this.consumedEnter = true;
      return false;
    }
    return !event.repeat && this.canSubmit();
  }

  keyUp(event: Key) { if (isEnter(event)) this.consumedEnter = false; }
}

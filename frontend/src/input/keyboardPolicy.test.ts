import { describe, expect, it } from "vitest";
import { gameShortcut, ImeSubmitGuard, isEditableTarget, movementKey } from "./keyboardPolicy";

describe("physical game keys", () => {
  it.each([["KeyE", "e", "interact"], ["KeyE", "ㄷ", "interact"], ["KeyI", "ㅑ", "inventory"]])("maps %s/%s", (code, key, expected) => {
    expect(gameShortcut(new KeyboardEvent("keydown", { code, key }), false)).toBe(expected);
  });
  it("does not repeat toggles, consume browser shortcuts, or interact over a dialogue", () => {
    expect(gameShortcut(new KeyboardEvent("keydown", { code: "KeyI", repeat: true }), false)).toBeNull();
    expect(gameShortcut(new KeyboardEvent("keydown", { code: "KeyE", ctrlKey: true }), false)).toBeNull();
    expect(gameShortcut(new KeyboardEvent("keydown", { code: "KeyE" }), true)).toBeNull();
    expect(movementKey({ code: "KeyW", key: "ㅈ" })).toBe("w");
  });
  it("recognizes inputs and nested editable elements", () => {
    const input = document.createElement("input");
    document.body.append(input);
    input.focus();
    expect(gameShortcut(new KeyboardEvent("keydown", { code: "KeyI" }), false)).toBeNull();
    input.remove();
    const parent = document.createElement("div");
    parent.contentEditable = "true";
    parent.setAttribute("contenteditable", "true");
    const child = parent.appendChild(document.createElement("span"));
    expect(isEditableTarget(child)).toBe(true);
  });
});

describe("IME submit boundary", () => {
  const enter = { code: "Enter", key: "Enter", keyCode: 13, isComposing: false, repeat: false };
  it("consumes composition Enter until release, then permits a separate Enter", () => {
    const guard = new ImeSubmitGuard();
    guard.start();
    expect(guard.keyDown(enter)).toBe(false);
    guard.end();
    expect(guard.canSubmit()).toBe(false);
    expect(guard.keyDown(enter)).toBe(false);
    guard.keyUp(enter);
    expect(guard.keyDown(enter)).toBe(true);
  });
  it("handles compositionend before keydown with legacy IME keyCode 229", () => {
    const guard = new ImeSubmitGuard();
    guard.start(); guard.end();
    expect(guard.keyDown({ ...enter, keyCode: 229 })).toBe(false);
    expect(guard.canSubmit()).toBe(false);
    guard.keyUp(enter);
    expect(guard.keyDown({ ...enter, code: "NumpadEnter" })).toBe(true);
    expect(guard.keyDown({ ...enter, repeat: true })).toBe(false);
  });
});

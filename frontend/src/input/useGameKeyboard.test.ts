import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useGameKeyboard } from "./useGameKeyboard";

it("clears held movement on dialog open, input focus, blur and keyup in an input", () => {
  const options = { movementBlocked: false, completed: false, onInteract: vi.fn(() => true), onInventory: vi.fn() };
  const { result, rerender } = renderHook(useGameKeyboard, { initialProps: options });
  act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyW", key: "ㅈ", cancelable: true })); });
  expect(result.current.current.has("w")).toBe(true);
  rerender({ ...options, movementBlocked: true });
  expect(result.current.current.size).toBe(0);
  act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyD" })); });
  expect(result.current.current.size).toBe(0);
  rerender(options);
  const input = document.createElement("input");
  document.body.append(input);
  act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyW" })); input.focus(); });
  expect(result.current.current.size).toBe(0);
  const typing = new KeyboardEvent("keydown", { code: "KeyA", key: "ㅁ", bubbles: true, cancelable: true });
  act(() => { input.dispatchEvent(typing); });
  expect(typing.defaultPrevented).toBe(false);
  expect(result.current.current.size).toBe(0);
  result.current.current.add("w");
  act(() => { input.dispatchEvent(new KeyboardEvent("keyup", { code: "KeyW", bubbles: true })); });
  expect(result.current.current.size).toBe(0);
  input.remove();
  result.current.current.add("d");
  act(() => { window.dispatchEvent(new Event("blur")); });
  expect(result.current.current.size).toBe(0);
});

it("handles Korean E/I exactly once and keeps I available on a read-only tab", () => {
  const onInteract = vi.fn(() => true);
  const onInventory = vi.fn();
  const options = { movementBlocked: false, completed: false, onInteract, onInventory };
  const { rerender } = renderHook(useGameKeyboard, { initialProps: options });
  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyE", key: "ㄷ" }));
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyI", key: "ㅑ" }));
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyI", key: "ㅑ", repeat: true }));
  });
  expect(onInteract).toHaveBeenCalledOnce();
  expect(onInventory).toHaveBeenCalledOnce();
  rerender({ ...options, movementBlocked: true });
  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyE", key: "ㄷ" }));
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyI", key: "ㅑ" }));
  });
  expect(onInteract).toHaveBeenCalledOnce();
  expect(onInventory).toHaveBeenCalledTimes(2);
});

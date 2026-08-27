import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";
import { deferred, response, snapshot } from "./test/fixtures";
import type { ActionResponse } from "./types";

vi.mock("./api", async importOriginal => ({
  ...await importOriginal<typeof import("./api")>(), startSession: vi.fn(), submitAction: vi.fn(),
  submitGameAction: vi.fn(), resetSession: vi.fn(), submitReport: vi.fn(), getSession: vi.fn(),
}));
afterEach(() => { cleanup(); vi.useRealTimers(); });

async function enterGame() {
  vi.useFakeTimers();
  vi.mocked(api.startSession).mockResolvedValue(snapshot({ current_location: "qa_desk" }));
  await act(async () => { render(<App />); });
  fireEvent.click(screen.getByRole("button", { name: /VISUAL OFFICE/ }));
  await move("KeyD", "ㅇ", 200);
}

it("supports short pointer/keyboard direction button activation and disables it during dialogue", async () => {
  vi.useFakeTimers();
  const current = snapshot({ current_location: "qa_desk" });
  current.available_game_actions = [{ id: "throw_americano_coupon_at_qa_01", family: "throw_held_object", label: "Throw coupon", object_id: "americano_coupon", target_id: "qa_01", owner_id: "player", scope: "target", location: "qa_desk", enabled: true, disabled_reason: null }];
  vi.mocked(api.startSession).mockResolvedValue(current);
  await act(async () => { render(<App />); });
  fireEvent.click(screen.getByRole("button", { name: /VISUAL OFFICE/ }));
  expect(screen.getByRole("button", { name: "액션" })).toBeTruthy();
  const player = screen.getByRole("application").querySelector<HTMLElement>(".world-player")!;
  const before = player.getAttribute("style");
  fireEvent.click(screen.getByRole("button", { name: "오른쪽으로 이동" }));
  expect(player.getAttribute("style")).not.toBe(before);
  talk();
  expect(screen.queryByRole("button", { name: "액션" })).toBeNull();
  expect((screen.getByRole("button", { name: "오른쪽으로 이동" }) as HTMLButtonElement).disabled).toBe(true);
});

async function move(code: string, key: string, duration: number) {
  fireEvent.keyDown(window, { code, key, cancelable: true });
  await act(async () => { await vi.advanceTimersByTimeAsync(duration); });
  fireEvent.keyUp(window, { code, key });
}

function talk() {
  fireEvent.keyDown(window, { code: "KeyE", key: "ㄷ" });
  fireEvent.keyUp(window, { code: "KeyE", key: "ㄷ" });
  fireEvent.click(screen.getByRole("button", { name: "대화하기" }));
}

it("opens an in-world dialogue through Korean E without remounting or moving the world", async () => {
  const pending = deferred<ActionResponse>();
  vi.mocked(api.submitAction).mockReturnValue(pending.promise);
  await enterGame();
  const world = screen.getByRole("application");
  const player = world.querySelector<HTMLElement>(".world-player")!;
  const position = player.getAttribute("style");
  talk();
  expect(screen.getByRole("application")).toBe(world);
  expect(screen.getByRole("dialog").textContent).toContain("QA Engineer와 대화하기");
  fireEvent.click(screen.getByRole("tab", { name: "Backend" }));
  await move("KeyW", "ㅈ", 400);
  expect(player.getAttribute("style")).toBe(position);
  fireEvent.click(screen.getByRole("tab", { name: "QA" }));
  fireEvent.change(screen.getByRole("textbox", { name: "대화 내용" }), { target: { value: "질문" } });
  fireEvent.click(screen.getByRole("button", { name: "전송" }));
  expect(api.submitAction).toHaveBeenCalledExactlyOnceWith("session-1", "질문", undefined, "qa_01", expect.any(AbortSignal));
  fireEvent.click(screen.getByRole("button", { name: "닫기" }));
  await act(async () => { pending.resolve(response({ snapshot: snapshot({ revision: 2, turn: 1, current_location: "qa_desk" }) })); });
  fireEvent.click(screen.getByRole("button", { name: "대화하기" }));
  expect(screen.getByRole("log").textContent).toContain("NPC response");
  expect(player.getAttribute("style")).toBe(position);
});

it("queues the latest physical location while a closed dialog request is still running", async () => {
  const pending = deferred<ActionResponse>();
  vi.mocked(api.submitAction).mockImplementation(async (_id, _text, hint) => {
    if (hint?.intent === "move") return response({ snapshot: snapshot({ revision: 3, turn: 2, current_location: hint.location! }) });
    return pending.promise;
  });
  await enterGame(); talk();
  fireEvent.change(screen.getByRole("textbox", { name: "대화 내용" }), { target: { value: "기다리는 질문" } });
  fireEvent.click(screen.getByRole("button", { name: "전송" }));
  fireEvent.click(screen.getByRole("button", { name: "닫기" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(20); });
  await move("KeyA", "ㅁ", 900);
  expect(api.submitAction).toHaveBeenCalledOnce();
  await act(async () => { pending.resolve(response({ snapshot: snapshot({ revision: 2, turn: 1, current_location: "qa_desk" }) })); });
  expect(api.submitAction).toHaveBeenCalledTimes(2);
  expect(vi.mocked(api.submitAction).mock.calls[1][2]?.location).toBe("meeting_room");
});

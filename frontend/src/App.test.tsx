import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";
import { deferred, response, snapshot } from "./test/fixtures";
import type { ActionResponse, GameActionResponse } from "./types";

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

function throwSnapshot(objectId: string, name: string) {
  const current = snapshot();
  current.npcs[2].dynamic_state.emotion = "urgent";
  current.world_objects = [{ id: objectId, name, owner_id: "player", location: "meeting_room", evidence_id: null,
    portable: true, destructible: true, holder_id: "player", condition: "normal", throw_effect: "physical_assault",
    throw_severity: 5, throw_impact: "split", is_dropped: false }];
  current.player_inventory.held_object_ids = [objectId];
  current.available_game_actions = [{ id: `throw_${objectId}_at_qa_01`, family: "throw_held_object", label: `Throw ${name} at QA Engineer`,
    object_id: objectId, target_id: "qa_01", owner_id: "player", scope: "target", location: "meeting_room", enabled: true, disabled_reason: null }];
  return current;
}

it.each([
  ["team_leader_person", "팀장님"], ["division_head_person", "본부장님"], ["representative_person", "대표님"],
])("keeps %s intact in flight and shows the target's status above its role", async (objectId, name) => {
  vi.useFakeTimers();
  const pending = deferred<GameActionResponse>();
  const current = throwSnapshot(objectId, name);
  vi.mocked(api.startSession).mockResolvedValue(current);
  vi.mocked(api.submitGameAction).mockReturnValue(pending.promise);
  await act(async () => { render(<App />); });
  fireEvent.click(screen.getByRole("button", { name: /VISUAL OFFICE/ }));
  const target = screen.getByRole("button", { name: "QA Engineer 선택" });
  expect(target.querySelector(".world-character-status")?.textContent).toBe("초조");
  expect(target.querySelector(".world-character-status")?.nextElementSibling?.textContent).toBe("QA Engineer");
  expect(screen.getByRole("button", { name: "Backend Developer 선택" }).textContent).toContain("차분");
  fireEvent.click(screen.getByRole("button", { name: "액션" }));
  fireEvent.click(screen.getByRole("button", { name: `${name} 던지기` }));
  fireEvent.click(screen.getByRole("button", { name: "QA Engineer · QA Engineer" }));

  const world = screen.getByRole("application");
  const flying = world.querySelector(".thrown-person-object")!;
  expect(flying.querySelector(".thrown-person-spin img")?.getAttribute("src")).toBe(`/office-assets/items/${objectId}.png`);
  expect(flying.querySelector(".running-person-sprite, .break-half")).toBeNull();
  const origin = flying.getAttribute("style");
  fireEvent.click(screen.getByRole("button", { name: "오른쪽으로 이동" }));
  expect(flying.getAttribute("style")).toBe(origin);

  const next = throwSnapshot(objectId, name);
  next.revision = 2;
  next.npcs[2].physical_state = "comatose";
  next.npcs[2].is_fallen = true;
  next.npcs[2].dynamic_state.emotion = "shocked";
  await act(async () => { pending.resolve({ snapshot: next, action_id: current.available_game_actions[0].id, message: "투척 완료", blocked: false, alert: null }); });
  expect(target.querySelector(".world-character-status")?.textContent).not.toBe("혼수상태");
  await act(async () => { await vi.advanceTimersByTimeAsync(1360); });
  expect(target.querySelector(".world-character-status")?.textContent).toBe("혼수상태");
  expect(target.querySelector(".world-character-label")?.textContent).toBe("QA Engineer");
  expect(world.querySelector(".world-blink-effect img")?.getAttribute("src")).toBe(`/office-assets/items/${objectId}.png`);
  expect(world.querySelector(".world-break-effect, .break-half")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(480); });
  expect(world.querySelector(".thrown-world-object, .world-blink-effect")).toBeNull();
});

it("updates overhead emotion from the server and preserves ordinary object break effects", async () => {
  vi.useFakeTimers();
  const current = throwSnapshot("qa_keyboard", "QA-keyboard");
  const next = throwSnapshot("qa_keyboard", "QA-keyboard");
  next.revision = 2;
  next.npcs[2].dynamic_state.emotion = "worried";
  vi.mocked(api.startSession).mockResolvedValue(current);
  vi.mocked(api.submitGameAction).mockResolvedValue({ snapshot: next, action_id: current.available_game_actions[0].id,
    message: "투척 완료", blocked: false, alert: null });
  await act(async () => { render(<App />); });
  fireEvent.click(screen.getByRole("button", { name: /VISUAL OFFICE/ }));
  fireEvent.click(screen.getByRole("button", { name: "액션" }));
  fireEvent.click(screen.getByRole("button", { name: "QA-keyboard 던지기" }));
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "QA Engineer · QA Engineer" })); });
  expect(screen.getByRole("button", { name: "QA Engineer 선택" }).querySelector(".world-character-status")?.textContent).toBe("걱정");
  await act(async () => { await vi.advanceTimersByTimeAsync(1360); });
  expect(screen.getByRole("application").querySelectorAll(".world-break-effect .break-half")).toHaveLength(2);
});

it("removes the flight and prevents an impact when the server rejects the throw", async () => {
  vi.useFakeTimers();
  const pending = deferred<GameActionResponse>();
  const current = throwSnapshot("team_leader_person", "팀장님");
  vi.mocked(api.startSession).mockResolvedValue(current);
  vi.mocked(api.submitGameAction).mockReturnValue(pending.promise);
  await act(async () => { render(<App />); });
  fireEvent.click(screen.getByRole("button", { name: /VISUAL OFFICE/ }));
  fireEvent.click(screen.getByRole("button", { name: "액션" }));
  fireEvent.click(screen.getByRole("button", { name: "팀장님 던지기" }));
  fireEvent.click(screen.getByRole("button", { name: "QA Engineer · QA Engineer" }));
  expect(screen.getByRole("application").querySelector(".thrown-person-spin")).not.toBeNull();
  await act(async () => { pending.resolve({ snapshot: current, action_id: current.available_game_actions[0].id,
    message: "사용할 수 없는 물건입니다.", blocked: true, alert: null }); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByRole("application").querySelector(".thrown-world-object, .world-blink-effect, .world-break-effect")).toBeNull();
  expect(screen.getByRole("button", { name: "QA Engineer 선택" }).querySelector(".world-character-status")?.textContent).toBe("초조");
});

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

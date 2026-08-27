import { useRef, useState } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { snapshot, response, deferred } from "../test/fixtures";
import type { ActionResponse, GameSnapshot } from "../types";
import { useGameDialogue } from "./useGameDialogue";
import { GameDialoguePanel } from "./GameDialoguePanel";

function Harness({ send, initial = snapshot() }: { send: (text: string, target: string) => Promise<ActionResponse>; initial?: GameSnapshot }) {
  const [current, setCurrent] = useState(initial);
  const viewport = useRef<HTMLDivElement>(null);
  const controller = useGameDialogue({ snapshot: current, busy: false, isBusy: () => false,
    sendRequest: async (text, target) => { const value = await send(text, target); setCurrent(value.snapshot); return value; } });
  return <div ref={viewport}>
    {current.npcs.map(npc => <button key={npc.id} onClick={() => { controller.setNearby(npc.id); controller.open(npc.id); }}>{npc.name} 열기</button>)}
    <GameDialoguePanel controller={controller} snapshot={current} requestBusy={false} viewportRef={viewport} onClose={controller.close} />
  </div>;
}

const openQa = () => fireEvent.click(screen.getByRole("button", { name: "QA Engineer 열기" }));
const input = () => screen.getByRole("textbox", { name: "대화 내용" }) as HTMLInputElement;

describe("Unity dialog interactions", () => {
  it("focuses the input, locks other history tabs, and restores the current target", () => {
    render(<Harness send={vi.fn()} />); openQa();
    expect(document.activeElement).toBe(input());
    fireEvent.click(screen.getByRole("tab", { name: "Backend" }));
    expect(input().disabled).toBe(true);
    expect(screen.getByRole("dialog").textContent).toContain("현재 대화 상대: QA Engineer");
    fireEvent.click(screen.getByRole("tab", { name: "QA" }));
    expect(input().disabled).toBe(false);
    expect(document.activeElement).toBe(input());
  });

  it("does not submit IME confirmation or implicit form submission; the next Enter submits once", async () => {
    const pending = deferred<ActionResponse>();
    const send = vi.fn(() => pending.promise);
    render(<Harness send={send} />); openQa();
    fireEvent.change(input(), { target: { value: "한글 질문" } });
    fireEvent.compositionStart(input());
    fireEvent.keyDown(input(), { code: "Enter", key: "Enter", keyCode: 229, isComposing: true });
    fireEvent.compositionEnd(input());
    fireEvent.submit(input().closest("form")!);
    expect(send).not.toHaveBeenCalled();
    fireEvent.keyUp(input(), { code: "Enter", key: "Enter" });
    fireEvent.keyDown(input(), { code: "Enter", key: "Enter", keyCode: 13 });
    fireEvent.submit(input().closest("form")!);
    expect(send).toHaveBeenCalledExactlyOnceWith("한글 질문", "qa_01");
    expect(input().disabled).toBe(true);
    expect(input().value).toBe("");
    await act(async () => { pending.resolve(response()); });
    await waitFor(() => expect(input().disabled).toBe(false));
  });

  it("keeps a response in the original NPC history after closing and reopening another NPC", async () => {
    const pending = deferred<ActionResponse>();
    render(<Harness send={() => pending.promise} />); openQa();
    fireEvent.change(input(), { target: { value: "기다리는 질문" } });
    fireEvent.click(screen.getByRole("button", { name: "전송" }));
    fireEvent.click(screen.getByRole("button", { name: "닫기" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Backend Developer 열기" }));
    expect(input().disabled).toBe(true);
    await act(async () => { pending.resolve(response({ message: "QA의 늦은 응답" })); });
    expect(screen.getByRole("log").textContent).not.toContain("QA의 늦은 응답");
    fireEvent.click(screen.getByRole("tab", { name: "QA" }));
    expect(screen.getByRole("log").textContent).toContain("QA의 늦은 응답");
    expect(input().disabled).toBe(true);
  });

  it("clears an unfinished IME confirmation when focus leaves the input", async () => {
    const send = vi.fn(async () => response());
    render(<Harness send={send} />); openQa();
    fireEvent.change(input(), { target: { value: "한글 확인" } });
    fireEvent.compositionStart(input());
    fireEvent.keyDown(input(), { code: "Enter", key: "Enter", keyCode: 229, isComposing: true });
    fireEvent.blur(input());
    fireEvent.focus(input());
    fireEvent.keyDown(input(), { code: "Enter", key: "Enter", keyCode: 13 });
    await waitFor(() => expect(send).toHaveBeenCalledExactlyOnceWith("한글 확인", "qa_01"));
  });

  it("uses a discovered evidence button and renders untrusted text without HTML", async () => {
    const current = snapshot(); current.evidences[0].discovered = true;
    const send = vi.fn(async () => response({ message: '<img src=x onerror="alert(1)">' }));
    render(<Harness send={send} initial={current} />); openQa();
    expect(screen.queryByRole("button", { name: /증거 제시하기 · API schema/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "증거 제시하기 · QA warning message" }));
    await waitFor(() => expect(send).toHaveBeenCalledExactlyOnceWith("QA warning message 증거를 QA Engineer에게 제시해줘.", "qa_01"));
    await waitFor(() => expect(screen.getByRole("log").textContent).toContain("<img"));
    expect(screen.getByRole("log").querySelector("img")).toBeNull();
  });

  it("shows failure and empty-input feedback without losing prior history", async () => {
    const send = vi.fn().mockRejectedValue(new Error("연결 실패"));
    render(<Harness send={send} />); openQa();
    fireEvent.click(screen.getByRole("button", { name: "전송" }));
    expect(send).not.toHaveBeenCalled();
    expect(screen.getByRole("status").textContent).toContain("입력하세요");
    fireEvent.change(input(), { target: { value: "실패할 질문" } });
    fireEvent.click(screen.getByRole("button", { name: "전송" }));
    await waitFor(() => expect(screen.getByRole("log").textContent).toContain("[오류] 연결 실패"));
    expect(input().disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "닫기" })); openQa();
    expect(screen.getByRole("log").textContent).toContain("Player: 실패할 질문");
  });
});

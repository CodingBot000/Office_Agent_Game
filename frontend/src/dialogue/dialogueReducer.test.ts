import { describe, expect, it } from "vitest";
import { snapshot, response } from "../test/fixtures";
import { createDialogueState, dialogueReducer, canSendDialogue } from "./dialogueReducer";
import type { DialogueRequest } from "./dialogueTypes";

const request: DialogueRequest = { id: 1, sessionId: "session-1", targetId: "qa_01", targetName: "QA Engineer", text: "질문", displayText: "질문" };
function opened() {
  let state = createDialogueState(snapshot());
  state = dialogueReducer(state, { type: "nearby", npcId: "qa_01" });
  return dialogueReducer(state, { type: "open", npcId: "qa_01", npcName: "QA Engineer" });
}

describe("Unity dialogue state", () => {
  it("keeps the active target when viewing another NPC's read-only history", () => {
    const state = dialogueReducer(opened(), { type: "view", npcId: "backend_01" });
    expect(state.activeNpcId).toBe("qa_01");
    expect(state.viewedNpcId).toBe("backend_01");
    expect(canSendDialogue(state, snapshot(), false)).toBe(false);
  });

  it("stores a late response in the original target after closing and opening another NPC", () => {
    let state = dialogueReducer(opened(), { type: "begin", request });
    state = dialogueReducer(state, { type: "close" });
    state = dialogueReducer(state, { type: "resolved", request, response: response() });
    expect(state.isOpen).toBe(false);
    expect(state.historiesByNpc.qa_01.at(-1)?.text).toBe("NPC response");
    state = dialogueReducer(state, { type: "nearby", npcId: "backend_01" });
    state = dialogueReducer(state, { type: "open", npcId: "backend_01", npcName: "Backend Developer" });
    expect(state.historiesByNpc.backend_01.some(message => message.text === "NPC response")).toBe(false);
  });

  it("rejects duplicate and prior-session completions", () => {
    let state = dialogueReducer(opened(), { type: "begin", request });
    state = dialogueReducer(state, { type: "resolved", request, response: response() });
    expect(dialogueReducer(state, { type: "resolved", request, response: response() })).toBe(state);
    state = dialogueReducer(state, { type: "snapshot", snapshot: snapshot({ session_id: "new-session" }) });
    expect(dialogueReducer(state, { type: "resolved", request, response: response() })).toBe(state);
    expect(state.historiesByNpc).toEqual({});
  });

  it("deduplicates snapshot evidence and preserves it on first opening its source NPC", () => {
    let state = opened();
    const next = snapshot({ revision: 2 });
    next.evidences[1].discovered = true;
    state = dialogueReducer(state, { type: "snapshot", snapshot: next });
    state = dialogueReducer(state, { type: "snapshot", snapshot: next });
    state = dialogueReducer(state, { type: "nearby", npcId: "backend_01" });
    state = dialogueReducer(state, { type: "open", npcId: "backend_01", npcName: "Backend Developer" });
    expect(state.historiesByNpc.backend_01.filter(message => message.kind === "evidence")).toHaveLength(1);
    expect(createDialogueState(next).historiesByNpc).toEqual({});
  });

  it("suppresses an evidence-request response only if its new evidence notice is shown", () => {
    let state = dialogueReducer(opened(), { type: "begin", request });
    const next = snapshot({ revision: 2 });
    next.evidences[0].discovered = true;
    // The shared snapshot consumer may run before the request callback.
    state = dialogueReducer(state, { type: "snapshot", snapshot: next });
    state = dialogueReducer(state, { type: "resolved", request, response: response({ snapshot: next, classified_action: "request_evidence", evidence_id: "qa_warning_message" }) });
    expect(state.historiesByNpc.qa_01.filter(message => message.kind === "evidence")).toHaveLength(1);
    expect(state.historiesByNpc.qa_01.some(message => message.kind === "npc")).toBe(false);
    state = dialogueReducer(opened(), { type: "begin", request });
    state = dialogueReducer(state, { type: "resolved", request, response: response({ classified_action: "request_evidence", message: "제공할 수 없는 증거입니다" }) });
    expect(state.historiesByNpc.qa_01.at(-1)?.text).toBe("제공할 수 없는 증거입니다");
  });

  it("closes on lost proximity, retains histories and does not silently change active targets", () => {
    let state = dialogueReducer(opened(), { type: "nearby", npcId: "backend_01" });
    expect(state.activeNpcId).toBe("qa_01");
    expect(canSendDialogue(state, snapshot(), false)).toBe(false);
    state = dialogueReducer(state, { type: "nearby", npcId: null });
    expect(state.isOpen).toBe(false);
    expect(state.historiesByNpc.qa_01.length).toBeGreaterThan(0);
  });
});

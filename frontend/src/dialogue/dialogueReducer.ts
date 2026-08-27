import type { GameSnapshot } from "../types";
import type { DialogueAction, DialogueMessage, DialogueState } from "./dialogueTypes";

export function snapshotRevision(snapshot: GameSnapshot): number {
  return snapshot.revision ?? snapshot.turn;
}

export function createDialogueState(snapshot: GameSnapshot | null = null): DialogueState {
  return {
    sessionId: snapshot?.session_id ?? null, revision: snapshot ? snapshotRevision(snapshot) : -1,
    isOpen: false, nearbyNpcId: null, activeNpcId: null, viewedNpcId: null, draft: "",
    pendingRequest: null, historiesByNpc: {}, startedNpcIds: [], nextMessageId: 1, status: "",
    renderedEvidenceNotificationIds: snapshot?.evidences.filter(item => item.discovered).map(item => item.id) ?? [],
  };
}

function append(state: DialogueState, npcId: string, message: Omit<DialogueMessage, "id">): DialogueState {
  return {
    ...state,
    nextMessageId: state.nextMessageId + 1,
    historiesByNpc: {
      ...state.historiesByNpc,
      [npcId]: [...(state.historiesByNpc[npcId] ?? []), { ...message, id: state.nextMessageId }],
    },
  };
}

function ingestSnapshot(state: DialogueState, snapshot: GameSnapshot): DialogueState {
  if (state.sessionId !== snapshot.session_id) return createDialogueState(snapshot);
  const revision = snapshotRevision(snapshot);
  if (revision < state.revision) return state;
  let next = { ...state, revision };
  const seen = new Set(state.renderedEvidenceNotificationIds);
  for (const evidence of snapshot.evidences) {
    if (!evidence.discovered || seen.has(evidence.id)) continue;
    seen.add(evidence.id);
    const targetId = evidence.source_npc_id ?? state.pendingRequest?.targetId ?? state.activeNpcId;
    if (!targetId || !snapshot.npcs.some(npc => npc.id === targetId)) continue;
    next = append(next, targetId, {
      kind: "evidence", text: evidence.content, evidenceId: evidence.id,
      evidenceTitle: evidence.title, snapshotRevision: revision,
    });
  }
  if (next.activeNpcId && !snapshot.npcs.some(npc => npc.id === next.activeNpcId)) {
    next = { ...next, isOpen: false, nearbyNpcId: null };
  }
  return { ...next, renderedEvidenceNotificationIds: [...seen] };
}

export function canSendDialogue(state: DialogueState, snapshot: GameSnapshot | null, busy: boolean): boolean {
  return Boolean(snapshot && state.sessionId === snapshot.session_id && !snapshot.completed && state.isOpen
    && !busy && !state.pendingRequest && state.activeNpcId
    && snapshot.npcs.some(npc => npc.id === state.activeNpcId)
    && state.nearbyNpcId === state.activeNpcId && state.viewedNpcId === state.activeNpcId);
}

export function dialogueReducer(state: DialogueState, action: DialogueAction): DialogueState {
  switch (action.type) {
    case "snapshot": return ingestSnapshot(state, action.snapshot);
    case "nearby":
      if (action.npcId === state.nearbyNpcId) return state;
      return { ...state, nearbyNpcId: action.npcId, isOpen: action.npcId ? state.isOpen : false };
    case "open": {
      if (!state.sessionId || state.nearbyNpcId !== action.npcId) return state;
      let next = state;
      if (!state.startedNpcIds.includes(action.npcId)) {
        next = append(next, action.npcId, { kind: "notice", text: `-- ${action.npcName} --\n입력한 내용은 서버로 전송됩니다.\n` });
        next = { ...next, startedNpcIds: [...next.startedNpcIds, action.npcId] };
      }
      return { ...next, isOpen: true, activeNpcId: action.npcId, viewedNpcId: action.npcId, draft: "", status: "" };
    }
    case "close": return state.isOpen ? { ...state, isOpen: false } : state;
    case "view": return { ...state, viewedNpcId: action.npcId, status: "" };
    case "draft": return { ...state, draft: action.text };
    case "status": return { ...state, status: action.text };
    case "begin": {
      if (state.pendingRequest || action.request.sessionId !== state.sessionId
        || action.request.targetId !== state.activeNpcId || state.viewedNpcId !== state.activeNpcId
        || state.nearbyNpcId !== state.activeNpcId || !state.isOpen) return state;
      return {
        ...append(state, action.request.targetId, { kind: "player", speaker: "Player", text: action.request.displayText }),
        pendingRequest: action.request, draft: "", status: "",
      };
    }
    case "resolved": {
      const { request, response } = action;
      if (state.sessionId !== request.sessionId || state.pendingRequest?.id !== request.id
        || response.snapshot.session_id !== state.sessionId) return state;
      let next = ingestSnapshot(state, response.snapshot);
      const evidenceNoticeShown = response.classified_action === "request_evidence"
        && Object.values(next.historiesByNpc).some(messages => messages.some(message => message.kind === "evidence"
          && message.snapshotRevision === snapshotRevision(response.snapshot)
          && (!response.evidence_id || message.evidenceId === response.evidence_id)));
      if (!evidenceNoticeShown) {
        next = append(next, request.targetId, { kind: "npc", speaker: request.targetName, text: response.message || "(응답 없음)" });
      }
      if (response.blocked && response.alert) next = append(next, request.targetId, { kind: "blocked", text: response.alert });
      return { ...next, pendingRequest: null,
        status: response.blocked ? "Backend가 대화를 차단했습니다." : evidenceNoticeShown ? "증거 확보 완료" : `${request.targetName} 응답 수신 완료` };
    }
    case "failed":
      if (state.sessionId !== action.request.sessionId || state.pendingRequest?.id !== action.request.id) return state;
      return { ...append(state, action.request.targetId, { kind: "error", text: action.error }),
        pendingRequest: null, status: action.error };
  }
}

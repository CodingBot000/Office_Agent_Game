import { useCallback, useLayoutEffect, useReducer, useRef } from "react";
import type { ActionResponse, GameSnapshot } from "../types";
import { canSendDialogue, createDialogueState, dialogueReducer } from "./dialogueReducer";
import type { DialogueRequest } from "./dialogueTypes";

type Options = {
  snapshot: GameSnapshot | null;
  busy: boolean;
  isBusy: () => boolean;
  sendRequest: (text: string, targetId: string) => Promise<ActionResponse>;
};

export function useGameDialogue(options: Options) {
  const [state, dispatch] = useReducer(dialogueReducer, options.snapshot, createDialogueState);
  const latest = useRef({ state, options });
  latest.current = { state, options };
  const requestId = useRef(0);
  const pending = useRef<DialogueRequest | null>(null);

  useLayoutEffect(() => {
    if (options.snapshot) dispatch({ type: "snapshot", snapshot: options.snapshot });
  }, [options.snapshot]);

  const open = useCallback((npcId: string) => {
    const npc = latest.current.options.snapshot?.npcs.find(item => item.id === npcId);
    if (npc) dispatch({ type: "open", npcId, npcName: npc.name });
  }, []);
  const close = useCallback(() => dispatch({ type: "close" }), []);
  const setNearby = useCallback((npcId: string | null) => dispatch({ type: "nearby", npcId }), []);
  const setDraft = useCallback((text: string) => dispatch({ type: "draft", text }), []);
  const view = useCallback((npcId: string) => {
    if (latest.current.options.snapshot?.npcs.some(npc => npc.id === npcId)) dispatch({ type: "view", npcId });
  }, []);

  const send = useCallback(async (text: string, displayText: string) => {
    const { state: current, options: config } = latest.current;
    if (pending.current || !canSendDialogue(current, config.snapshot, config.isBusy())) return;
    const trimmed = text.trim();
    if (!trimmed || [...trimmed].length > 500) {
      dispatch({ type: "status", text: trimmed ? "대화 내용은 500자 이내로 입력하세요." : "대화 내용을 입력하세요." });
      return;
    }
    const npc = config.snapshot!.npcs.find(item => item.id === current.activeNpcId)!;
    const request: DialogueRequest = {
      id: ++requestId.current, sessionId: config.snapshot!.session_id, targetId: npc.id,
      targetName: npc.name, text: trimmed, displayText: displayText.trim(),
    };
    pending.current = request;
    dispatch({ type: "begin", request });
    try {
      const response = await config.sendRequest(request.text, request.targetId);
      dispatch({ type: "resolved", request, response });
    } catch (reason) {
      dispatch({ type: "failed", request, error: reason instanceof Error ? reason.message : "대화 요청에 실패했습니다." });
    } finally {
      if (pending.current === request) pending.current = null;
    }
  }, []);

  const submit = useCallback(() => {
    const text = latest.current.state.draft;
    return send(text, text);
  }, [send]);
  const presentEvidence = useCallback((evidenceId: string) => {
    const { state: current, options: config } = latest.current;
    const evidence = config.snapshot?.evidences.find(item => item.id === evidenceId && item.discovered);
    const npc = config.snapshot?.npcs.find(item => item.id === current.activeNpcId);
    if (!evidence || !npc) return Promise.resolve();
    return send(`${evidence.title} 증거를 ${npc.name}에게 제시해줘.`, `${evidence.title} 증거를 제시했습니다.`);
  }, [send]);

  return { state, open, close, setNearby, view, setDraft, submit, presentEvidence,
    canSend: canSendDialogue(state, options.snapshot, options.busy) };
}

export type GameDialogueController = ReturnType<typeof useGameDialogue>;

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, RefObject } from "react";
import type { GameSnapshot } from "../types";
import type { GameDialogueController } from "./useGameDialogue";
import type { DialogueMessage } from "./dialogueTypes";
import { DialogueHistory } from "./DialogueHistory";
import { DialogueEvidenceActions } from "./DialogueEvidenceActions";
import { ImeSubmitGuard, isEnter } from "../input/keyboardPolicy";
import "./game-dialogue.css";

const EMPTY_MESSAGES: DialogueMessage[] = [];
const TAB_LABELS: Record<string, string> = { backend_01: "Backend", frontend_01: "Frontend", qa_01: "QA", pm_01: "PM" };

type Props = {
  controller: GameDialogueController;
  snapshot: GameSnapshot;
  requestBusy: boolean;
  viewportRef: RefObject<HTMLDivElement | null>;
  onClose: () => void;
};

export function GameDialoguePanel({ controller, snapshot, requestBusy, viewportRef, onClose }: Props) {
  const { state, canSend } = controller;
  const inputRef = useRef<HTMLInputElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const ime = useRef(new ImeSubmitGuard());
  const [dots, setDots] = useState(1);
  const [layout, setLayout] = useState({ scale: 1, compact: false });
  useLayoutEffect(() => {
    if (!state.isOpen) return;
    const viewport = viewportRef.current;
    if (!viewport) return;
    const resize = () => {
      const { width, height } = viewport.getBoundingClientRect();
      if (width > 0 && height > 0) setLayout({ scale: Math.sqrt(width / 1280 * height / 720), compact: width < 640 || height < 360 });
    };
    resize();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    observer?.observe(viewport);
    return () => observer?.disconnect();
  }, [state.isOpen, viewportRef]);

  useLayoutEffect(() => {
    if (state.isOpen && canSend) inputRef.current?.focus({ preventScroll: true });
    else {
      if (document.activeElement === inputRef.current) inputRef.current?.blur();
      ime.current.reset();
    }
  }, [state.isOpen, state.activeNpcId, state.viewedNpcId, canSend]);

  useEffect(() => {
    if (!state.isOpen || !state.pendingRequest) return;
    setDots(1);
    const timer = window.setInterval(() => setDots(value => value % 5 + 1), 350);
    return () => window.clearInterval(timer);
  }, [state.isOpen, state.pendingRequest?.id]);

  if (!state.isOpen || !state.viewedNpcId) return null;
  const viewedName = snapshot.npcs.find(npc => npc.id === state.viewedNpcId)?.name ?? state.viewedNpcId;
  const activeName = snapshot.npcs.find(npc => npc.id === state.activeNpcId)?.name ?? state.activeNpcId;
  const isActiveTab = state.viewedNpcId === state.activeNpcId;
  const status = state.pendingRequest ? `${state.pendingRequest.targetName} 응답 중`
    : !isActiveTab ? `${viewedName} 기록 보기 · 현재 대화 상대: ${activeName}`
    : snapshot.completed ? "이미 종료된 사건입니다."
    : state.nearbyNpcId !== state.activeNpcId ? "현재 대화 상대와 가까이 있지 않아 입력할 수 없습니다."
    : requestBusy ? "Backend 요청이 처리 중입니다."
    : state.status || "대화 입력 가능";
  const submit = () => { if (canSend && ime.current.canSubmit()) void controller.submit(); };

  return (
    <div className="game-dialogue-layer" style={{ "--dialogue-scale": layout.scale } as CSSProperties}>
      <section className="game-dialogue-panel" data-compact={layout.compact} role="dialog" aria-modal="false" aria-labelledby="game-dialogue-title">
        <h2 id="game-dialogue-title">{isActiveTab ? `${viewedName}와 대화하기` : `${viewedName} 대화 기록`}</h2>
        <button className="game-dialogue-close" type="button" onClick={onClose}>닫기</button>
        <div className="game-dialogue-tabs" role="tablist" aria-label="NPC 대화 기록">
          {snapshot.npcs.map((npc, index) => (
            <button key={npc.id} type="button" role="tab" id={`game-dialogue-tab-${npc.id}`}
              aria-selected={npc.id === state.viewedNpcId} aria-controls="game-dialogue-record"
              tabIndex={npc.id === state.viewedNpcId ? 0 : -1}
              data-current={npc.id === state.activeNpcId}
              ref={element => { tabRefs.current[index] = element; }}
              onClick={() => controller.view(npc.id)}
              onKeyDown={event => {
                const next = event.key === "ArrowRight" ? (index + 1) % snapshot.npcs.length
                  : event.key === "ArrowLeft" ? (index + snapshot.npcs.length - 1) % snapshot.npcs.length
                  : event.key === "Home" ? 0 : event.key === "End" ? snapshot.npcs.length - 1 : null;
                if (next === null) return;
                event.preventDefault(); controller.view(snapshot.npcs[next].id); tabRefs.current[next]?.focus();
              }}>
              {TAB_LABELS[npc.id] ?? npc.name}
            </button>
          ))}
        </div>
        <div id="game-dialogue-record" role="tabpanel" aria-labelledby={`game-dialogue-tab-${state.viewedNpcId}`}>
          <DialogueHistory messages={state.historiesByNpc[state.viewedNpcId] ?? EMPTY_MESSAGES} npcId={state.viewedNpcId} npcName={viewedName} />
        </div>
        <div className="game-dialogue-status" role="status">
          {status}{state.pendingRequest && <span aria-hidden="true">{".".repeat(dots)}</span>}
        </div>
        {canSend && <DialogueEvidenceActions evidences={snapshot.evidences} onPresent={id => void controller.presentEvidence(id)} />}
        <form className="game-dialogue-form" onSubmit={event => { event.preventDefault(); submit(); }}>
          <input ref={inputRef} aria-label="대화 내용" aria-describedby="game-dialogue-title" placeholder="대화 내용을 입력하세요"
            value={state.draft} onChange={event => controller.setDraft(event.target.value)} disabled={!canSend} autoComplete="off"
            onBlur={() => ime.current.reset()}
            onCompositionStart={() => ime.current.start()} onCompositionEnd={() => ime.current.end()}
            onKeyDown={event => {
              if (!isEnter(event.nativeEvent)) return;
              if (ime.current.keyDown(event.nativeEvent)) { event.preventDefault(); submit(); }
              else if (!ime.current.composing && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) event.preventDefault();
            }}
            onKeyUp={event => ime.current.keyUp(event.nativeEvent)} />
          <button type="submit" disabled={!canSend}>전송</button>
        </form>
      </section>
    </div>
  );
}

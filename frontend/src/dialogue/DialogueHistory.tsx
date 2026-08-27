import { useLayoutEffect, useRef } from "react";
import type { DialogueMessage } from "./dialogueTypes";

export function DialogueHistory({ messages, npcId, npcName }: { messages: DialogueMessage[]; npcId: string; npcName: string }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const scrollToLatest = () => { element.scrollTop = element.scrollHeight; };
    scrollToLatest();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(scrollToLatest) : null;
    observer?.observe(element);
    if (contentRef.current) observer?.observe(contentRef.current);
    return () => observer?.disconnect();
  }, [messages, npcId]);
  return (
    <div className="game-dialogue-history" role="log" aria-label={`${npcName} 대화 기록`} aria-live="polite" tabIndex={0} ref={scrollRef}>
      <div ref={contentRef}>
      {messages.length === 0 ? <p>{`-- ${npcName} --\n아직 대화 기록이 없습니다.\n`}</p> : messages.map(message => (
        <div key={message.id} className={`game-dialogue-message ${message.kind}`}>
          {message.kind === "evidence" ? <>
            <strong>증거를 확보했습니다.</strong>
            <div>{message.evidenceTitle}</div>
            <div>{message.text}</div>
          </> : <>
            {message.speaker && <span>{message.speaker}: </span>}
            {message.kind === "error" && "[오류] "}
            {message.kind === "blocked" && "[차단됨] "}
            {message.text}
          </>}
        </div>
      ))}
      </div>
    </div>
  );
}

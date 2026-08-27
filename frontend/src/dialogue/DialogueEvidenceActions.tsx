import type { Evidence } from "../types";

export function DialogueEvidenceActions({ evidences, onPresent }: { evidences: Evidence[]; onPresent: (id: string) => void }) {
  const discovered = evidences.filter(evidence => evidence.discovered);
  if (!discovered.length) return null;
  return (
    <div className="game-dialogue-evidence" aria-label="증거 제시" data-single={discovered.length === 1}>
      {discovered.map(evidence => (
        <button key={evidence.id} type="button" onClick={() => onPresent(evidence.id)} title={evidence.title}>
          증거 제시하기 · {evidence.title}
        </button>
      ))}
    </div>
  );
}

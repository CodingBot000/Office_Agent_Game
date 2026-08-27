import type { ActionResponse, GameSnapshot } from "../types";

export type DialogueMessage = {
  id: number;
  kind: "player" | "npc" | "notice" | "error" | "blocked" | "evidence";
  text: string;
  speaker?: string;
  evidenceId?: string;
  evidenceTitle?: string;
  snapshotRevision?: number;
};

export type DialogueRequest = {
  id: number;
  sessionId: string;
  targetId: string;
  targetName: string;
  text: string;
  displayText: string;
};

export type DialogueState = {
  sessionId: string | null;
  revision: number;
  isOpen: boolean;
  nearbyNpcId: string | null;
  activeNpcId: string | null;
  viewedNpcId: string | null;
  draft: string;
  pendingRequest: DialogueRequest | null;
  historiesByNpc: Record<string, DialogueMessage[]>;
  startedNpcIds: string[];
  renderedEvidenceNotificationIds: string[];
  nextMessageId: number;
  status: string;
};

export type DialogueAction =
  | { type: "snapshot"; snapshot: GameSnapshot }
  | { type: "nearby"; npcId: string | null }
  | { type: "open"; npcId: string; npcName: string }
  | { type: "close" }
  | { type: "view"; npcId: string }
  | { type: "draft"; text: string }
  | { type: "status"; text: string }
  | { type: "begin"; request: DialogueRequest }
  | { type: "resolved"; request: DialogueRequest; response: ActionResponse }
  | { type: "failed"; request: DialogueRequest; error: string };

import type { ActionResponse, GameActionResponse, GameSnapshot, IntentClassification } from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function startSession(): Promise<GameSnapshot> {
  return request<GameSnapshot>("/api/v1/sessions", { method: "POST" });
}

export function submitAction(sessionId: string, text: string, intentHint?: IntentClassification, targetHint?: string | null): Promise<ActionResponse> {
  return request<ActionResponse>(`/api/v1/sessions/${sessionId}/actions`, {
    method: "POST",
    body: JSON.stringify({
      text,
      ...(intentHint ? { intent_hint: intentHint } : {}),
      ...(targetHint ? { target_hint: targetHint } : {}),
    }),
  });
}

export function submitGameAction(sessionId: string, actionId: string): Promise<GameActionResponse> {
  return request<GameActionResponse>(`/api/v1/sessions/${sessionId}/game-actions`, {
    method: "POST",
    body: JSON.stringify({ action_id: actionId }),
  });
}

export function resetSession(sessionId: string): Promise<GameSnapshot> {
  return request<GameSnapshot>(`/api/v1/sessions/${sessionId}/reset`, { method: "POST" });
}

export function submitReport(
  sessionId: string,
  primaryCause: string,
  contributingFactors: string[],
): Promise<GameSnapshot> {
  return request<GameSnapshot>(`/api/v1/sessions/${sessionId}/report`, {
    method: "POST",
    body: JSON.stringify({
      primary_cause: primaryCause,
      contributing_factors: contributingFactors,
    }),
  });
}

import type { ActionResponse, GameSnapshot } from "../types";

export function snapshot(overrides: Partial<GameSnapshot> = {}): GameSnapshot {
  return {
    session_id: "session-1", revision: 1, turn: 0, current_location: "meeting_room",
    incident_status: "ACTIVE", ai_provider: "deterministic-mock", ai_model: "test",
    objective: [], relationships: [], world_objects: [], available_game_actions: [],
    player_inventory: { held_object_ids: [], max_held_objects: 1, unlimited: true },
    game_action_traces: [], social_events: [], dialogue_refused_npc_ids: [], events: [],
    agent_traces: [], fallback_notices: [], available_actions: [], completed: false, result: null,
    npcs: [
      ["backend_01", "Backend Developer"], ["frontend_01", "Frontend Developer"],
      ["qa_01", "QA Engineer"], ["pm_01", "PM / Planner"],
    ].map(([id, name]) => ({
      id, name, role: name, personality: { assertiveness: 50, cooperativeness: 50, risk_aversion: 50, blame_sensitivity: 50 },
      dynamic_state: { emotion: "calm", stress: 20, trust_toward_player: 0, cooperation: 60 },
      physical_state: "normal", is_fallen: false, known_fact_ids: [], known_facts: [],
      beliefs: [], relationships: [], recent_memories: [], important_memories: [],
    })),
    evidences: [
      { id: "qa_warning_message", title: "QA warning message", summary: "Warning", content: "QA evidence content", source_npc_id: "qa_01", discovered: false },
      { id: "api_schema_diff", title: "API schema diff", summary: "Diff", content: "API evidence content", source_npc_id: "backend_01", discovered: false },
    ],
    ...overrides,
  };
}

export function response(overrides: Partial<ActionResponse> = {}): ActionResponse {
  return {
    snapshot: snapshot({ revision: 2, turn: 1 }), classified_action: "ask", message: "NPC response",
    intent_provider: "deterministic-mock", intent_confidence: 1, intent_fallback_used: false,
    question_type: "general_status", reference_scope: "none", evidence_id: null,
    social_impact_provider: null, social_impact_fallback_used: false, blocked: false, alert: null,
    ...overrides,
  };
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

export interface Personality {
  assertiveness: number;
  cooperativeness: number;
  risk_aversion: number;
  blame_sensitivity: number;
}

export interface DynamicState {
  emotion: string;
  stress: number;
  trust_toward_player: number;
  cooperation: number;
}

export interface Belief {
  subject: string;
  belief: string;
  confidence: number;
}

export interface Memory {
  summary: string;
  importance: number;
  turn: number;
}

export interface Relationship {
  target_npc_id: string;
  trust: number;
  tension: number;
}

export interface NPCState {
  id: string;
  name: string;
  role: string;
  personality: Personality;
  dynamic_state: DynamicState;
  known_facts: string[];
  beliefs: Belief[];
  relationships: Relationship[];
  recent_memories: Memory[];
  important_memories: Memory[];
}

export interface Evidence {
  id: string;
  title: string;
  summary: string;
  content: string;
  source_npc_id: string | null;
  discovered: boolean;
}

export interface EventLogEntry {
  id: number;
  turn: number;
  actor: string;
  actor_id: string | null;
  message: string;
  event_type: string;
  created_at: string;
}

export interface GuardrailCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface AgentDecision {
  npc_id: string;
  emotion: string;
  stress_delta: number;
  trust_delta: number;
  cooperation_delta: number;
  belief_updates: Belief[];
  memory_candidate: Memory | null;
  action_type: string;
  action_target: string | null;
  dialogue: string;
}

export interface AgentTrace {
  id: number;
  turn: number;
  event: string;
  npc_id: string;
  provider: "cli" | "openai" | "deterministic-mock";
  context_summary: string;
  known_facts: string[];
  retrieved_rules: string[];
  decision: AgentDecision;
  guardrails: GuardrailCheck[];
  fallback_used: boolean;
}

export interface GameResult {
  incident_diagnosis: number;
  evidence_coverage: number;
  team_trust: number;
  recovery_efficiency: number;
  summary: string;
}

export interface GameSnapshot {
  session_id: string;
  turn: number;
  current_location: string;
  incident_status: string;
  ai_provider: "cli" | "openai" | "deterministic-mock";
  ai_model: string;
  objective: string[];
  npcs: NPCState[];
  evidences: Evidence[];
  events: EventLogEntry[];
  agent_traces: AgentTrace[];
  available_actions: string[];
  completed: boolean;
  result: GameResult | null;
}

export interface ActionResponse {
  snapshot: GameSnapshot;
  classified_action: string;
  message: string;
  intent_provider: "cli" | "openai" | "deterministic-mock" | "ui";
  intent_confidence: number;
  intent_fallback_used: boolean;
}

export interface IntentClassification {
  intent: string;
  target_npc_id?: string | null;
  evidence_id?: string | null;
  location?: "meeting_room" | "dev_area" | "qa_desk" | "pm_desk" | null;
  confidence?: number;
}

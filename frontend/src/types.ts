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

export interface RelationshipUpdate {
  target_npc_id: string;
  trust_delta: number;
  tension_delta: number;
}

export interface RelationshipState {
  source_id: string;
  target_id: string;
  trust: number;
  tension: number;
  respect: number;
  fear: number;
  grievance: number;
  repair_stage: "none" | "acknowledged" | "apologized" | "repaired" | "mediated";
  trust_ceiling: number | null;
  fear_floor: number;
  last_changed_turn: number;
}

export interface WorldObjectState {
  id: string;
  name: string;
  owner_id: string | null;
  location: "meeting_room" | "dev_area" | "qa_desk" | "pm_desk";
  evidence_id: string | null;
  portable: boolean;
  destructible: boolean;
  holder_id: string | null;
  condition: "normal" | "damaged" | "destroyed";
}

export type GameActionFamily = "pick_up_object" | "break_held_object" | "drop_held_object" | "inspect_object" | "throw_held_object";

export interface AvailableGameAction {
  id: string;
  family: GameActionFamily;
  label: string;
  object_id: string | null;
  target_id: string | null;
  location: string;
  enabled: boolean;
  disabled_reason: string | null;
}

export interface PlayerInventory {
  held_object_ids: string[];
  max_held_objects: number;
}

export interface GameActionGuardrail {
  name: string;
  passed: boolean;
  detail: string;
}

export interface GameActionTrace {
  id: number;
  turn: number;
  action_id: string;
  family: GameActionFamily | null;
  actor_id: string;
  location: string;
  object_id: string | null;
  owner_id: string | null;
  holder_before: string | null;
  holder_after: string | null;
  condition_before: string | null;
  condition_after: string | null;
  message: string;
  guardrails: GameActionGuardrail[];
  blocked: boolean;
}

export interface NPCState {
  id: string;
  name: string;
  role: string;
  personality: Personality;
  dynamic_state: DynamicState;
  known_fact_ids: string[];
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
  relationship_updates: RelationshipUpdate[];
  grounding_type: "fact" | "belief" | "acknowledgement";
  knowledge_refs: string[];
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
  known_fact_ids: string[];
  known_facts: string[];
  retrieved_rules: string[];
  decision: AgentDecision;
  requested_decision: AgentDecision | null;
  guardrails: GuardrailCheck[];
  fallback_used: boolean;
}

export interface SocialImpactClassification {
  action_family: string;
  direct_target_ids: string[];
  affected_target_ids: string[];
  object_id: string | null;
  severity: number;
  intentionality: "accidental" | "reckless" | "deliberate";
  observable: boolean;
  evidence_based: boolean;
  reason_codes: string[];
  confidence: number;
}

export interface RelationshipEffect {
  source_id: string;
  target_id: string;
  trust_delta: number;
  tension_delta: number;
  respect_delta: number;
  fear_delta: number;
  grievance_delta: number;
  reason_codes: string[];
}

export interface EmotionEffect {
  npc_id: string;
  emotion: string;
  stress_delta: number;
  cooperation_delta: number;
}

export interface PolicyModifier {
  code: string;
  multiplier: number;
}

export interface WorldEvent {
  event_type: string;
  target_id: string | null;
  detail: string;
}

export interface SocialPolicyOutcome {
  conduct_level: "permitted" | "inappropriate" | "misconduct" | "severe_misconduct";
  relationship_effects: RelationshipEffect[];
  emotion_effects: EmotionEffect[];
  mandatory_world_events: WorldEvent[];
  memory_effects: Array<{ npc_id: string; memory: Memory }>;
  applied_modifiers: PolicyModifier[];
}

export interface SocialEventTrace {
  id: number;
  turn: number;
  actor_id: string;
  provider: "cli" | "openai" | "deterministic-mock";
  player_input: string;
  classification: SocialImpactClassification;
  requested_classification: SocialImpactClassification | null;
  policy_outcome: SocialPolicyOutcome;
  guardrails: GuardrailCheck[];
  fallback_used: boolean;
}

export interface FallbackNotice {
  id: number;
  turn: number;
  stage: "intent_provider" | "intent_guardrail" | "decision_provider" | "decision_guardrail" | "social_impact_provider" | "social_impact_guardrail";
  provider: "cli" | "openai" | "deterministic-mock";
  reason: string;
  created_at: string;
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
  relationships: RelationshipState[];
  world_objects: WorldObjectState[];
  available_game_actions: AvailableGameAction[];
  player_inventory: PlayerInventory;
  game_action_traces: GameActionTrace[];
  social_events: SocialEventTrace[];
  dialogue_refused_npc_ids: string[];
  evidences: Evidence[];
  events: EventLogEntry[];
  agent_traces: AgentTrace[];
  fallback_notices: FallbackNotice[];
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
  social_impact_provider: "cli" | "openai" | "deterministic-mock" | null;
  social_impact_fallback_used: boolean;
  blocked: boolean;
  alert: string | null;
}

export interface GameActionResponse {
  snapshot: GameSnapshot;
  action_id: string;
  message: string;
  blocked: boolean;
  alert: string | null;
}

export interface IntentClassification {
  intent: string;
  interaction_kind?: "dialogue" | "game_action_attempt";
  game_action_family?: string | null;
  target_npc_id?: string | null;
  evidence_id?: string | null;
  location?: "meeting_room" | "dev_area" | "qa_desk" | "pm_desk" | null;
  confidence?: number;
}

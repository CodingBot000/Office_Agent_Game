from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, get_args
from uuid import uuid4

from app.config import Settings, get_settings
from app.game.seed import (
    CANONICAL_TRUTH,
    FACT_REGISTRY,
    INCIDENT_RULES,
    LEGACY_FACT_TEXT_TO_ID,
    build_initial_world_objects,
    build_relationship_graph,
    clone_evidence,
    clone_npcs,
    clone_relationships,
    clone_world_objects,
    relationship_key,
)
from app.models import (
    ActionResponse,
    ActionType,
    AgentDecision,
    AgentTrace,
    Belief,
    DynamicState,
    Evidence,
    EventLogEntry,
    FallbackNotice,
    GameResult,
    GameSnapshot,
    GuardrailCheck,
    IncidentReportRequest,
    IntentClassification,
    Memory,
    NPCState,
    Relationship,
    RelationshipState,
    RelationshipUpdate,
    SocialEventTrace,
    WorldObjectState,
)
from app.providers import AgentProvider, DecisionContext, IntentContext, IntentProvider, ProviderError, create_intent_provider, create_provider
from app.providers.deterministic import DeterministicDecisionProvider, DeterministicIntentProvider
from app.storage import SessionRepository, create_session_repository


logger = logging.getLogger(__name__)
CURRENT_SESSION_SCHEMA_VERSION = 4


AVAILABLE_ACTIONS = list(get_args(ActionType))

ALLOWED_AGENT_ACTION_TYPES = {"dialogue", "show_evidence", "belief_update"}


@dataclass
class GameSession:
    session_id: str
    turn: int = 0
    current_location: str = "meeting_room"
    incident_status: str = "ACTIVE"
    objective: list[str] = field(
        default_factory=lambda: [
            "장애의 직접 원인과 기여 요인을 파악하세요.",
            "주요 증거를 확보하세요.",
            "팀의 신뢰를 심각하게 훼손하지 마세요.",
            "최종 Incident Report를 제출하세요.",
        ]
    )
    npcs: dict[str, NPCState] = field(default_factory=clone_npcs)
    relationships: dict[str, RelationshipState] = field(default_factory=clone_relationships)
    world_objects: dict[str, WorldObjectState] = field(default_factory=clone_world_objects)
    social_events: list[SocialEventTrace] = field(default_factory=list)
    evidences: dict[str, Evidence] = field(default_factory=clone_evidence)
    events: list[EventLogEntry] = field(default_factory=list)
    agent_traces: list[AgentTrace] = field(default_factory=list)
    fallback_notices: list[FallbackNotice] = field(default_factory=list)
    discovered_evidence: set[str] = field(default_factory=set)
    canonical_truth: list[str] = field(default_factory=lambda: list(CANONICAL_TRUTH))
    completed: bool = False
    result: GameResult | None = None


class SessionNotFoundError(KeyError):
    pass


class InvalidIntentHintError(ValueError):
    pass


class GameEngine:
    """Authoritative game loop with a replaceable agent decision provider."""

    def __init__(
        self,
        provider: AgentProvider | None = None,
        intent_provider: IntentProvider | None = None,
        settings: Settings | None = None,
        session_repository: SessionRepository | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.session_repository = session_repository or create_session_repository(self.settings)
        self.provider = provider or create_provider(self.settings)
        self.intent_provider = intent_provider or create_intent_provider(self.settings)
        self.fallback_provider = DeterministicDecisionProvider()
        self.intent_fallback_provider = DeterministicIntentProvider()

    def create_session(self) -> GameSnapshot:
        session = GameSession(session_id=str(uuid4()))
        self._append_event(session, "System", "서비스 장애 사건이 시작되었습니다. 현재 상태: ACTIVE.", "system")
        self._save_session(session)
        return self.snapshot(session)

    def get_session(self, session_id: str) -> GameSession:
        payload = self.session_repository.load(session_id)
        if payload is None:
            raise SessionNotFoundError(session_id)
        migrated_payload, migrated = self._migrate_session_payload(payload)
        session = self._deserialize_session(migrated_payload)
        if migrated:
            self._save_session(session)
        return session

    def reset_session(self, session_id: str) -> GameSnapshot:
        self.session_repository.delete(session_id)
        return self.create_session()

    def submit_action(
        self,
        session_id: str,
        text: str,
        intent_hint: IntentClassification | None = None,
        target_hint: str | None = None,
    ) -> ActionResponse:
        session = self.get_session(session_id)
        if session.completed:
            return ActionResponse(
                snapshot=self.snapshot(session),
                classified_action="completed",
                message="이미 종료된 사건입니다. 새 세션을 시작하세요.",
                intent_provider=self.intent_provider.name,
                intent_confidence=1.0,
            )

        session.turn += 1
        # Natural-language input is logged for the conversation timeline.
        # Explicit UI hints already have a visible pending state, so persisting
        # the raw button command would duplicate the movement confirmation.
        if intent_hint is None:
            self._append_event(session, "Player", text.strip(), "input")
        if intent_hint is not None:
            intent = self._validate_intent_hint(session, intent_hint)
            intent_fallback = False
            intent_provider = "ui"
        else:
            if target_hint is not None and target_hint not in session.npcs:
                raise InvalidIntentHintError("Target hint does not match an NPC in the current session.")
            intent, intent_fallback = self._classify_intent(session, text, target_hint)
            intent_provider = self.intent_provider.name
        message = self._handle_action(session, intent, text)
        self._save_session(session)
        return ActionResponse(
            snapshot=self.snapshot(session),
            classified_action=intent.intent,
            message=message,
            intent_provider=intent_provider,
            intent_confidence=intent.confidence,
            intent_fallback_used=intent_fallback,
        )

    def submit_report(self, session_id: str, report: IncidentReportRequest) -> GameSnapshot:
        session = self.get_session(session_id)
        if session.completed:
            return self.snapshot(session)

        session.turn += 1
        self._append_event(session, "Player", "최종 Incident Report를 제출했습니다.", "report")
        session.result = self._score_report(session, report)
        session.completed = True
        session.incident_status = "RESOLVED"
        self._append_event(session, "System", "사건 분석이 종료되었습니다. 결과를 확인하세요.", "system")
        self._save_session(session)
        return self.snapshot(session)

    def snapshot(self, session: GameSession) -> GameSnapshot:
        visible_evidence = []
        for evidence_id, evidence in session.evidences.items():
            visible_evidence.append(
                evidence.model_copy(
                    update={
                        "discovered": evidence_id in session.discovered_evidence,
                        "content": evidence.content if evidence_id in session.discovered_evidence else "",
                    }
                )
            )
        return GameSnapshot(
            session_id=session.session_id,
            turn=session.turn,
            current_location=session.current_location,
            incident_status=session.incident_status,
            ai_provider=self.provider.name,
            ai_model=self.provider.model,
            objective=session.objective,
            npcs=list(session.npcs.values()),
            relationships=list(session.relationships.values()),
            world_objects=list(session.world_objects.values()),
            social_events=session.social_events[-20:],
            evidences=visible_evidence,
            events=session.events[-50:],
            agent_traces=session.agent_traces[-20:],
            fallback_notices=session.fallback_notices[-20:],
            available_actions=AVAILABLE_ACTIONS,
            completed=session.completed,
            result=session.result,
        )

    def _save_session(self, session: GameSession) -> None:
        self.session_repository.save(session.session_id, self._serialize_session(session))

    def _serialize_session(self, session: GameSession) -> dict[str, object]:
        return {
            "schema_version": CURRENT_SESSION_SCHEMA_VERSION,
            "session_id": session.session_id,
            "turn": session.turn,
            "current_location": session.current_location,
            "incident_status": session.incident_status,
            "objective": session.objective,
            "npcs": {npc_id: npc.model_dump(mode="json") for npc_id, npc in session.npcs.items()},
            "relationships": {
                relationship_id: relationship.model_dump(mode="json")
                for relationship_id, relationship in session.relationships.items()
            },
            "world_objects": {
                object_id: world_object.model_dump(mode="json")
                for object_id, world_object in session.world_objects.items()
            },
            "social_events": [event.model_dump(mode="json") for event in session.social_events],
            "evidences": {evidence_id: evidence.model_dump(mode="json") for evidence_id, evidence in session.evidences.items()},
            "events": [event.model_dump(mode="json") for event in session.events],
            "agent_traces": [trace.model_dump(mode="json") for trace in session.agent_traces],
            "fallback_notices": [notice.model_dump(mode="json") for notice in session.fallback_notices],
            "discovered_evidence": sorted(session.discovered_evidence),
            "canonical_truth": session.canonical_truth,
            "completed": session.completed,
            "result": session.result.model_dump(mode="json") if session.result else None,
        }

    def _migrate_session_payload(self, payload: dict[str, object]) -> tuple[dict[str, object], bool]:
        version = int(payload.get("schema_version", 1))
        if version > CURRENT_SESSION_SCHEMA_VERSION:
            raise ValueError(f"Unsupported future session schema version: {version}")
        if version == CURRENT_SESSION_SCHEMA_VERSION:
            return payload, False

        npc_payload = dict(payload.get("npcs", {}))
        for npc_id, raw_npc in npc_payload.items():
            if not isinstance(raw_npc, dict):
                continue
            known_fact_ids = [str(item) for item in raw_npc.get("known_fact_ids", [])]
            if not known_fact_ids:
                for legacy_fact in raw_npc.get("known_facts", []):
                    fact_id = LEGACY_FACT_TEXT_TO_ID.get(str(legacy_fact))
                    if fact_id:
                        known_fact_ids.append(fact_id)
                    else:
                        logger.warning(
                            "session_migration_unmapped_fact session_id=%s npc_id=%s fact=%s",
                            payload.get("session_id"),
                            npc_id,
                            str(legacy_fact)[:160],
                        )
            raw_npc["known_fact_ids"] = list(dict.fromkeys(known_fact_ids))
            raw_npc["known_facts"] = [
                FACT_REGISTRY[fact_id].statement
                for fact_id in raw_npc["known_fact_ids"]
                if fact_id in FACT_REGISTRY
            ]

        payload["npcs"] = npc_payload
        if version < 4:
            migrated_npcs = {
                str(npc_id): NPCState.model_validate(raw_npc)
                for npc_id, raw_npc in npc_payload.items()
                if isinstance(raw_npc, dict)
            }
            relationship_graph = build_relationship_graph(migrated_npcs)
            payload["relationships"] = {
                relationship_id: relationship.model_dump(mode="json")
                for relationship_id, relationship in relationship_graph.items()
            }
            payload["world_objects"] = {
                object_id: world_object.model_dump(mode="json")
                for object_id, world_object in build_initial_world_objects().items()
            }
            payload["social_events"] = []
        payload["schema_version"] = CURRENT_SESSION_SCHEMA_VERSION
        return payload, True

    def _deserialize_session(self, payload: dict[str, object]) -> GameSession:
        npc_payload = payload.get("npcs", {})
        evidence_payload = payload.get("evidences", {})
        relationship_payload = payload.get("relationships", {})
        world_object_payload = payload.get("world_objects", {})
        return GameSession(
            session_id=str(payload["session_id"]),
            turn=int(payload.get("turn", 0)),
            current_location=str(payload.get("current_location", "meeting_room")),
            incident_status=str(payload.get("incident_status", "ACTIVE")),
            objective=[str(item) for item in payload.get("objective", [])],
            npcs={str(npc_id): NPCState.model_validate(npc) for npc_id, npc in dict(npc_payload).items()},
            relationships={
                str(relationship_id): RelationshipState.model_validate(relationship)
                for relationship_id, relationship in dict(relationship_payload).items()
            },
            world_objects={
                str(object_id): WorldObjectState.model_validate(world_object)
                for object_id, world_object in dict(world_object_payload).items()
            },
            social_events=[SocialEventTrace.model_validate(event) for event in payload.get("social_events", [])],
            evidences={
                str(evidence_id): Evidence.model_validate(evidence)
                for evidence_id, evidence in dict(evidence_payload).items()
            },
            events=[EventLogEntry.model_validate(event) for event in payload.get("events", [])],
            agent_traces=[AgentTrace.model_validate(trace) for trace in payload.get("agent_traces", [])],
            fallback_notices=[FallbackNotice.model_validate(notice) for notice in payload.get("fallback_notices", [])],
            discovered_evidence={str(item) for item in payload.get("discovered_evidence", [])},
            canonical_truth=[str(item) for item in payload.get("canonical_truth", CANONICAL_TRUTH)],
            completed=bool(payload.get("completed", False)),
            result=GameResult.model_validate(payload["result"]) if payload.get("result") else None,
        )

    def _classify_intent(
        self,
        session: GameSession,
        text: str,
        target_hint: str | None = None,
    ) -> tuple[IntentClassification, bool]:
        context = IntentContext(
            player_input=text,
            current_location=session.current_location,
            target_hint=target_hint,
            available_npcs=tuple(f"{npc.id}: {npc.name} ({npc.role})" for npc in session.npcs.values()),
            available_npc_ids=tuple(session.npcs),
            available_evidence_ids=tuple(session.evidences),
            available_locations=("meeting_room", "dev_area", "qa_desk", "pm_desk"),
            available_actions=tuple(AVAILABLE_ACTIONS),
        )
        try:
            candidate = self.intent_provider.classify(context)
            provider_fallback = False
        except ProviderError as exc:
            self._record_fallback(
                session,
                stage="intent_provider",
                provider=self.intent_provider.name,
                reason=str(exc),
            )
            candidate = self.intent_fallback_provider.classify(context)
            provider_fallback = True
        validated, validation_fallback = self._validate_intent(session, context, candidate)
        return validated, provider_fallback or validation_fallback

    def _validate_intent_hint(self, session: GameSession, candidate: IntentClassification) -> IntentClassification:
        if candidate.intent != "move":
            raise InvalidIntentHintError("Only move intent hints are accepted from Office controls.")
        if candidate.location not in ("meeting_room", "dev_area", "qa_desk", "pm_desk"):
            raise InvalidIntentHintError("Office control requested an unknown location.")
        if candidate.target_npc_id is not None or candidate.evidence_id is not None:
            raise InvalidIntentHintError("Move intent hints cannot target NPCs or evidence.")
        return candidate.model_copy(update={"confidence": 1.0})

    def _validate_intent(
        self,
        session: GameSession,
        context: IntentContext,
        candidate: IntentClassification,
    ) -> tuple[IntentClassification, bool]:
        target_valid = candidate.target_npc_id is None or candidate.target_npc_id in session.npcs
        evidence_valid = candidate.evidence_id is None or candidate.evidence_id in session.evidences
        location_valid = candidate.location is None or candidate.location in context.available_locations
        if target_valid and evidence_valid and location_valid:
            return candidate, False

        self._record_fallback(
            session,
            stage="intent_guardrail",
            provider=self.intent_provider.name,
            reason="Intent target, evidence, or location was outside the current world state.",
        )
        return self.intent_fallback_provider.classify(context), True

    def _handle_action(self, session: GameSession, intent: IntentClassification, text: str) -> str:
        handlers = self._action_handlers(session, intent, text)
        if set(handlers) != set(AVAILABLE_ACTIONS):
            raise RuntimeError("Action handler registry does not match ActionType contract.")
        return handlers[intent.intent]()

    def _action_handlers(
        self,
        session: GameSession,
        intent: IntentClassification,
        text: str,
    ) -> dict[str, Callable[[], str]]:
        target_id = intent.target_npc_id
        return {
            "talk": lambda: self._talk_npc(session, target_id, text),
            "ask": lambda: self._ask_npc(session, target_id, text),
            "accuse": lambda: self._accuse_npc(session, target_id, text),
            "defend": lambda: self._defend_npc(session, target_id, text),
            "order": lambda: self._handle_order(session),
            "inspect": lambda: self._inspect_evidence(session, text, intent.evidence_id),
            "show_evidence": lambda: self._show_evidence(session, target_id, intent.evidence_id),
            "request_evidence": lambda: self._request_evidence(session, target_id, intent.evidence_id, text),
            "move": lambda: self._handle_move(session, intent.location),
            "summon_meeting": lambda: self._handle_summon_meeting(session),
            "report_conclusion": lambda: self._handle_report_prompt(session),
            "social_action": lambda: self._handle_social_action_placeholder(session),
        }

    def _handle_social_action_placeholder(self, session: GameSession) -> str:
        self._append_event(session, "System", "사회적 행동 정책을 적용할 수 없습니다.", "guardrail")
        return "사회적 행동 정책을 적용할 수 없습니다."

    def _handle_order(self, session: GameSession) -> str:
        self._append_event(session, "System", "배포 중단 및 롤백을 지시했습니다.", "command")
        session.incident_status = "MITIGATING"
        return "롤백 지시가 기록되었습니다."

    def _handle_move(self, session: GameSession, location: str | None) -> str:
        session.current_location = location or "dev_area"
        location_labels = {
            "meeting_room": "회의실",
            "dev_area": "개발 구역",
            "qa_desk": "QA Desk",
            "pm_desk": "PM Desk",
        }
        self._append_event(session, "System", f"{location_labels[session.current_location]}로 이동했습니다.", "movement")
        return "현재 위치가 변경되었습니다."

    def _handle_summon_meeting(self, session: GameSession) -> str:
        session.current_location = "meeting_room"
        self._append_event(session, "System", "팀원들을 회의실로 소집했습니다.", "command")
        return "회의실에 팀원들이 모였습니다."

    def _handle_report_prompt(self, session: GameSession) -> str:
        self._append_event(session, "System", "보고서 화면에서 최종 원인과 기여 요인을 제출하세요.", "prompt")
        return "최종 보고서를 입력하면 사건을 종료할 수 있습니다."

    def _inspect_evidence(self, session: GameSession, text: str, evidence_id: str | None = None) -> str:
        evidence_id = evidence_id or self._evidence_from_text(text) or "release_timeline"
        self._discover_evidence(session, evidence_id)
        evidence = session.evidences[evidence_id]
        self._append_event(session, "Player", f"Evidence 확인: {evidence.title}", "evidence")
        return evidence.content

    def _show_evidence(self, session: GameSession, target_id: str | None, evidence_id: str | None = None) -> str:
        evidence_id = evidence_id or next(iter(session.discovered_evidence), "qa_warning_message")
        self._discover_evidence(session, evidence_id)
        evidence = session.evidences[evidence_id]
        target = target_id or "qa_01"
        if target in session.npcs:
            self._append_event(session, "Player", f"{session.npcs[target].name}에게 {evidence.title}를 제시했습니다.", "evidence")
            if target == "backend_01" and evidence_id == "qa_warning_message":
                self._update_backend_after_warning(session)
                return "Backend Developer가 QA 경고를 확인했습니다. 위험을 알고도 배포했는지 되짚기 시작합니다."
        return evidence.content

    def _request_evidence(
        self,
        session: GameSession,
        target_id: str | None,
        evidence_id: str | None,
        text: str,
    ) -> str:
        evidence_id = evidence_id or self._evidence_from_text(text) or "qa_warning_message"
        self._discover_evidence(session, evidence_id)
        evidence = session.evidences[evidence_id]
        actor = session.npcs[target_id].name if target_id in session.npcs else "System"
        self._append_event(
            session,
            actor,
            f"{evidence.title}를 공개했습니다.\n{evidence.content}",
            "evidence",
            target_id,
        )
        return evidence.content

    def _talk_npc(self, session: GameSession, target_id: str | None, player_input: str = "") -> str:
        target_id = target_id or "qa_01"
        npc = session.npcs.get(target_id)
        if npc is None:
            self._append_event(session, "System", "대화할 NPC를 찾지 못했습니다.", "guardrail")
            return "대화할 NPC를 찾지 못했습니다."
        decision, provider_fallback = self._request_decision(session, npc, "talk", player_input)
        self._apply_decision(session, npc, decision, f"Player talked to {npc.name}.", provider_fallback)
        self._append_event(session, npc.name, decision.dialogue, "dialogue", npc.id)
        return decision.dialogue

    def _ask_npc(self, session: GameSession, target_id: str | None, player_input: str = "") -> str:
        target_id = target_id or "qa_01"
        npc = session.npcs.get(target_id)
        if npc is None:
            self._append_event(session, "System", "질문할 NPC를 찾지 못했습니다.", "guardrail")
            return "질문할 NPC를 찾지 못했습니다."
        decision, provider_fallback = self._request_decision(session, npc, "ask", player_input)
        self._apply_decision(session, npc, decision, f"Player asked {npc.name} about the incident.", provider_fallback)
        self._append_event(session, npc.name, decision.dialogue, "dialogue", npc.id)
        return decision.dialogue

    def _accuse_npc(self, session: GameSession, target_id: str | None, player_input: str = "") -> str:
        target_id = target_id or "qa_01"
        npc = session.npcs.get(target_id)
        if npc is None:
            self._append_event(session, "System", "책임을 물을 NPC를 찾지 못했습니다.", "guardrail")
            return "책임을 물을 NPC를 찾지 못했습니다."
        decision, provider_fallback = self._request_decision(session, npc, "accuse", player_input)
        self._apply_decision(session, npc, decision, f"Player accused {npc.name}.", provider_fallback)
        self._append_event(session, npc.name, decision.dialogue, "dialogue", npc.id)
        if decision.action_type == "show_evidence" and decision.action_target:
            self._discover_evidence(session, decision.action_target)
            self._append_event(session, npc.name, "QA Warning evidence를 공개했습니다.", "evidence", npc.id)
        return decision.dialogue

    def _defend_npc(self, session: GameSession, target_id: str | None, player_input: str = "") -> str:
        target_id = target_id or "qa_01"
        npc = session.npcs.get(target_id)
        if npc is None:
            self._append_event(session, "System", "옹호할 NPC를 찾지 못했습니다.", "guardrail")
            return "옹호할 NPC를 찾지 못했습니다."
        decision, provider_fallback = self._request_decision(session, npc, "defend", player_input)
        self._apply_decision(session, npc, decision, f"Player defended {npc.name}.", provider_fallback)
        self._append_event(session, npc.name, decision.dialogue, "dialogue", npc.id)
        return decision.dialogue

    def _request_decision(
        self,
        session: GameSession,
        npc: NPCState,
        mode: str,
        player_input: str,
    ) -> tuple[AgentDecision, bool]:
        context = DecisionContext(
            mode=mode,
            player_input=player_input,
            turn=session.turn,
            npc=npc,
            target_npc_id=npc.id,
            available_facts=tuple(
                f"{fact_id}: {FACT_REGISTRY[fact_id].statement}"
                for fact_id in npc.known_fact_ids
                if fact_id in FACT_REGISTRY
            ),
            available_evidence_ids=tuple(session.evidences),
            incident_rules=tuple(INCIDENT_RULES),
        )
        try:
            return self.provider.decide(context), False
        except ProviderError as exc:
            self._record_fallback(
                session,
                stage="decision_provider",
                provider=self.provider.name,
                reason=str(exc),
            )
            return self.fallback_provider.decide(context), True

    def _apply_decision(
        self,
        session: GameSession,
        npc: NPCState,
        decision: AgentDecision,
        event: str,
        provider_fallback: bool = False,
    ) -> None:
        checks = self._validate_decision(session, npc, decision)
        rejected = any(not check.passed for check in checks)
        if rejected:
            failed_checks = ", ".join(check.name for check in checks if not check.passed)
            self._record_fallback(
                session,
                stage="decision_guardrail",
                provider=self.provider.name,
                reason=f"Decision guardrail rejected: {failed_checks}",
            )
            trace_decision = self._safe_fallback(npc)
            guardrails = checks + [GuardrailCheck(name="fallback_action", passed=True, detail="Safe dialogue fallback applied.")]
            fallback_used = True
        else:
            trace_decision = decision
            guardrails = checks
            fallback_used = False

        npc.dynamic_state = self._bounded_dynamic_state(npc.dynamic_state, trace_decision)
        player_relationship = session.relationships[relationship_key(npc.id, "player")]
        session.relationships[relationship_key(npc.id, "player")] = player_relationship.model_copy(
            update={"trust": npc.dynamic_state.trust_toward_player, "last_changed_turn": session.turn}
        )
        for belief in trace_decision.belief_updates:
            self._upsert_belief(npc, belief)
        for relationship_update in trace_decision.relationship_updates:
            self._apply_relationship_update(session, npc, relationship_update)
        if trace_decision.memory_candidate:
            duplicate_memory = any(
                memory.summary.casefold() == trace_decision.memory_candidate.summary.casefold()
                for memory in (*npc.recent_memories, *npc.important_memories)
            )
            if not duplicate_memory:
                npc.recent_memories.append(trace_decision.memory_candidate)
                if trace_decision.memory_candidate.importance >= 0.75:
                    npc.important_memories.append(trace_decision.memory_candidate)
            npc.recent_memories = npc.recent_memories[-8:]
            npc.important_memories = npc.important_memories[-8:]

        session.agent_traces.append(
            AgentTrace(
                id=len(session.agent_traces) + 1,
                turn=session.turn,
                event=event,
                npc_id=npc.id,
                provider=self.provider.name,
                context_summary=f"{npc.name} evaluated the player's latest action using its private knowledge boundary.",
                known_fact_ids=list(npc.known_fact_ids),
                known_facts=list(npc.known_facts),
                retrieved_rules=list(INCIDENT_RULES[:1]) if npc.id == "qa_01" else [],
                decision=trace_decision,
                requested_decision=decision if rejected else None,
                guardrails=guardrails,
                fallback_used=provider_fallback or fallback_used,
            )
        )

    def _validate_decision(self, session: GameSession, npc: NPCState, decision: AgentDecision) -> list[GuardrailCheck]:
        action_targets = set(session.evidences) | set(session.npcs) | {None}
        belief_subjects = set(session.npcs) | {"player", "incident"}
        return [
            GuardrailCheck(
                name="npc_exists",
                passed=decision.npc_id in session.npcs,
                detail="NPC exists in the current session.",
            ),
            GuardrailCheck(
                name="evidence_exists",
                passed=decision.action_target in action_targets,
                detail="Action target is a known NPC, evidence, or empty target.",
            ),
            GuardrailCheck(
                name="action_type_allowed",
                passed=decision.action_type in ALLOWED_AGENT_ACTION_TYPES,
                detail="Decision action type is in the server-owned action vocabulary.",
            ),
            GuardrailCheck(
                name="belief_subjects_valid",
                passed=all(belief.subject in belief_subjects for belief in decision.belief_updates),
                detail="Belief updates reference a known NPC, player, or incident.",
            ),
            GuardrailCheck(
                name="relationship_targets_valid",
                passed=all(update.target_npc_id in session.npcs for update in decision.relationship_updates),
                detail="Relationship updates reference NPCs in the current session.",
            ),
            GuardrailCheck(
                name="knowledge_refs_exist",
                passed=all(fact_id in FACT_REGISTRY for fact_id in decision.knowledge_refs),
                detail="Knowledge references exist in the server-owned Fact Registry.",
            ),
            GuardrailCheck(
                name="knowledge_refs_present",
                passed=bool(decision.knowledge_refs),
                detail="NPC dialogue decisions include at least one factual grounding reference.",
            ),
            GuardrailCheck(
                name="knowledge_refs_known_by_npc",
                passed=all(fact_id in npc.known_fact_ids for fact_id in decision.knowledge_refs),
                detail="Knowledge references are inside the NPC knowledge boundary.",
            ),
            GuardrailCheck(
                name="knowledge_refs_evidence_valid",
                passed=all(
                    FACT_REGISTRY[fact_id].revealable
                    and all(evidence_id in session.evidences for evidence_id in FACT_REGISTRY[fact_id].source_evidence_ids)
                    for fact_id in decision.knowledge_refs
                    if fact_id in FACT_REGISTRY
                ),
                detail="Knowledge references are revealable and their evidence exists in the current world state.",
            ),
            GuardrailCheck(
                name="state_ranges_valid",
                passed=all(-100 <= value <= 100 for value in (decision.trust_delta, decision.stress_delta, decision.cooperation_delta)),
                detail="Decision deltas are within the allowed range.",
            ),
        ]

    def _safe_fallback(self, npc: NPCState) -> AgentDecision:
        return AgentDecision(
            npc_id=npc.id,
            emotion=npc.dynamic_state.emotion,
            stress_delta=0,
            trust_delta=0,
            cooperation_delta=0,
            action_type="dialogue",
            dialogue="현재 질문에 답하기 전에 확인할 수 있는 정보부터 정리하겠습니다.",
        )

    def _bounded_dynamic_state(self, state: DynamicState, decision: AgentDecision) -> DynamicState:
        return DynamicState(
            emotion=decision.emotion,
            stress=max(0, min(100, state.stress + decision.stress_delta)),
            trust_toward_player=max(-100, min(100, state.trust_toward_player + decision.trust_delta)),
            cooperation=max(0, min(100, state.cooperation + decision.cooperation_delta)),
        )

    def _upsert_belief(self, npc: NPCState, belief: Belief) -> None:
        for index, existing in enumerate(npc.beliefs):
            if existing.subject == belief.subject:
                npc.beliefs[index] = belief
                return
        npc.beliefs.append(belief)

    def _apply_relationship_update(self, session: GameSession, npc: NPCState, update: RelationshipUpdate) -> None:
        for index, relationship in enumerate(npc.relationships):
            if relationship.target_npc_id == update.target_npc_id:
                npc.relationships[index] = Relationship(
                    target_npc_id=relationship.target_npc_id,
                    trust=max(-100, min(100, relationship.trust + update.trust_delta)),
                    tension=max(0, min(100, relationship.tension + update.tension_delta)),
                )
                break
        else:
            npc.relationships.append(
                Relationship(
                    target_npc_id=update.target_npc_id,
                    trust=max(-100, min(100, update.trust_delta)),
                    tension=max(0, min(100, update.tension_delta)),
                )
            )

        edge_id = relationship_key(npc.id, update.target_npc_id)
        edge = session.relationships[edge_id]
        session.relationships[edge_id] = edge.model_copy(
            update={
                "trust": max(-100, min(100, edge.trust + update.trust_delta)),
                "tension": max(0, min(100, edge.tension + update.tension_delta)),
                "last_changed_turn": session.turn,
            }
        )

    def _update_backend_after_warning(self, session: GameSession) -> None:
        backend = session.npcs["backend_01"]
        belief = Belief(
            subject="incident",
            belief="The ignored QA warning and API schema change jointly enabled the outage.",
            confidence=0.85,
        )
        self._upsert_belief(backend, belief)
        backend.dynamic_state = backend.dynamic_state.model_copy(
            update={
                "emotion": "uneasy",
                "stress": min(100, backend.dynamic_state.stress + 8),
                "trust_toward_player": min(100, backend.dynamic_state.trust_toward_player + 3),
            }
        )
        player_relationship = session.relationships[relationship_key(backend.id, "player")]
        session.relationships[relationship_key(backend.id, "player")] = player_relationship.model_copy(
            update={"trust": backend.dynamic_state.trust_toward_player, "last_changed_turn": session.turn}
        )
        backend.recent_memories.append(
            Memory(summary="Player showed the QA warning message during the incident review.", importance=0.7, turn=session.turn)
        )
        backend.recent_memories = backend.recent_memories[-8:]
        session.agent_traces.append(
            AgentTrace(
                id=len(session.agent_traces) + 1,
                turn=session.turn,
                event="Player showed QA warning evidence to Backend Developer.",
                npc_id=backend.id,
                provider=self.provider.name,
                context_summary="Backend Developer evaluated newly revealed evidence against its existing belief.",
                known_fact_ids=list(backend.known_fact_ids),
                known_facts=list(backend.known_facts),
                retrieved_rules=list(INCIDENT_RULES),
                decision=AgentDecision(
                    npc_id=backend.id,
                    emotion=backend.dynamic_state.emotion,
                    stress_delta=8,
                    trust_delta=3,
                    cooperation_delta=0,
                    belief_updates=[belief],
                    knowledge_refs=["qa_sent_warning", "backend_changed_api_schema"],
                    action_type="belief_update",
                    dialogue="QA warning evidence와 API schema 변경의 연관성을 새롭게 반영했습니다.",
                ),
                guardrails=[
                    GuardrailCheck(name="npc_exists", passed=True, detail="NPC exists in the current session."),
                    GuardrailCheck(name="evidence_exists", passed=True, detail="Evidence exists in the current session."),
                    GuardrailCheck(name="state_ranges_valid", passed=True, detail="State remains within allowed ranges."),
                ],
            )
        )

    def _discover_evidence(self, session: GameSession, evidence_id: str) -> None:
        if evidence_id in session.evidences:
            session.discovered_evidence.add(evidence_id)

    def _evidence_from_text(self, text: str) -> str | None:
        normalized = text.lower()
        if "api" in normalized or "스키마" in normalized:
            return "api_schema_diff"
        if "일정" in normalized or "timeline" in normalized:
            return "release_timeline"
        if "qa" in normalized or "경고" in normalized or "warning" in normalized or "메시지" in normalized:
            return "qa_warning_message"
        return None

    def _score_report(self, session: GameSession, report: IncidentReportRequest) -> GameResult:
        diagnosis_terms = ("api", "schema", "스키마", "백엔드", "backend", "qa", "검증", "배포")
        diagnosis = 85 if sum(term in report.primary_cause.lower() for term in diagnosis_terms) >= 2 else 35
        coverage = min(100, 20 + len(session.discovered_evidence) * 25)
        average_trust = sum(
            session.relationships[relationship_key(npc.id, "player")].trust
            for npc in session.npcs.values()
        ) / len(session.npcs)
        team_trust = max(0, min(100, round(60 + average_trust / 2)))
        efficiency = max(20, min(100, 100 - max(0, session.turn - 5) * 4))
        return GameResult(
            incident_diagnosis=diagnosis,
            evidence_coverage=coverage,
            team_trust=team_trust,
            recovery_efficiency=efficiency,
            summary="API schema 변경이 QA 검증 완료 전에 배포된 것이 직접 원인으로 평가되었습니다.",
        )

    def _record_fallback(self, session: GameSession, stage: str, provider: str, reason: str) -> None:
        safe_reason = " ".join(reason.split())[-320:] or "Unknown provider failure"
        notice = FallbackNotice(
            id=len(session.fallback_notices) + 1,
            turn=session.turn,
            stage=stage,
            provider=provider,
            reason=safe_reason,
            created_at=datetime.now(UTC),
        )
        session.fallback_notices.append(notice)
        logger.warning(
            "deterministic_fallback stage=%s provider=%s turn=%s reason=%s",
            stage,
            provider,
            session.turn,
            safe_reason,
        )
        self._append_event(
            session,
            "DETERMINISTIC FALLBACK",
            f"{stage} · {provider} 실패로 deterministic fallback을 사용했습니다. {safe_reason}",
            "fallback",
        )

    def _append_event(self, session: GameSession, actor: str, message: str, event_type: str, actor_id: str | None = None) -> None:
        session.events.append(
            EventLogEntry(
                id=len(session.events) + 1,
                turn=session.turn,
                actor=actor,
                actor_id=actor_id,
                message=message,
                event_type=event_type,
                created_at=datetime.now(UTC),
            )
        )

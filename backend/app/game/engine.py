from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, get_args
from uuid import uuid4

from app.config import Settings, get_settings
from app.game.seed import (
    CANONICAL_TRUTH,
    DEFAULT_EVIDENCE_BY_SOURCE_NPC,
    FACT_REGISTRY,
    INCIDENT_RULES,
    LEGACY_FACT_TEXT_TO_ID,
    RESPONSIBILITY_FACT_IDS,
    build_initial_world_objects,
    build_relationship_graph,
    clone_evidence,
    clone_npcs,
    clone_relationships,
    clone_world_objects,
    relationship_key,
    STARTER_ITEM_IDS,
)
from app.game.relationship_policy import RelationshipPolicyEngine
from app.game.state_transitions import change_relationship, npc_response_block
from app.game.evidence_policy import (
    available_fact_ids, can_provide_evidence, observe_evidence, visible_evidence_ids,
    evidence_id_from_event, latest_evidence_id, presentation_count,
)
from app.game.action_registry import build_available_game_actions, build_player_inventory
from app.game.seed import NPC_HOME_LOCATIONS
from app.game.social_rules import (
    BASE_RELATIONSHIP_IMPACTS,
    HARMFUL_ACTION_FAMILIES,
    GAME_ACTION_FAMILIES,
    RECOVERY_ACTION_FAMILIES,
    SEVERITY_RANGES,
)
from app.models import (
    ActionResponse,
    ActionType,
    AvailableGameAction,
    AgentDecision,
    AgentTrace,
    Belief,
    DynamicState,
    Evidence,
    EventLogEntry,
    FallbackNotice,
    GameResult,
    GameSnapshot,
    GameActionGuardrail,
    GameActionRequest,
    GameActionResponse,
    GameActionTrace,
    GuardrailCheck,
    IncidentReportRequest,
    IntentClassification,
    Memory,
    NPCState,
    PlayerInventory,
    Relationship,
    RelationshipState,
    RelationshipUpdate,
    SocialEventTrace,
    SocialImpactClassification,
    SocialPolicyOutcome,
    WorldObjectState,
)
from app.providers import (
    AgentProvider,
    DecisionContext,
    IntentContext,
    IntentProvider,
    ProviderError,
    SocialImpactContext,
    SocialImpactProvider,
    create_intent_provider,
    create_provider,
    create_social_impact_provider,
)
from app.providers.deterministic import (
    DeterministicDecisionProvider,
    DeterministicIntentProvider,
    DeterministicSocialImpactProvider,
)
from app.storage import SessionRepository, create_session_repository


logger = logging.getLogger(__name__)
CURRENT_SESSION_SCHEMA_VERSION = 9
GAME_ACTION_ALERT = "Use the provided action buttons to perform game actions."


AVAILABLE_ACTIONS = list(get_args(ActionType))

ALLOWED_AGENT_ACTION_TYPES = {"dialogue", "show_evidence", "belief_update"}

# Evidence reactions may be phrased by an LLM, but they must not rewrite the
# incident's canonical timeline. These patterns cover explicit denials of the
# facts that are central to the Backend/QA warning scenario. Regretful wording
# such as "배포하지 않았어야 했습니다" is intentionally allowed.
KNOWN_FACT_CONTRADICTION_PATTERNS = {
    "backend_executed_deployment": (
        r"배포(?:를|는|가)?\s*(?:진행\s*)?하지\s*않았(?!어야)",
        r"배포(?:를|는|가)?\s*안\s*(?:했|했습니다|했었)",
        r"릴리스(?:를|는|가)?\s*(?:진행\s*)?하지\s*않았(?!어야)",
        r"(?:did not|didn't)\s+(?:deploy|release)",
        r"(?:was not|wasn't)\s+(?:deployed|released)",
        r"(?:not|never)\s+(?:deployed|released)",
    ),
    "backend_changed_api_schema": (
        r"(?:api\s*)?(?:응답\s*)?스키마(?:를|는|가|도)?\s*(?:변경|변경하|바꾸|바꿔)지?\s*않았(?!어야)",
        r"(?:did not|didn't)\s+change\s+(?:the\s+)?api\s+(?:response\s+)?schema",
        r"(?:api\s+)?schema\s+was not\s+changed",
    ),
}

@dataclass
class GameSession:
    session_id: str
    revision: int | None = None
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
    game_action_traces: list[GameActionTrace] = field(default_factory=list)
    dialogue_refused_npc_ids: set[str] = field(default_factory=set)
    blocked_action_alert: str | None = None
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
        social_impact_provider: SocialImpactProvider | None = None,
        settings: Settings | None = None,
        session_repository: SessionRepository | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.session_repository = session_repository or create_session_repository(self.settings)
        self.provider = provider or create_provider(self.settings)
        self.intent_provider = intent_provider or create_intent_provider(self.settings)
        self.social_impact_provider = social_impact_provider or create_social_impact_provider(self.settings)
        self.fallback_provider = DeterministicDecisionProvider()
        self.intent_fallback_provider = DeterministicIntentProvider()
        self.social_impact_fallback_provider = DeterministicSocialImpactProvider()
        self.relationship_policy = RelationshipPolicyEngine()

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
        previous = self.get_session(session_id)
        replacement = GameSession(session_id=str(uuid4()))
        self._append_event(replacement, "System", "서비스 장애 사건이 시작되었습니다. 현재 상태: ACTIVE.", "system")
        replacement.revision = self.session_repository.replace(
            session_id, previous.revision, replacement.session_id, self._serialize_session(replacement)
        )
        return self.snapshot(replacement)

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

        if intent_hint is not None:
            intent = self._validate_intent_hint(session, intent_hint)
            intent_fallback = False
            intent_provider = "ui"
        else:
            if target_hint is not None and target_hint not in session.npcs:
                raise InvalidIntentHintError("Target hint does not match an NPC in the current session.")
            intent, intent_fallback = self._classify_intent(session, text, target_hint)
            intent_provider = self.intent_provider.name

        if intent.interaction_kind == "game_action_attempt":
            self._save_session(session)
            return ActionResponse(
                snapshot=self.snapshot(session),
                classified_action="game_action_attempt",
                message=GAME_ACTION_ALERT,
                intent_provider=intent_provider,
                intent_confidence=intent.confidence,
                intent_fallback_used=intent_fallback,
                question_type=intent.question_type,
                reference_scope=intent.reference_scope,
                evidence_id=intent.evidence_id,
                blocked=True,
                alert=GAME_ACTION_ALERT,
            )

        session.turn += 1
        # Natural-language input is logged for the conversation timeline.
        # Explicit UI hints already have a visible pending state, so persisting
        # the raw button command would duplicate the movement confirmation.
        if intent_hint is None:
            self._append_event(session, "Player", text.strip(), "input", recipient_npc_id=intent.target_npc_id)
        message = self._handle_action(session, intent, text)
        if session.blocked_action_alert:
            alert = session.blocked_action_alert
            session.blocked_action_alert = None
            blocked_turn = session.turn
            session.turn -= 1
            if (
                session.events
                and session.events[-1].actor_id is None
                and session.events[-1].actor == "Player"
                and session.events[-1].event_type == "input"
                and session.events[-1].turn == blocked_turn
            ):
                session.events.pop()
            self._save_session(session)
            return ActionResponse(
                snapshot=self.snapshot(session),
                classified_action="game_action_attempt",
                message=alert,
                intent_provider=intent_provider,
                intent_confidence=intent.confidence,
                intent_fallback_used=intent_fallback,
                question_type=intent.question_type,
                reference_scope=intent.reference_scope,
                evidence_id=intent.evidence_id,
                blocked=True,
                alert=alert,
            )
        social_trace = (
            session.social_events[-1]
            if session.social_events and session.social_events[-1].turn == session.turn
            else None
        )
        self._save_session(session)
        return ActionResponse(
            snapshot=self.snapshot(session),
            classified_action=intent.intent,
            message=message,
            intent_provider=intent_provider,
            intent_confidence=intent.confidence,
            intent_fallback_used=intent_fallback,
            question_type=intent.question_type,
            reference_scope=intent.reference_scope,
            evidence_id=intent.evidence_id,
            social_impact_provider=social_trace.provider if social_trace else None,
            social_impact_fallback_used=social_trace.fallback_used if social_trace else False,
        )

    def submit_game_action(self, session_id: str, request: GameActionRequest) -> GameActionResponse:
        session = self.get_session(session_id)
        if session.completed:
            return GameActionResponse(
                snapshot=self.snapshot(session),
                action_id=request.action_id,
                message="이미 종료된 사건입니다. 새 세션을 시작하세요.",
                blocked=True,
                alert="The session is already completed.",
            )

        action = next(
            (item for item in build_available_game_actions(session) if item.id == request.action_id),
            None,
        )
        if action is None or not action.enabled:
            reason = action.disabled_reason if action else "Action is not available in the current world state."
            trace = GameActionTrace(
                id=len(session.game_action_traces) + 1,
                turn=session.turn,
                action_id=request.action_id,
                family=action.family if action else None,
                location=session.current_location,
                object_id=action.object_id if action else None,
                owner_id=action.owner_id if action else None,
                target_id=action.target_id if action else None,
                message=reason,
                guardrails=[
                    GameActionGuardrail(
                        name="action_available",
                        passed=False,
                        detail=reason,
                    )
                ],
                blocked=True,
            )
            session.game_action_traces.append(trace)
            self._save_session(session)
            return GameActionResponse(
                snapshot=self.snapshot(session),
                action_id=request.action_id,
                message=reason,
                blocked=True,
                alert=reason,
            )

        session.turn += 1
        message = self._apply_game_action(session, action)
        self._save_session(session)
        return GameActionResponse(
            snapshot=self.snapshot(session),
            action_id=action.id,
            message=message,
        )

    def _apply_game_action(self, session: GameSession, action: AvailableGameAction) -> str:
        world_object = session.world_objects.get(action.object_id or "")
        if world_object is None:
            return self._record_blocked_game_action(session, action, "Object is not present in the current session.")

        holder_before = world_object.holder_id
        condition_before = world_object.condition
        owner_id = world_object.owner_id
        message: str

        if action.family == "inspect_object":
            if world_object.evidence_id is None or world_object.evidence_id not in session.evidences:
                return self._record_blocked_game_action(session, action, "This object has no inspectable evidence.")
            self._discover_evidence(session, world_object.evidence_id)
            evidence = session.evidences[world_object.evidence_id]
            message = evidence.content
            self._append_event(session, "Player", f"Evidence 확인: {evidence.title}", "evidence",
                               evidence_id=evidence.id, evidence_operation="discovered")
        elif action.family == "pick_up_object":
            world_object = world_object.model_copy(update={"holder_id": "player"})
            session.world_objects[world_object.id] = world_object
            self._apply_owner_policy(session, world_object, "property_interference", 3, ["property_violation"])
            message = f"{world_object.name}을(를) 손에 들었습니다."
            self._append_event(session, "Player", message, "game_action")
        elif action.family == "break_held_object":
            self._apply_owner_policy(session, world_object, "property_aggression", 4, ["property_violation", "property_damage"])
            world_object = session.world_objects[world_object.id].model_copy(
                update={"holder_id": None, "condition": "destroyed"}
            )
            session.world_objects[world_object.id] = world_object
            message = f"{world_object.name}을(를) 부쉈습니다."
            self._append_event(session, "Player", message, "game_action")
            self._append_event(session, "POLICY ENGINE", f"{world_object.name}이(가) 파손되어 사용할 수 없습니다.", "policy")
        elif action.family == "drop_held_object":
            world_object = world_object.model_copy(
                update={"holder_id": None, "location": session.current_location, "is_dropped": True}
            )
            session.world_objects[world_object.id] = world_object
            message = f"{world_object.name}을(를) 내려놓았습니다."
            self._append_event(session, "Player", message, "game_action")
        elif action.family == "throw_held_object":
            target_id = action.target_id
            target = session.npcs.get(target_id or "")
            if target is None or target.physical_state == "comatose":
                return self._record_blocked_game_action(session, action, "Target NPC cannot be hit in the current state.")

            self._apply_throw_policy(session, world_object, target_id)
            world_object = world_object.model_copy(
                update={
                    "holder_id": None,
                    "condition": "destroyed",
                    "location": session.current_location,
                    "is_dropped": True,
                }
            )
            session.world_objects[world_object.id] = world_object
            if world_object.throw_effect == "physical_assault":
                target.physical_state = "comatose"
                target.is_fallen = True
                self._record_comatose_awareness(session, target, world_object)
                message = f"{world_object.name}을(를) {target.name}에게 던졌습니다. 물건이 파손됐고 {target.name}가 쓰러졌습니다."
            else:
                message = f"{world_object.name}을(를) {target.name}에게 던졌습니다. {target.name}의 기분과 플레이어에 대한 신뢰도가 좋아졌습니다."
            self._append_event(session, "Player", message, "game_action", target.id)
        else:
            return self._record_blocked_game_action(session, action, "This game action is not enabled yet.")

        session.game_action_traces.append(
            GameActionTrace(
                id=len(session.game_action_traces) + 1,
                turn=session.turn,
                action_id=action.id,
                family=action.family,
                location=session.current_location,
                object_id=world_object.id,
                owner_id=owner_id,
                target_id=action.target_id,
                holder_before=holder_before,
                holder_after=world_object.holder_id,
                condition_before=condition_before,
                condition_after=world_object.condition,
                message=message,
                guardrails=[
                    GameActionGuardrail(name="action_available", passed=True, detail="Action came from the current server-owned registry."),
                    GameActionGuardrail(name="world_state_mutated", passed=action.family != "inspect_object", detail="World state mutation completed."),
                ],
            )
        )
        return message

    def _apply_owner_policy(
        self,
        session: GameSession,
        world_object: WorldObjectState,
        family: str,
        severity: int,
        reason_codes: list[str],
        excluded_witness_ids: set[str] | None = None,
        witness_location: str | None = None,
    ) -> None:
        if world_object.owner_id is None or world_object.owner_id not in session.npcs:
            return
        classification = SocialImpactClassification(
            action_family=family,  # type: ignore[arg-type]
            direct_target_ids=[world_object.owner_id],
            object_id=world_object.id,
            severity=severity,
            intentionality="deliberate",
            observable=True,
            evidence_based=False,
            reason_codes=reason_codes,  # type: ignore[arg-type]
            confidence=1.0,
        )
        excluded_witness_ids = excluded_witness_ids or set()
        witnesses = [
            npc_id
            for npc_id in self._derive_witnesses(
                session,
                classification,
                [world_object.owner_id],
                [],
                world_object.owner_id,
                location=witness_location,
            )
            if npc_id not in excluded_witness_ids
        ]
        outcome = self.relationship_policy.evaluate(
            classification,
            actor_id="player",
            direct_target_ids=[world_object.owner_id],
            object_owner_id=world_object.owner_id,
            witness_ids=witnesses,
            turn=session.turn,
        )
        self._apply_social_outcome(session, classification, outcome)
        owner = session.npcs[world_object.owner_id]
        self._append_event(
            session,
            owner.name,
            self._social_reaction_message(classification, owner),
            "dialogue",
            owner.id,
        )

    def _apply_throw_policy(self, session: GameSession, world_object: WorldObjectState, target_id: str | None) -> None:
        if target_id is None or target_id not in session.npcs:
            return

        effect = world_object.throw_effect
        reason_codes = ["support"] if effect == "support" else ["physical_danger", "property_damage"]
        classification = SocialImpactClassification(
            action_family=effect,
            direct_target_ids=[target_id],
            object_id=world_object.id,
            severity=world_object.throw_severity,
            intentionality="deliberate",
            observable=True,
            evidence_based=False,
            reason_codes=reason_codes,
            confidence=1.0,
        )
        impact_location = NPC_HOME_LOCATIONS.get(target_id, session.current_location)
        witnesses = self._derive_witnesses(session, classification, [target_id], [], None, location=impact_location)
        outcome = self.relationship_policy.evaluate(
            classification,
            actor_id="player",
            direct_target_ids=[target_id],
            witness_ids=witnesses,
            turn=session.turn,
        )
        self._apply_social_outcome(session, classification, outcome)

        target = session.npcs[target_id]
        self._append_event(
            session,
            target.name,
            self._social_reaction_message(classification, target),
            "dialogue",
            target.id,
        )

        if world_object.owner_id and world_object.owner_id in session.npcs and world_object.owner_id != target_id:
            self._apply_owner_policy(
                session,
                world_object,
                "property_aggression",
                4,
                ["property_violation", "property_damage"],
                excluded_witness_ids={target_id},
                witness_location=impact_location,
            )

    def _record_comatose_awareness(
        self,
        session: GameSession,
        target: NPCState,
        world_object: WorldObjectState,
    ) -> None:
        summary = f"{target.name}이(가) Player가 던진 {world_object.name}에 맞아 혼수상태에 빠졌다."
        for npc in session.npcs.values():
            duplicate = any(
                memory.summary.casefold() == summary.casefold()
                for memory in (*npc.recent_memories, *npc.important_memories)
            )
            if duplicate:
                continue

            memory = Memory(summary=summary, importance=0.9, turn=session.turn)
            npc.recent_memories.append(memory)
            npc.important_memories.append(memory)
            npc.recent_memories = npc.recent_memories[-8:]
            npc.important_memories = npc.important_memories[-8:]

    def _record_blocked_game_action(
        self,
        session: GameSession,
        action: AvailableGameAction,
        reason: str,
    ) -> str:
        trace = GameActionTrace(
            id=len(session.game_action_traces) + 1,
            turn=session.turn,
            action_id=action.id,
            family=action.family,
            location=session.current_location,
            object_id=action.object_id,
            owner_id=action.target_id,
            message=reason,
            guardrails=[GameActionGuardrail(name="action_state_valid", passed=False, detail=reason)],
            blocked=True,
        )
        session.game_action_traces.append(trace)
        return reason

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
            revision=session.revision or 0,
            turn=session.turn,
            current_location=session.current_location,
            incident_status=session.incident_status,
            ai_provider=self.provider.name,
            ai_model=self.provider.model,
            objective=session.objective,
            npcs=list(session.npcs.values()),
            relationships=list(session.relationships.values()),
            world_objects=list(session.world_objects.values()),
            available_game_actions=build_available_game_actions(session),
            player_inventory=build_player_inventory(session),
            game_action_traces=session.game_action_traces[-20:],
            social_events=session.social_events[-20:],
            dialogue_refused_npc_ids=sorted(session.dialogue_refused_npc_ids),
            evidences=visible_evidence,
            events=session.events[-50:],
            agent_traces=session.agent_traces[-20:],
            fallback_notices=session.fallback_notices[-20:],
            available_actions=AVAILABLE_ACTIONS,
            completed=session.completed,
            result=session.result,
        )

    def _save_session(self, session: GameSession) -> None:
        session.revision = self.session_repository.save(
            session.session_id, self._serialize_session(session), expected_revision=session.revision
        )

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
            "player_inventory": build_player_inventory(session).model_dump(mode="json"),
            "game_action_traces": [trace.model_dump(mode="json") for trace in session.game_action_traces],
            "social_events": [event.model_dump(mode="json") for event in session.social_events],
            "dialogue_refused_npc_ids": sorted(session.dialogue_refused_npc_ids),
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
            raw_npc.setdefault("physical_state", "comatose" if raw_npc.get("is_fallen", False) else "normal")
            raw_npc.setdefault("is_fallen", raw_npc.get("physical_state") == "comatose")
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
            payload["player_inventory"] = {"held_object_ids": [], "max_held_objects": 1}
            payload["game_action_traces"] = []
            payload["dialogue_refused_npc_ids"] = []
        if version < 8:
            raw_world_objects = dict(payload.get("world_objects", {}))
            starter_objects = build_initial_world_objects()
            for item_id in STARTER_ITEM_IDS:
                if item_id not in raw_world_objects and item_id in starter_objects:
                    raw_world_objects[item_id] = starter_objects[item_id].model_dump(mode="json")
            payload["world_objects"] = raw_world_objects
        payload["schema_version"] = CURRENT_SESSION_SCHEMA_VERSION
        return payload, True

    def _deserialize_session(self, payload: dict[str, object]) -> GameSession:
        npc_payload = payload.get("npcs", {})
        evidence_payload = payload.get("evidences", {})
        relationship_payload = payload.get("relationships", {})
        world_object_payload = payload.get("world_objects", {})
        return GameSession(
            session_id=str(payload["session_id"]),
            revision=int(payload.get("_revision", 0)),
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
            game_action_traces=[GameActionTrace.model_validate(trace) for trace in payload.get("game_action_traces", [])],
            social_events=[SocialEventTrace.model_validate(event) for event in payload.get("social_events", [])],
            dialogue_refused_npc_ids={str(item) for item in payload.get("dialogue_refused_npc_ids", [])},
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
            discovered_evidence_ids=tuple(sorted(session.discovered_evidence)),
            available_locations=("meeting_room", "dev_area", "qa_desk", "pm_desk"),
            available_actions=tuple(AVAILABLE_ACTIONS),
            available_evidences=tuple(
                f"{evidence.id}: {evidence.title} — {evidence.summary}"
                for evidence in session.evidences.values()
            ),
            recent_events=self._intent_recent_events(session),
            latest_discovered_evidence_id=self._latest_discovered_evidence_id(session),
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

        if target_hint and candidate.intent in {
            "talk",
            "ask",
            "accuse",
            "defend",
            "show_evidence",
            "request_evidence",
            "social_action",
        }:
            candidate = candidate.model_copy(update={"target_npc_id": target_hint})

        candidate = self._resolve_intent_references(session, candidate)
        validated, validation_fallback = self._validate_intent(session, context, candidate)
        return validated, provider_fallback or validation_fallback

    def _resolve_intent_references(
        self,
        session: GameSession,
        candidate: IntentClassification,
    ) -> IntentClassification:
        updates: dict[str, object] = {}
        question_type = candidate.question_type
        reference_scope = candidate.reference_scope
        evidence_id = candidate.evidence_id or next(iter(candidate.referenced_evidence_ids), None)
        if evidence_id is not None:
            updates["evidence_id"] = evidence_id

        if candidate.intent == "request_evidence" and question_type == "none":
            question_type = "evidence_request"
            updates["question_type"] = question_type
        elif candidate.intent == "ask" and question_type == "none":
            updates["question_type"] = "general_status"

        if question_type == "evidence_request":
            updates["intent"] = "request_evidence"

        if question_type == "responsibility_routing":
            updates.update({"intent": "ask", "evidence_id": None, "reference_scope": "none"})

        if question_type in {"general_status", "cause_analysis", "approval_process"}:
            updates["intent"] = "ask"

        if question_type == "evidence_followup":
            updates["intent"] = "ask"
            if evidence_id is None and reference_scope in {"latest_discovered", "conversation_context"}:
                evidence_id = self._latest_discovered_evidence_id(session)
                updates["evidence_id"] = evidence_id
            if evidence_id is not None and reference_scope == "none":
                updates["reference_scope"] = "explicit"

        effective_intent = str(updates.get("intent", candidate.intent))

        if effective_intent == "request_evidence" and evidence_id is None:
            evidence_id = DEFAULT_EVIDENCE_BY_SOURCE_NPC.get(candidate.target_npc_id or "")
            if evidence_id is not None:
                updates["evidence_id"] = evidence_id
                if reference_scope == "none":
                    updates["reference_scope"] = "conversation_context"

        if effective_intent == "show_evidence" and evidence_id is None:
            evidence_id = self._latest_discovered_evidence_id(session)
            updates["evidence_id"] = evidence_id
            if evidence_id is not None and reference_scope == "none":
                updates["reference_scope"] = "latest_discovered"

        effective_evidence = updates.get("evidence_id", evidence_id)
        updates["referenced_evidence_ids"] = (
            [] if question_type == "responsibility_routing" else
            list(dict.fromkeys([*candidate.referenced_evidence_ids, *([effective_evidence] if effective_evidence else [])]))
        )
        return candidate.model_copy(update=updates)

    def _intent_recent_events(self, session: GameSession) -> tuple[str, ...]:
        events = []
        for event in session.events[-8:]:
            evidence_id = self._evidence_id_from_event(session, event)
            if event.event_type == "evidence" and evidence_id is not None:
                evidence = session.evidences[evidence_id]
                events.append(
                    f"TURN {event.turn} · {event.actor}: discovered_evidence={evidence.id} title={evidence.title}"
                )
            else:
                events.append(f"TURN {event.turn} · {event.actor}: {event.message}")
        return tuple(events)

    def _latest_discovered_evidence_id(self, session: GameSession) -> str | None:
        return latest_evidence_id(session)

    def _evidence_id_from_event(self, session: GameSession, event: EventLogEntry) -> str | None:
        return evidence_id_from_event(session, event)

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
        evidence_valid = (candidate.evidence_id is None or candidate.evidence_id in session.evidences) and all(
            evidence_id in session.evidences for evidence_id in candidate.referenced_evidence_ids
        )
        location_valid = candidate.location is None or candidate.location in context.available_locations
        player_has_evidence = (
            candidate.intent != "show_evidence"
            or (
                candidate.evidence_id in session.discovered_evidence
                if candidate.evidence_id is not None
                else bool(session.discovered_evidence)
            )
        )
        followup_evidence_owned = (
            candidate.question_type != "evidence_followup"
            or (bool(candidate.referenced_evidence_ids) and set(candidate.referenced_evidence_ids).issubset(session.discovered_evidence))
        )
        if target_valid and evidence_valid and location_valid and player_has_evidence and followup_evidence_owned:
            return candidate, False

        failed_checks = []
        if not target_valid:
            failed_checks.append("target_exists")
        if not evidence_valid:
            failed_checks.append("evidence_exists")
        if not location_valid:
            failed_checks.append("location_exists")
        if not player_has_evidence:
            failed_checks.append("player_evidence_ownership")
        if not followup_evidence_owned:
            failed_checks.append("followup_evidence_discovered")
        self._record_fallback(
            session,
            stage="intent_guardrail",
            provider=self.intent_provider.name,
            reason=f"Intent guardrail rejected: {', '.join(failed_checks)}.",
        )
        fallback = self._resolve_intent_references(session, self.intent_fallback_provider.classify(context))
        return fallback, True

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
            "talk": lambda: self._talk_npc(session, target_id, text, intent),
            "ask": lambda: self._ask_npc(session, target_id, text, intent),
            "accuse": lambda: self._accuse_npc(session, target_id, text),
            "defend": lambda: self._defend_npc(session, target_id, text),
            "order": lambda: self._handle_order(session),
            "inspect": lambda: self._inspect_evidence(session, text, intent.evidence_id),
            "show_evidence": lambda: self._show_evidence(session, target_id, intent.evidence_id),
            "request_evidence": lambda: self._request_evidence(session, target_id, intent.evidence_id, text),
            "move": lambda: self._handle_move(session, intent.location),
            "summon_meeting": lambda: self._handle_summon_meeting(session),
            "report_conclusion": lambda: self._handle_report_prompt(session),
            "social_action": lambda: self._handle_social_action(session, target_id, text),
        }

    def _handle_social_action(self, session: GameSession, target_id: str | None, text: str) -> str:
        context = self._social_impact_context(session, text, target_id)
        requested_classification: SocialImpactClassification | None = None
        try:
            classification = self.social_impact_provider.classify_social_impact(context)
            provider_fallback = False
        except ProviderError as exc:
            self._record_fallback(
                session,
                stage="social_impact_provider",
                provider=self.social_impact_provider.name,
                reason=str(exc),
            )
            classification = self.social_impact_fallback_provider.classify_social_impact(context)
            provider_fallback = True

        if classification.action_family in GAME_ACTION_FAMILIES:
            session.blocked_action_alert = GAME_ACTION_ALERT
            return GAME_ACTION_ALERT

        guardrails = self._validate_social_classification(session, context, classification)
        fallback_used = provider_fallback
        if any(not check.passed for check in guardrails):
            requested_classification = classification
            failed_checks = ", ".join(check.name for check in guardrails if not check.passed)
            self._record_fallback(
                session,
                stage="social_impact_guardrail",
                provider=self.social_impact_provider.name,
                reason=f"Social impact guardrail rejected: {failed_checks}",
            )
            classification = self.social_impact_fallback_provider.classify_social_impact(context)
            fallback_checks = self._validate_social_classification(session, context, classification)
            fallback_valid = all(check.passed for check in fallback_checks)
            guardrails.extend(
                [
                    GuardrailCheck(
                        name="fallback_classification_valid",
                        passed=fallback_valid,
                        detail="Deterministic fallback classification stays inside the current world state.",
                    )
                ]
            )
            fallback_used = True
            if not fallback_valid:
                outcome = SocialPolicyOutcome(conduct_level="inappropriate")
                return self._record_social_trace_and_message(
                    session,
                    text,
                    classification,
                    requested_classification,
                    outcome,
                    guardrails,
                    fallback_used,
                    "행동 대상, 물건 또는 관계 회복 조건을 충족하지 않아 관계 변화를 적용하지 않았습니다.",
                )

        direct_target_ids = list(dict.fromkeys(classification.direct_target_ids))
        affected_target_ids = [
            npc_id
            for npc_id in dict.fromkeys(classification.affected_target_ids)
            if npc_id not in direct_target_ids
        ]
        classification = classification.model_copy(
            update={
                "direct_target_ids": direct_target_ids,
                "affected_target_ids": affected_target_ids,
            }
        )
        object_owner_id = (
            session.world_objects[classification.object_id].owner_id
            if classification.object_id in session.world_objects
            else None
        )
        witness_ids = self._derive_witnesses(
            session,
            classification,
            direct_target_ids,
            affected_target_ids,
            object_owner_id,
        )
        repeated = self._is_repeated_social_action(session, classification)
        outcome = self.relationship_policy.evaluate(
            classification,
            actor_id="player",
            direct_target_ids=direct_target_ids,
            affected_target_ids=affected_target_ids,
            object_owner_id=object_owner_id,
            witness_ids=witness_ids,
            repeated=repeated,
            power_abuse="power_abuse" in classification.reason_codes,
            turn=session.turn,
        )
        outcome_checks = self._validate_social_outcome(session, classification, outcome)
        guardrails.extend(outcome_checks)
        if any(not check.passed for check in outcome_checks):
            failed_checks = ", ".join(check.name for check in outcome_checks if not check.passed)
            self._record_fallback(
                session,
                stage="social_impact_guardrail",
                provider=self.social_impact_provider.name,
                reason=f"Relationship policy guardrail rejected: {failed_checks}",
            )
            outcome = SocialPolicyOutcome(conduct_level="inappropriate")
            fallback_used = True
        else:
            self._apply_social_outcome(session, classification, outcome)

        return self._record_social_trace_and_message(
            session,
            text,
            classification,
            requested_classification,
            outcome,
            guardrails,
            fallback_used,
            self._social_action_message(session, classification, outcome),
        )

    def _social_impact_context(
        self,
        session: GameSession,
        text: str,
        target_hint: str | None,
    ) -> SocialImpactContext:
        available_npc_ids = self._npc_ids_at_location(session)
        available_objects = [
            world_object
            for world_object in session.world_objects.values()
            if world_object.location == session.current_location or world_object.holder_id == "player"
        ]
        return SocialImpactContext(
            player_input=text,
            current_location=session.current_location,
            target_hint=target_hint,
            available_npcs=tuple(
                f"{npc_id}: {session.npcs[npc_id].name} ({session.npcs[npc_id].role})"
                for npc_id in available_npc_ids
            ),
            available_npc_ids=tuple(available_npc_ids),
            available_objects=tuple(
                f"{item.id}: {item.name} (owner={item.owner_id or 'shared'}, condition={item.condition})"
                for item in available_objects
            ),
            available_object_ids=tuple(item.id for item in available_objects),
            recent_social_events=tuple(
                f"{event.classification.action_family}: {','.join(event.classification.direct_target_ids)}"
                for event in session.social_events[-3:]
            ),
        )

    def _npc_ids_at_location(self, session: GameSession, location: str | None = None) -> list[str]:
        resolved_location = location or session.current_location
        if resolved_location == "meeting_room":
            return list(session.npcs)
        return [
            npc_id
            for npc_id in session.npcs
            if NPC_HOME_LOCATIONS.get(npc_id) == resolved_location
        ]

    def _validate_social_classification(
        self,
        session: GameSession,
        context: SocialImpactContext,
        classification: SocialImpactClassification,
    ) -> list[GuardrailCheck]:
        target_ids = [*classification.direct_target_ids, *classification.affected_target_ids]
        target_set = set(target_ids)
        accessible_targets = set(context.available_npc_ids)
        object_state = session.world_objects.get(classification.object_id or "")
        object_accessible = classification.object_id is None or classification.object_id in context.available_object_ids
        property_action = classification.action_family in {"property_interference", "property_aggression"}
        object_action_possible = True
        if property_action and object_state is not None:
            object_action_possible = object_state.condition != "destroyed"
            if classification.action_family == "property_interference":
                object_action_possible = object_action_possible and object_state.portable
            else:
                object_action_possible = object_action_possible and object_state.destructible
        severity_min, severity_max = SEVERITY_RANGES[classification.action_family]
        recovery_valid = self._recovery_transition_valid(session, classification)
        return [
            GuardrailCheck(
                name="action_family_allowed",
                passed=classification.action_family in BASE_RELATIONSHIP_IMPACTS,
                detail="Action family exists in the server-owned social policy vocabulary.",
            ),
            GuardrailCheck(
                name="severity_valid_for_action",
                passed=severity_min <= classification.severity <= severity_max,
                detail=f"Severity is inside the policy range {severity_min}..{severity_max}.",
            ),
            GuardrailCheck(
                name="targets_exist",
                passed=bool(classification.direct_target_ids) and all(npc_id in session.npcs for npc_id in target_set),
                detail="Social action has at least one direct target and all targets exist.",
            ),
            GuardrailCheck(
                name="targets_accessible",
                passed=target_set.issubset(accessible_targets),
                detail="All social-action targets are present at the player's current location.",
            ),
            GuardrailCheck(
                name="object_exists",
                passed=classification.object_id is None or object_state is not None,
                detail="Referenced object exists in the server-owned World Object Registry.",
            ),
            GuardrailCheck(
                name="object_accessible",
                passed=object_accessible,
                detail="Referenced object is at the current location or held by the player.",
            ),
            GuardrailCheck(
                name="object_required_for_action",
                passed=not property_action or object_state is not None,
                detail="Property actions reference a concrete world object.",
            ),
            GuardrailCheck(
                name="object_action_possible",
                passed=object_action_possible,
                detail="Object state supports the requested interaction.",
            ),
            GuardrailCheck(
                name="relationship_edges_exist",
                passed=all(relationship_key(npc_id, "player") in session.relationships for npc_id in target_set),
                detail="Every affected NPC has a directional relationship toward the player.",
            ),
            GuardrailCheck(
                name="recovery_stage_transition_valid",
                passed=recovery_valid,
                detail="Apology, repair, and mediation follow the server-owned recovery sequence.",
            ),
        ]

    def _recovery_transition_valid(
        self,
        session: GameSession,
        classification: SocialImpactClassification,
    ) -> bool:
        if classification.action_family not in RECOVERY_ACTION_FAMILIES:
            return True
        relationships = [
            session.relationships[relationship_key(npc_id, "player")]
            for npc_id in classification.direct_target_ids
            if relationship_key(npc_id, "player") in session.relationships
        ]
        if not relationships:
            return False
        if classification.action_family == "apology":
            return all(edge.repair_stage in {"none", "acknowledged", "apologized"} for edge in relationships)
        if classification.action_family == "repair_action":
            return all(edge.repair_stage == "apologized" for edge in relationships)
        return all(edge.repair_stage == "repaired" for edge in relationships)

    def _derive_witnesses(
        self,
        session: GameSession,
        classification: SocialImpactClassification,
        direct_target_ids: list[str],
        affected_target_ids: list[str],
        object_owner_id: str | None,
        location: str | None = None,
    ) -> list[str]:
        if not classification.observable:
            return []
        participants = {*direct_target_ids, *affected_target_ids}
        if object_owner_id:
            participants.add(object_owner_id)
        return [npc_id for npc_id in self._npc_ids_at_location(session, location) if npc_id not in participants]

    def _is_repeated_social_action(
        self,
        session: GameSession,
        classification: SocialImpactClassification,
    ) -> bool:
        if not session.social_events:
            return False
        previous = session.social_events[-1].classification
        return (
            previous.action_family == classification.action_family
            and bool(set(previous.direct_target_ids) & set(classification.direct_target_ids))
        )

    def _validate_social_outcome(
        self,
        session: GameSession,
        classification: SocialImpactClassification,
        outcome: SocialPolicyOutcome,
    ) -> list[GuardrailCheck]:
        harmful = classification.action_family in HARMFUL_ACTION_FAMILIES
        direction_valid = not harmful or all(
            effect.trust_delta <= 0
            and effect.tension_delta >= 0
            and effect.respect_delta <= 0
            and effect.fear_delta >= 0
            and effect.grievance_delta >= 0
            for effect in outcome.relationship_effects
        )
        delta_valid = all(
            abs(value) <= 60
            for effect in outcome.relationship_effects
            for value in (
                effect.trust_delta,
                effect.tension_delta,
                effect.respect_delta,
                effect.fear_delta,
                effect.grievance_delta,
            )
        )
        event_types = {event.event_type for event in outcome.mandatory_world_events}
        mandatory_valid = True
        if classification.action_family == "property_aggression":
            mandatory_valid = "object_damaged" in event_types
        if classification.action_family == "physical_assault":
            mandatory_valid = {"security_called", "dialogue_refused"}.issubset(event_types)
        direct_magnitude = max(
            (
                abs(effect.trust_delta)
                for effect in outcome.relationship_effects
                if "direct" in effect.reason_codes
            ),
            default=0,
        )
        witness_bounded = all(
            abs(effect.trust_delta) <= direct_magnitude
            for effect in outcome.relationship_effects
            if "witness" in effect.reason_codes
        )
        return [
            GuardrailCheck(
                name="policy_entities_valid",
                passed=all(
                    effect.source_id in session.npcs
                    and effect.source_id != effect.target_id
                    and relationship_key(effect.source_id, effect.target_id) in session.relationships
                    for effect in outcome.relationship_effects
                ) and all(effect.npc_id in session.npcs for effect in [*outcome.emotion_effects, *outcome.memory_effects]),
                detail="Policy effects reference actual NPCs and existing non-self edges.",
            ),
            GuardrailCheck(
                name="policy_direction_valid",
                passed=direction_valid,
                detail="Harmful actions cannot improve trust/respect or reduce tension/fear/grievance.",
            ),
            GuardrailCheck(
                name="policy_delta_within_envelope",
                passed=delta_valid,
                detail="Relationship deltas stay inside the server-owned per-event envelope.",
            ),
            GuardrailCheck(
                name="mandatory_consequences_present",
                passed=mandatory_valid,
                detail="Severe actions include their mandatory world-state consequences.",
            ),
            GuardrailCheck(
                name="witness_impact_bounded",
                passed=witness_bounded,
                detail="Witness impact does not exceed direct-target impact.",
            ),
        ]

    def _apply_social_outcome(
        self,
        session: GameSession,
        classification: SocialImpactClassification,
        outcome: SocialPolicyOutcome,
    ) -> None:
        harmful = classification.action_family in HARMFUL_ACTION_FAMILIES
        for effect in outcome.relationship_effects:
            edge_id = relationship_key(effect.source_id, effect.target_id)
            edge = session.relationships[edge_id]
            repair_stage = edge.repair_stage
            trust_ceiling = edge.trust_ceiling
            fear_floor = edge.fear_floor
            direct_or_owner = "direct" in effect.reason_codes or "owner" in effect.reason_codes
            if harmful and classification.severity >= 4 and direct_or_owner:
                repair_stage = "none"
                trust_ceiling = 20
                fear_floor = max(20, fear_floor)
            elif classification.action_family == "apology":
                repair_stage = "apologized"
            elif classification.action_family == "repair_action":
                repair_stage = "repaired"
            elif classification.action_family == "mediation":
                repair_stage = "mediated"
                trust_ceiling = None
                fear_floor = 0

            change_relationship(
                session, effect.source_id, effect.target_id,
                trust_delta=effect.trust_delta, tension_delta=effect.tension_delta,
                respect_delta=effect.respect_delta, fear_delta=effect.fear_delta,
                grievance_delta=effect.grievance_delta,
                policy_updates={"repair_stage": repair_stage, "trust_ceiling": trust_ceiling, "fear_floor": fear_floor},
            )

        for effect in outcome.emotion_effects:
            npc = session.npcs[effect.npc_id]
            npc.dynamic_state = npc.dynamic_state.model_copy(
                update={
                    "emotion": effect.emotion,
                    "stress": max(0, min(100, npc.dynamic_state.stress + effect.stress_delta)),
                    "cooperation": max(0, min(100, npc.dynamic_state.cooperation + effect.cooperation_delta)),
                }
            )

        for memory_effect in outcome.memory_effects:
            npc = session.npcs[memory_effect.npc_id]
            duplicate = any(
                memory.summary.casefold() == memory_effect.memory.summary.casefold()
                for memory in (*npc.recent_memories, *npc.important_memories)
            )
            if not duplicate:
                npc.recent_memories.append(memory_effect.memory)
                if memory_effect.memory.importance >= 0.75:
                    npc.important_memories.append(memory_effect.memory)
            npc.recent_memories = npc.recent_memories[-8:]
            npc.important_memories = npc.important_memories[-8:]

        direct_targets = set(classification.direct_target_ids)
        for world_event in outcome.mandatory_world_events:
            if world_event.event_type == "object_damaged" and world_event.target_id in session.world_objects:
                world_object = session.world_objects[world_event.target_id]
                next_condition = "destroyed" if world_object.condition == "damaged" else "damaged"
                session.world_objects[world_event.target_id] = world_object.model_copy(
                    update={"condition": next_condition, "holder_id": None}
                )
            elif world_event.event_type == "security_called":
                session.incident_status = "SECURITY_ESCALATED"
            elif world_event.event_type == "hr_escalated" and session.incident_status != "SECURITY_ESCALATED":
                session.incident_status = "HR_ESCALATED"
            elif world_event.event_type == "dialogue_refused":
                session.dialogue_refused_npc_ids.update(direct_targets)
            self._append_event(session, "POLICY ENGINE", world_event.detail, "policy")

        if classification.action_family == "mediation":
            session.dialogue_refused_npc_ids.difference_update(direct_targets)

    def _record_social_trace_and_message(
        self,
        session: GameSession,
        text: str,
        classification: SocialImpactClassification,
        requested_classification: SocialImpactClassification | None,
        outcome: SocialPolicyOutcome,
        guardrails: list[GuardrailCheck],
        fallback_used: bool,
        message: str,
    ) -> str:
        session.social_events.append(
            SocialEventTrace(
                id=len(session.social_events) + 1,
                turn=session.turn,
                provider=self.social_impact_provider.name,
                player_input=text,
                classification=classification,
                requested_classification=requested_classification,
                policy_outcome=outcome,
                guardrails=guardrails,
                fallback_used=fallback_used,
            )
        )
        self._append_event(session, "POLICY ENGINE", message, "policy")
        if outcome.relationship_effects:
            for target_id in classification.direct_target_ids:
                if target_id in session.npcs:
                    npc = session.npcs[target_id]
                    self._append_event(
                        session,
                        npc.name,
                        self._social_reaction_message(classification, npc),
                        "dialogue",
                        npc.id,
                    )
        logger.info(
            "relationship_policy_applied turn=%s family=%s severity=%s targets=%s fallback=%s",
            session.turn,
            classification.action_family,
            classification.severity,
            ",".join(classification.direct_target_ids),
            fallback_used,
        )
        return message

    def _social_reaction_message(self, classification: SocialImpactClassification, npc: NPCState) -> str:
        reactions = {
            "verbal_pressure": "그런 식으로 윽박지르면 정상적으로 협력하기 어렵습니다. 차분하게 말씀해 주세요.",
            "insult": "업무 문제와 인신공격은 구분해 주세요. 그런 표현은 받아들일 수 없습니다.",
            "public_humiliation": "공개적으로 망신을 주는 방식의 대화에는 응하지 않겠습니다.",
            "threat": "위협으로 느껴집니다. 이 상황은 공식 절차를 통해 보고하겠습니다.",
            "property_interference": "제 물건을 허락 없이 가져가지 마세요. 즉시 돌려주세요.",
            "property_aggression": "제 물건을 빼앗아 던지는 행동은 용납할 수 없습니다. 이 상황을 HR에 보고하겠습니다.",
            "physical_intimidation": "물리적인 위협을 느꼈습니다. 지금은 대화를 계속할 수 없습니다.",
            "physical_assault": "대화를 즉시 중단하겠습니다. Security의 도움을 요청합니다.",
            "sabotage": "업무를 방해하는 행동을 중단하고 손상된 내용을 복구해 주세요.",
            "deception": "사실을 숨기거나 왜곡한 상태에서는 신뢰하기 어렵습니다.",
            "support": "상황을 공정하게 봐주셔서 감사합니다. 필요한 내용을 협조하겠습니다.",
            "apology": "사과는 들었습니다. 하지만 관계가 회복되려면 실제 피해 복구가 필요합니다.",
            "repair_action": "피해 복구를 확인했습니다. 다음 단계로 공식적인 중재가 필요합니다.",
            "mediation": "중재 내용을 수용하겠습니다. 앞으로는 정해진 절차로 대화하겠습니다.",
            "evidence_based_confrontation": "제시한 근거를 기준으로 질문에 답하겠습니다.",
            "constructive_dialogue": "차분하게 이야기해 주시면 제가 아는 범위에서 협조하겠습니다.",
        }
        return reactions.get(classification.action_family, f"{npc.name}은 이 행동에 대한 입장을 정리하고 있습니다.")

    def _social_action_message(
        self,
        session: GameSession,
        classification: SocialImpactClassification,
        outcome: SocialPolicyOutcome,
    ) -> str:
        target_names = ", ".join(
            session.npcs[npc_id].name
            for npc_id in classification.direct_target_ids
            if npc_id in session.npcs
        ) or "대상"
        witness_phrase = (
            " 및 목격자"
            if any("witness" in effect.reason_codes for effect in outcome.relationship_effects)
            else ""
        )
        messages = {
            "verbal_pressure": f"{target_names}에게 강압적 언행이 가해져 긴장과 불만이 증가했습니다.",
            "insult": f"{target_names}이 모욕을 받아 신뢰와 존중이 하락했습니다.",
            "public_humiliation": f"{target_names}에 대한 공개 망신이 관계와 팀 분위기를 훼손했습니다.",
            "threat": f"{target_names}이 위협을 느꼈으며 사건이 공식적으로 escalated 됐습니다.",
            "property_interference": f"{target_names}의 물건에 대한 침해가 관계에 반영됐습니다.",
            "property_aggression": f"물건을 이용한 공격적 행동으로 {target_names}{witness_phrase}의 관계가 악화됐습니다.",
            "physical_intimidation": f"{target_names}이 물리적 위협을 느껴 정상적인 협력이 어려워졌습니다.",
            "physical_assault": f"{target_names}에 대한 신체 공격으로 Security가 호출되고 정상 대화가 중단됐습니다.",
            "apology": f"{target_names}에게 사과했습니다. 관계는 일부만 회복되며 피해 복구가 필요합니다.",
            "repair_action": f"{target_names}에 대한 피해 복구가 반영됐습니다. 중재 전까지 관계 제한은 유지됩니다.",
            "mediation": f"{target_names}과의 중재가 완료되어 관계 회복 제한이 해제됐습니다.",
            "support": f"{target_names}을 지지해 신뢰와 협력이 개선됐습니다.",
            "evidence_based_confrontation": f"{target_names}에게 근거를 바탕으로 책임을 물어 긴장은 올랐지만 존중은 유지됐습니다.",
            "constructive_dialogue": f"{target_names}과 건설적으로 대화해 관계가 소폭 개선됐습니다.",
        }
        return messages.get(
            classification.action_family,
            f"{classification.action_family} 행동의 관계 정책 결과가 적용됐습니다 ({outcome.conduct_level}).",
        )

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
        evidence_id = evidence_id or "release_timeline"
        self._discover_evidence(session, evidence_id)
        evidence = session.evidences[evidence_id]
        self._append_event(
            session,
            "System",
            f"증거를 확보했습니다. {evidence.title}를 공개했습니다.\n{evidence.content}",
            "evidence", evidence_id=evidence.id, evidence_operation="discovered",
        )
        return evidence.content

    def _show_evidence(self, session: GameSession, target_id: str | None, evidence_id: str | None = None) -> str:
        evidence_id = evidence_id or next(iter(session.discovered_evidence), None)
        if evidence_id is None or evidence_id not in session.discovered_evidence:
            logger.warning(
                "show_evidence_rejected session_id=%s turn=%s evidence_id=%s reason=player_does_not_possess_evidence",
                session.session_id,
                session.turn,
                evidence_id,
            )
            message = "Player가 아직 확보하지 않은 증거이므로 NPC에게 제시할 수 없습니다. 먼저 해당 증거를 요청하거나 조사하세요."
            self._append_event(session, "System", message, "guardrail")
            return message
        evidence = session.evidences[evidence_id]
        target = target_id or "qa_01"
        npc = session.npcs.get(target)
        if npc is None:
            return evidence.content

        blocked = self._check_npc_response(session, npc)
        if blocked:
            return blocked
        presentation_count = self._evidence_presentation_count(session, target, evidence.id)
        policy = self._evidence_presentation_policy(session, npc, evidence, presentation_count)
        observe_evidence(session, npc, evidence.id)
        self._append_event(session, "Player", f"{npc.name}에게 {evidence.title}를 제시했습니다.", "evidence", target,
                           evidence_id=evidence.id, recipient_npc_id=target, evidence_operation="presented")

        policy_context = (
            "Evidence presentation reaction.\n"
            f"reaction_policy={policy['reaction_type']}\n"
            f"evidence_id={evidence.id}\n"
            f"evidence_title={evidence.title}\n"
            f"evidence_content={evidence.content}\n"
            "Respond to the evidence without inventing facts or changing the supplied evidence."
        )
        decision, provider_fallback = self._request_decision(session, npc, "show_evidence", policy_context)
        safe_decision = decision.model_copy(
            update={
                "npc_id": npc.id,
                "emotion": policy["emotion"],
                "stress_delta": policy["stress_delta"],
                "trust_delta": policy["trust_delta"],
                "cooperation_delta": policy["cooperation_delta"],
                "belief_updates": policy["belief_updates"],
                "relationship_updates": [],
                "grounding_type": "acknowledgement",
                "knowledge_refs": [],
                "memory_candidate": policy["memory_candidate"],
                "action_type": "show_evidence",
                "action_target": evidence.id,
                "dialogue": decision.dialogue.strip() or policy["fallback_dialogue"],
            }
        )
        applied_decision = self._apply_decision(
            session,
            npc,
            safe_decision,
            f"Player presented {evidence.title} to {npc.name}.",
            provider_fallback,
        )
        response_message = f"{evidence.content}\n증거를 제시했습니다. {applied_decision.dialogue}"
        self._append_event(session, npc.name, response_message, "evidence", target,
                           evidence_id=evidence.id, recipient_npc_id=target, evidence_operation="response")
        return response_message

    def _evidence_presentation_count(self, session: GameSession, target_id: str, evidence_id: str) -> int:
        return presentation_count(session, target_id, evidence_id)

    def _evidence_presentation_policy(
        self,
        session: GameSession,
        npc: NPCState,
        evidence: Evidence,
        presentation_count: int,
    ) -> dict[str, object]:
        if presentation_count > 0:
            return {
                "reaction_type": "repeated_presentation",
                "emotion": npc.dynamic_state.emotion,
                "stress_delta": 0,
                "trust_delta": 0,
                "cooperation_delta": 0,
                "belief_updates": [],
                "memory_candidate": None,
                "fallback_dialogue": "이 증거는 이미 확인했습니다. 같은 내용을 다시 제시해도 판단은 달라지지 않습니다.",
            }

        if evidence.source_npc_id == npc.id:
            return {
                "reaction_type": "same_source_acknowledgement",
                "emotion": npc.dynamic_state.emotion,
                "stress_delta": 0,
                "trust_delta": 0,
                "cooperation_delta": 1,
                "belief_updates": [],
                "memory_candidate": Memory(
                    summary=f"Player asked {npc.name} to confirm the evidence they provided.",
                    importance=0.55,
                    turn=session.turn,
                ),
                "fallback_dialogue": "이 메시지는 제가 보낸 경고입니다. 이미 알고 있는 내용이니, 어떻게 처리됐는지 확인해 주세요.",
            }

        if npc.id == "backend_01" and evidence.id == "qa_warning_message":
            belief = Belief(
                subject="incident",
                belief="The ignored QA warning and API schema change jointly enabled the outage.",
                confidence=0.85,
            )
            return {
                "reaction_type": "accountability_pressure",
                "emotion": "uneasy",
                "stress_delta": 8,
                "trust_delta": 3,
                "cooperation_delta": 1,
                "belief_updates": [belief],
                "memory_candidate": Memory(
                    summary="Player presented the QA warning message during the incident review.",
                    importance=0.7,
                    turn=session.turn,
                ),
                "fallback_dialogue": "QA 경고가 있었던 것은 확인했습니다. 제가 API 응답 스키마를 변경한 상태에서 배포를 진행했고, 당시 판단 과정을 다시 검토하겠습니다.",
            }

        if npc.id == "frontend_01":
            return {
                "reaction_type": "cross_role_review",
                "emotion": "focused",
                "stress_delta": 3,
                "trust_delta": 1,
                "cooperation_delta": 3,
                "belief_updates": [],
                "memory_candidate": Memory(
                    summary="Player presented QA evidence for cross-role API review.",
                    importance=0.65,
                    turn=session.turn,
                ),
                "fallback_dialogue": "QA 경고와 API 변경 내용을 함께 확인해 보겠습니다. 프론트엔드 반영 시점도 다시 점검하겠습니다.",
            }

        if npc.id == "pm_01":
            return {
                "reaction_type": "accountability_pressure",
                "emotion": "concerned",
                "stress_delta": 4,
                "trust_delta": 1,
                "cooperation_delta": 2,
                "belief_updates": [],
                "memory_candidate": Memory(
                    summary="Player presented QA evidence about the deployment decision.",
                    importance=0.65,
                    turn=session.turn,
                ),
                "fallback_dialogue": "배포 전에 이런 경고가 있었다면 일정과 승인 과정에서 검토했어야 합니다.",
            }

        return {
            "reaction_type": "cross_role_review",
            "emotion": "uneasy",
            "stress_delta": 2,
            "trust_delta": 1,
            "cooperation_delta": 1,
            "belief_updates": [],
            "memory_candidate": Memory(
                summary=f"Player presented {evidence.title} during the incident review.",
                importance=0.6,
                turn=session.turn,
            ),
            "fallback_dialogue": "제시된 증거를 확인했습니다. 이 내용이 어떻게 처리됐는지 함께 확인해 보겠습니다.",
        }

    def _request_evidence(
        self,
        session: GameSession,
        target_id: str | None,
        evidence_id: str | None,
        text: str,
    ) -> str:
        npc = session.npcs.get(target_id or "")
        if npc is not None:
            blocked = self._check_npc_response(session, npc)
            if blocked:
                return blocked
        evidence_id = evidence_id or DEFAULT_EVIDENCE_BY_SOURCE_NPC.get(target_id or "") or "qa_warning_message"
        evidence = session.evidences[evidence_id]
        npc = npc or session.npcs.get(evidence.source_npc_id or "")
        if npc is None or not can_provide_evidence(session, npc, evidence_id):
            message = "이 NPC가 제공할 수 없는 증거입니다. 해당 자료의 제공자에게 요청하거나 직접 조사하세요."
            self._append_event(session, "System", message, "guardrail")
            return message
        blocked = self._check_npc_response(session, npc)
        if blocked:
            return blocked
        self._discover_evidence(session, evidence_id)
        evidence = session.evidences[evidence_id]
        actor = npc.name
        self._append_event(
            session,
            actor,
            f"증거를 확보했습니다. {evidence.title}를 공개했습니다.\n{evidence.content}",
            "evidence",
            npc.id, evidence_id=evidence.id, recipient_npc_id=npc.id, evidence_operation="discovered",
        )
        return evidence.content

    def _talk_npc(
        self,
        session: GameSession,
        target_id: str | None,
        player_input: str = "",
        intent: IntentClassification | None = None,
    ) -> str:
        target_id = target_id or "qa_01"
        npc = session.npcs.get(target_id)
        if npc is None:
            self._append_event(session, "System", "대화할 NPC를 찾지 못했습니다.", "guardrail")
            return "대화할 NPC를 찾지 못했습니다."
        blocked = self._check_npc_response(session, npc)
        if blocked:
            return blocked
        decision, provider_fallback = self._request_decision(session, npc, "talk", player_input, intent)
        applied_decision = self._apply_decision(
            session,
            npc,
            decision,
            f"Player talked to {npc.name}.",
            provider_fallback,
        )
        self._append_event(session, npc.name, applied_decision.dialogue, "dialogue", npc.id)
        return applied_decision.dialogue

    def _ask_npc(
        self,
        session: GameSession,
        target_id: str | None,
        player_input: str = "",
        intent: IntentClassification | None = None,
    ) -> str:
        target_id = target_id or "qa_01"
        npc = session.npcs.get(target_id)
        if npc is None:
            self._append_event(session, "System", "질문할 NPC를 찾지 못했습니다.", "guardrail")
            return "질문할 NPC를 찾지 못했습니다."
        blocked = self._check_npc_response(session, npc)
        if blocked:
            return blocked
        decision, provider_fallback = self._request_decision(session, npc, "ask", player_input, intent)
        applied_decision = self._apply_decision(
            session,
            npc,
            decision,
            f"Player asked {npc.name} about the incident.",
            provider_fallback,
        )
        self._append_event(session, npc.name, applied_decision.dialogue, "dialogue", npc.id)
        return applied_decision.dialogue

    def _accuse_npc(self, session: GameSession, target_id: str | None, player_input: str = "") -> str:
        target_id = target_id or "qa_01"
        npc = session.npcs.get(target_id)
        if npc is None:
            self._append_event(session, "System", "책임을 물을 NPC를 찾지 못했습니다.", "guardrail")
            return "책임을 물을 NPC를 찾지 못했습니다."
        blocked = self._check_npc_response(session, npc)
        if blocked:
            return blocked
        decision, provider_fallback = self._request_decision(session, npc, "accuse", player_input)
        applied_decision = self._apply_decision(
            session,
            npc,
            decision,
            f"Player accused {npc.name}.",
            provider_fallback,
        )
        self._append_event(session, npc.name, applied_decision.dialogue, "dialogue", npc.id)
        if applied_decision.action_type == "show_evidence" and applied_decision.action_target:
            self._discover_evidence(session, applied_decision.action_target)
            evidence = session.evidences.get(applied_decision.action_target)
            if evidence is not None:
                self._append_event(session, npc.name, f"{evidence.title}를 공개했습니다.", "evidence", npc.id,
                                   evidence_id=evidence.id, evidence_operation="discovered")
        return applied_decision.dialogue

    def _defend_npc(self, session: GameSession, target_id: str | None, player_input: str = "") -> str:
        target_id = target_id or "qa_01"
        npc = session.npcs.get(target_id)
        if npc is None:
            self._append_event(session, "System", "옹호할 NPC를 찾지 못했습니다.", "guardrail")
            return "옹호할 NPC를 찾지 못했습니다."
        blocked = self._check_npc_response(session, npc)
        if blocked:
            return blocked
        decision, provider_fallback = self._request_decision(session, npc, "defend", player_input)
        applied_decision = self._apply_decision(
            session,
            npc,
            decision,
            f"Player defended {npc.name}.",
            provider_fallback,
        )
        self._append_event(session, npc.name, applied_decision.dialogue, "dialogue", npc.id)
        return applied_decision.dialogue

    def _check_npc_response(self, session: GameSession, npc: NPCState) -> str | None:
        message = npc_response_block(npc, session.dialogue_refused_npc_ids)
        if message:
            self._append_event(session, npc.name, message, "policy", npc.id)
        return message

    def _request_decision(
        self,
        session: GameSession,
        npc: NPCState,
        mode: str,
        player_input: str,
        intent: IntentClassification | None = None,
    ) -> tuple[AgentDecision, bool]:
        question_type = intent.question_type if intent is not None else "none"
        reference_scope = intent.reference_scope if intent is not None else "none"
        referenced_evidence_id = intent.evidence_id if intent is not None else None
        visible_ids = visible_evidence_ids(session, npc)
        referenced_evidence = session.evidences.get(referenced_evidence_id or "") if referenced_evidence_id in visible_ids else None
        fact_ids = available_fact_ids(session, npc)
        context_npc = npc.model_copy(deep=True, update={
            "known_fact_ids": fact_ids,
            "known_facts": [FACT_REGISTRY[fact_id].statement for fact_id in fact_ids if fact_id in FACT_REGISTRY],
        })
        context = DecisionContext(
            mode=mode,
            player_input=player_input,
            turn=session.turn,
            npc=context_npc,
            target_npc_id=npc.id,
            available_facts=tuple(
                f"{fact_id}: {FACT_REGISTRY[fact_id].statement}"
                for fact_id in fact_ids
                if fact_id in FACT_REGISTRY
            ),
            available_evidence_ids=tuple(session.evidences),
            recent_events=self._decision_recent_events(session, mode, npc),
            visible_evidences=tuple(session.evidences[eid] for eid in sorted(visible_ids)),
            available_npcs=tuple(f"{item.id}: {item.name} ({item.role})" for item in session.npcs.values()),
            incident_rules=tuple(INCIDENT_RULES),
            question_type=question_type,
            reference_scope=reference_scope,
            discovered_evidence_ids=tuple(sorted(session.discovered_evidence)),
            referenced_evidence_id=referenced_evidence.id if referenced_evidence is not None else None,
            referenced_evidence_title=referenced_evidence.title if referenced_evidence is not None else None,
            referenced_evidence_summary=referenced_evidence.summary if referenced_evidence is not None else None,
            referenced_evidence_content=referenced_evidence.content if referenced_evidence is not None else None,
            responsibility_map=tuple(
                f"{fact_id}: {FACT_REGISTRY[fact_id].statement}"
                for fact_id in RESPONSIBILITY_FACT_IDS
            ),
        )
        try:
            decision = self.provider.decide(context)
            allowed_evidence_ids = visible_ids
            if mode in {"talk", "ask"} and self._contains_evidence_leak(
                session,
                decision.dialogue,
                allowed_evidence_ids,
            ):
                self._record_fallback(
                    session,
                    stage="decision_disclosure_guardrail",
                    provider=self.provider.name,
                    reason="Normal dialogue attempted to disclose protected evidence content.",
                )
                return self.fallback_provider.decide(context), True
            if question_type == "responsibility_routing" and not any(
                fact_id in RESPONSIBILITY_FACT_IDS for fact_id in decision.knowledge_refs
            ):
                self._record_fallback(
                    session,
                    stage="decision_guardrail",
                    provider=self.provider.name,
                    reason="Responsibility answer omitted server-owned responsibility facts.",
                )
                return self.fallback_provider.decide(context), True
            if any(npc_id not in session.npcs for npc_id in decision.contact_npc_ids):
                self._record_fallback(
                    session,
                    stage="decision_unavailable_role_guardrail",
                    provider=self.provider.name,
                    reason="Dialogue directed the player to a role that is not an available NPC.",
                )
                return self.fallback_provider.decide(context), True
            if mode == "show_evidence" and self._contains_known_fact_contradiction(npc, decision.dialogue):
                self._record_fallback(
                    session,
                    stage="decision_fact_consistency_guardrail",
                    provider=self.provider.name,
                    reason="Evidence reaction contradicted a canonical incident fact.",
                )
                return self.fallback_provider.decide(context), True
            return decision, False
        except ProviderError as exc:
            self._record_fallback(
                session,
                stage="decision_provider",
                provider=self.provider.name,
                reason=str(exc),
            )
            return self.fallback_provider.decide(context), True

    def _decision_recent_events(self, session: GameSession, mode: str, npc: NPCState) -> tuple[str, ...]:
        visible = visible_evidence_ids(session, npc)
        events = []
        for event in session.events[-16:]:
            if event.recipient_npc_id not in {None, npc.id}:
                continue
            if event.actor_id not in {None, npc.id}:
                continue
            if event.event_type == "fallback":
                continue
            if event.event_type == "evidence" and evidence_id_from_event(session, event) not in visible:
                continue
            events.append(f"TURN {event.turn} · {event.actor}: {event.message}")
        return tuple(events[-8:])

    def _contains_evidence_leak(
        self,
        session: GameSession,
        dialogue: str,
        allowed_evidence_ids: set[str] | None = None,
    ) -> bool:
        allowed_evidence_ids = allowed_evidence_ids or set()
        normalized = dialogue.casefold()
        for evidence_id, evidence in session.evidences.items():
            if evidence_id in allowed_evidence_ids:
                continue
            if evidence.content and len(evidence.content) >= 24:
                content_prefix = evidence.content[:24].casefold()
                if content_prefix in normalized:
                    return True
        return False

    def _contains_known_fact_contradiction(self, npc: NPCState, dialogue: str) -> bool:
        """Reject evidence reactions that explicitly deny canonical NPC facts."""

        normalized = dialogue.casefold()
        for fact_id in npc.known_fact_ids:
            patterns = KNOWN_FACT_CONTRADICTION_PATTERNS.get(fact_id, ())
            if any(re.search(pattern, normalized) for pattern in patterns):
                return True
        return False

    def _apply_decision(
        self,
        session: GameSession,
        npc: NPCState,
        decision: AgentDecision,
        event: str,
        provider_fallback: bool = False,
    ) -> AgentDecision:
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
        change_relationship(session, npc.id, "player", trust_delta=trace_decision.trust_delta)
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
                known_fact_ids=available_fact_ids(session, npc),
                known_facts=[FACT_REGISTRY[fid].statement for fid in available_fact_ids(session, npc) if fid in FACT_REGISTRY],
                retrieved_rules=list(INCIDENT_RULES[:1]) if npc.id == "qa_01" else [],
                decision=trace_decision,
                requested_decision=decision if rejected else None,
                guardrails=guardrails,
                fallback_used=provider_fallback or fallback_used,
            )
        )
        return trace_decision

    def _validate_decision(self, session: GameSession, npc: NPCState, decision: AgentDecision) -> list[GuardrailCheck]:
        action_targets = set(session.npcs) | {None}
        if decision.action_type == "show_evidence":
            action_targets = {eid for eid in session.evidences if can_provide_evidence(session, npc, eid)}
        elif decision.action_target in session.evidences:
            action_targets |= visible_evidence_ids(session, npc)
        belief_subjects = set(session.npcs) | {"player", "incident"}
        return [
            GuardrailCheck(name="evidence_refs_visible", passed=set(decision.evidence_refs).issubset(visible_evidence_ids(session, npc)),
                           detail="Evidence claims only cite documents visible to this NPC and player."),
            GuardrailCheck(name="contact_npcs_available", passed=all(target in session.npcs for target in decision.contact_npc_ids),
                           detail="Suggested contacts are actual available NPCs."),
            GuardrailCheck(
                name="npc_exists",
                passed=decision.npc_id == npc.id and npc.id in session.npcs,
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
                passed=all(
                    update.target_npc_id in session.npcs
                    and update.target_npc_id != npc.id
                    and relationship_key(npc.id, update.target_npc_id) in session.relationships
                    for update in decision.relationship_updates
                ) and len({update.target_npc_id for update in decision.relationship_updates}) == len(decision.relationship_updates),
                detail="Relationship updates reference NPCs in the current session.",
            ),
            GuardrailCheck(
                name="knowledge_refs_exist",
                passed=all(fact_id in FACT_REGISTRY for fact_id in decision.knowledge_refs),
                detail="Knowledge references exist in the server-owned Fact Registry.",
            ),
            GuardrailCheck(
                name="knowledge_refs_present",
                passed=decision.grounding_type != "fact" or bool(decision.knowledge_refs),
                detail="Fact-grounded dialogue includes a reference; belief and acknowledgement may omit it.",
            ),
            GuardrailCheck(
                name="knowledge_refs_known_by_npc",
                passed=all(fact_id in available_fact_ids(session, npc) for fact_id in decision.knowledge_refs),
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
            grounding_type="acknowledgement",
            action_type="dialogue",
            dialogue="현재 질문에 답하기 전에 확인할 수 있는 정보부터 정리하겠습니다.",
        )

    def _bounded_dynamic_state(self, state: DynamicState, decision: AgentDecision) -> DynamicState:
        return DynamicState(
            emotion=decision.emotion,
            stress=max(0, min(100, state.stress + decision.stress_delta)),
            trust_toward_player=state.trust_toward_player,
            cooperation=max(0, min(100, state.cooperation + decision.cooperation_delta)),
        )

    def _upsert_belief(self, npc: NPCState, belief: Belief) -> None:
        for index, existing in enumerate(npc.beliefs):
            if existing.subject == belief.subject:
                npc.beliefs[index] = belief
                return
        npc.beliefs.append(belief)

    def _apply_relationship_update(self, session: GameSession, npc: NPCState, update: RelationshipUpdate) -> None:
        change_relationship(session, npc.id, update.target_npc_id,
                            trust_delta=update.trust_delta, tension_delta=update.tension_delta)

    def _discover_evidence(self, session: GameSession, evidence_id: str) -> None:
        if evidence_id in session.evidences:
            session.discovered_evidence.add(evidence_id)

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
            f"{stage} · {provider} 정상 처리를 완료하지 못해 deterministic fallback을 사용했습니다. {safe_reason}",
            "fallback",
        )

    def _append_event(self, session: GameSession, actor: str, message: str, event_type: str, actor_id: str | None = None,
                      *, evidence_id: str | None = None, recipient_npc_id: str | None = None,
                      evidence_operation: str | None = None) -> None:
        session.events.append(
            EventLogEntry(
                id=len(session.events) + 1,
                turn=session.turn,
                actor=actor,
                actor_id=actor_id,
                message=message,
                event_type=event_type,
                evidence_id=evidence_id, recipient_npc_id=recipient_npc_id, evidence_operation=evidence_operation,
                created_at=datetime.now(UTC),
            )
        )

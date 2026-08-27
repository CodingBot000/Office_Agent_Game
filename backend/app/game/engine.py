from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Callable, get_args
from uuid import uuid4

from app.game.session import GameSession
from app.game.session_codec import CURRENT_SESSION_SCHEMA_VERSION, serialize_session, deserialize_session, migrate_session_payload
from app.game.events import append_event
from app.game.conversation import build_decision_context, validate_decision, contains_evidence_leak, contains_known_fact_contradiction
from app.game.social_state import (recovery_transition_valid, npc_ids_at_location, derive_witnesses,
                                   is_repeated_social_action, validate_social_outcome, apply_social_outcome)
from app.config import Settings, get_settings
from app.game.seed import DEFAULT_EVIDENCE_BY_SOURCE_NPC, FACT_REGISTRY, INCIDENT_RULES, RESPONSIBILITY_FACT_IDS, relationship_key
from app.game.relationship_policy import RelationshipPolicyEngine
from app.game.reporting import evaluate_report
from app.providers.base import ReportProvider
from app.providers.factory import create_report_provider
from app.game.state_transitions import change_relationship, npc_response_block
from app.game.evidence_policy import available_fact_ids, can_provide_evidence, observe_evidence, evidence_id_from_event, latest_evidence_id, presentation_count, evidence_presentation_policy
from app.game.action_registry import build_available_game_actions, build_player_inventory
from app.game.seed import NPC_HOME_LOCATIONS, LOCATION_LABELS
from app.game.social_rules import BASE_RELATIONSHIP_IMPACTS, GAME_ACTION_FAMILIES, SEVERITY_RANGES
from app.models import ActionResponse, ActionType, AvailableGameAction, AgentDecision, AgentTrace, Belief, DynamicState, EventLogEntry, FallbackNotice, GameSnapshot, GameActionGuardrail, GameActionRequest, GameActionResponse, GameActionTrace, GuardrailCheck, IncidentReportRequest, IntentClassification, Memory, NPCState, RelationshipUpdate, SocialEventTrace, SocialImpactClassification, SocialPolicyOutcome, WorldObjectState
from app.providers import AgentProvider, IntentContext, IntentProvider, ProviderError, SocialImpactContext, SocialImpactProvider, create_intent_provider, create_provider, create_social_impact_provider
from app.providers.deterministic import (
    DeterministicDecisionProvider,
    DeterministicIntentProvider,
    DeterministicSocialImpactProvider,
)
from app.storage import SessionRepository, create_session_repository


logger = logging.getLogger(__name__)
GAME_ACTION_ALERT = "Use the provided action buttons to perform game actions."


AVAILABLE_ACTIONS = list(get_args(ActionType))


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
        report_provider: ReportProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.session_repository = session_repository or create_session_repository(self.settings)
        self.provider = provider or create_provider(self.settings)
        self.intent_provider = intent_provider or create_intent_provider(self.settings)
        self.social_impact_provider = social_impact_provider or create_social_impact_provider(self.settings)
        self.report_provider = report_provider or create_report_provider(self.settings)
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
        migrated_payload, migrated = migrate_session_payload(payload)
        session = deserialize_session(migrated_payload)
        if migrated:
            self._save_session(session)
        return session

    def reset_session(self, session_id: str) -> GameSnapshot:
        previous = self.get_session(session_id)
        replacement = GameSession(session_id=str(uuid4()))
        self._append_event(replacement, "System", "서비스 장애 사건이 시작되었습니다. 현재 상태: ACTIVE.", "system")
        replacement.revision = self.session_repository.replace(
            session_id, previous.revision, replacement.session_id, serialize_session(replacement)
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
            self._apply_owner_policy(session, world_object, "property_interference", 3, ["property_violation"], player_input=action.label)
            message = f"{world_object.name}을(를) 손에 들었습니다."
            self._append_event(session, "Player", message, "game_action")
        elif action.family == "break_held_object":
            self._apply_owner_policy(session, world_object, "property_aggression", 4, ["property_violation", "property_damage"], player_input=action.label)
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

            self._apply_throw_policy(session, world_object, target_id, action.label)
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
        player_input: str = "",
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
            for npc_id in derive_witnesses(
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
        apply_social_outcome(session, classification, outcome)
        owner = session.npcs[world_object.owner_id]
        self._generate_social_reaction(session, classification, owner, outcome, player_input or f"{family}: {world_object.name}")

    def _apply_throw_policy(self, session: GameSession, world_object: WorldObjectState, target_id: str | None, player_input: str) -> None:
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
        witnesses = derive_witnesses(session, classification, [target_id], [], None, location=impact_location)
        outcome = self.relationship_policy.evaluate(
            classification,
            actor_id="player",
            direct_target_ids=[target_id],
            witness_ids=witnesses,
            turn=session.turn,
        )
        apply_social_outcome(session, classification, outcome)

        target = session.npcs[target_id]
        if effect != "physical_assault":
            self._generate_social_reaction(session, classification, target, outcome, player_input)


        if world_object.owner_id and world_object.owner_id in session.npcs and world_object.owner_id != target_id:
            self._apply_owner_policy(
                session,
                world_object,
                "property_aggression",
                4,
                ["property_violation", "property_damage"],
                excluded_witness_ids={target_id},
                witness_location=impact_location,
                player_input=player_input,
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

        result, extraction = evaluate_report(session, report, self.report_provider)
        session.turn += 1
        self._append_event(session, "Player", "최종 Incident Report를 제출했습니다.", "report")
        session.result = result
        session.report = report
        session.report_extraction = extraction
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
            session.session_id, serialize_session(session), expected_revision=session.revision
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
            available_locations=tuple(LOCATION_LABELS),
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
        if candidate.location not in LOCATION_LABELS:
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
            "order": lambda: self._handle_order(session, intent),
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
        witness_ids = derive_witnesses(
            session,
            classification,
            direct_target_ids,
            affected_target_ids,
            object_owner_id,
        )
        repeated = is_repeated_social_action(session, classification)
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
        outcome_checks = validate_social_outcome(session, classification, outcome)
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
            apply_social_outcome(session, classification, outcome)

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
        available_npc_ids = npc_ids_at_location(session)
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
        recovery_valid = recovery_transition_valid(session, classification)
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
                    self._generate_social_reaction(session, classification, npc, outcome, text)
        logger.info(
            "relationship_policy_applied turn=%s family=%s severity=%s targets=%s fallback=%s",
            session.turn,
            classification.action_family,
            classification.severity,
            ",".join(classification.direct_target_ids),
            fallback_used,
        )
        return message

    def _generate_social_reaction(
        self, session: GameSession, classification: SocialImpactClassification, npc: NPCState,
        outcome: SocialPolicyOutcome, player_input: str,
    ) -> None:
        if npc.physical_state == "comatose":
            return
        decision, fallback = self._request_decision(
            session, npc, "social_reaction", player_input,
            social_classification=classification, social_outcome=outcome,
        )
        # Narration cannot reapply policy effects or replace the policy-owned emotion.
        decision = decision.model_copy(update={"emotion": npc.dynamic_state.emotion})
        applied = self._apply_decision(session, npc, decision, f"Reaction to {classification.action_family}", fallback)
        self._append_event(session, npc.name, applied.dialogue, "dialogue", npc.id)

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

    def _handle_order(self, session: GameSession, intent: IntentClassification) -> str:
        if intent.command_kind != "rollback":
            message = "지원되는 명령이 명시되지 않아 실행하지 않았습니다. 현재는 명시적인 배포 중단·롤백 지시만 실행할 수 있습니다."
            self._append_event(session, "System", message, "guardrail")
            return message
        self._append_event(session, "System", "배포 중단 및 롤백을 지시했습니다.", "command")
        session.incident_status = "MITIGATING"
        return "롤백 지시가 기록되었습니다."

    def _handle_move(self, session: GameSession, location: str | None) -> str:
        session.current_location = location or "dev_area"
        self._append_event(session, "System", f"{LOCATION_LABELS[session.current_location]}로 이동했습니다.", "movement")
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
        policy = evidence_presentation_policy(session, npc, evidence, presentation_count)
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
        validation_candidate = decision.model_copy(update={"action_target": evidence.id}) if (
            decision.action_type == "show_evidence" and decision.action_target is None
        ) else decision
        failed = [check.name for check in self._validate_decision(session, npc, validation_candidate) if not check.passed]
        if failed:
            self._record_fallback(session, "decision_guardrail", self.provider.name,
                                  f"Evidence reaction rejected: {', '.join(failed)}")
            decision = self._safe_fallback(npc)
            provider_fallback = True
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
        *, social_classification: SocialImpactClassification | None = None,
        social_outcome: SocialPolicyOutcome | None = None,
    ) -> tuple[AgentDecision, bool]:
        context = build_decision_context(session, npc, mode, player_input, intent,
                                         social_classification=social_classification, social_outcome=social_outcome)
        question_type = context.question_type
        visible_ids = {evidence.id for evidence in context.visible_evidences}
        required_kind = context.required_response_kind
        try:
            decision = self.provider.decide(context)
            if mode == "social_reaction" and (
                decision.action_type != "dialogue" or decision.action_target is not None
                or any((decision.stress_delta, decision.trust_delta, decision.cooperation_delta))
                or decision.belief_updates or decision.relationship_updates or decision.memory_candidate
                or decision.response_kind != required_kind
            ):
                self._record_fallback(session, "decision_guardrail", self.provider.name,
                                      "Social narration attempted state changes or contradicted the required response kind.")
                return self.fallback_provider.decide(context), True
            allowed_evidence_ids = visible_ids
            if mode in {"talk", "ask", "social_reaction"} and contains_evidence_leak(
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
            if mode == "show_evidence" and contains_known_fact_contradiction(npc, decision.dialogue):
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
        return validate_decision(session, npc, decision)

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
        append_event(session, actor, message, event_type, actor_id, evidence_id=evidence_id,
                     recipient_npc_id=recipient_npc_id, evidence_operation=evidence_operation)

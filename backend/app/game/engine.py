from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.game.seed import CANONICAL_TRUTH, INCIDENT_RULES, clone_evidence, clone_npcs
from app.models import (
    ActionResponse,
    AgentDecision,
    AgentTrace,
    Belief,
    DynamicState,
    Evidence,
    EventLogEntry,
    GameResult,
    GameSnapshot,
    GuardrailCheck,
    IncidentReportRequest,
    Memory,
    NPCState,
)


AVAILABLE_ACTIONS = [
    "talk",
    "ask",
    "accuse",
    "defend",
    "order",
    "inspect",
    "show_evidence",
    "request_evidence",
    "move",
    "summon_meeting",
    "report_conclusion",
]


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
    evidences: dict[str, Evidence] = field(default_factory=clone_evidence)
    events: list[EventLogEntry] = field(default_factory=list)
    agent_traces: list[AgentTrace] = field(default_factory=list)
    discovered_evidence: set[str] = field(default_factory=set)
    canonical_truth: list[str] = field(default_factory=lambda: list(CANONICAL_TRUTH))
    completed: bool = False
    result: GameResult | None = None


class SessionNotFoundError(KeyError):
    pass


class GameEngine:
    """Phase 1 deterministic game loop.

    The engine owns the authoritative session state. A future LLM provider can
    produce AgentDecision candidates, but mutations still pass through here.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, GameSession] = {}

    def create_session(self) -> GameSnapshot:
        session = GameSession(session_id=str(uuid4()))
        self._sessions[session.session_id] = session
        self._append_event(session, "System", "서비스 장애 사건이 시작되었습니다. 현재 상태: ACTIVE.", "system")
        return self.snapshot(session)

    def get_session(self, session_id: str) -> GameSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFoundError(session_id) from exc

    def reset_session(self, session_id: str) -> GameSnapshot:
        self._sessions.pop(session_id, None)
        return self.create_session()

    def submit_action(self, session_id: str, text: str) -> ActionResponse:
        session = self.get_session(session_id)
        if session.completed:
            return ActionResponse(
                snapshot=self.snapshot(session),
                classified_action="completed",
                message="이미 종료된 사건입니다. 새 세션을 시작하세요.",
            )

        session.turn += 1
        action, target_id = self._classify_action(text)
        message = self._handle_action(session, action, target_id, text)
        return ActionResponse(snapshot=self.snapshot(session), classified_action=action, message=message)

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
            objective=session.objective,
            npcs=list(session.npcs.values()),
            evidences=visible_evidence,
            events=session.events[-50:],
            agent_traces=session.agent_traces[-20:],
            available_actions=AVAILABLE_ACTIONS,
            completed=session.completed,
            result=session.result,
        )

    def _classify_action(self, text: str) -> tuple[str, str | None]:
        normalized = text.strip().lower()
        target_id = self._resolve_target(normalized)
        if any(keyword in normalized for keyword in ("최종 보고", "보고 제출", "report", "결론 제출")):
            return "report_conclusion", target_id
        if any(keyword in normalized for keyword in ("증거", "메시지 기록", "로그 확인", "기록 확인", "inspect", "조사")):
            if any(keyword in normalized for keyword in ("보여", "제시", "전달", "공개", "backend", "백엔드")):
                return "show_evidence", target_id
            return "inspect", target_id
        if any(keyword in normalized for keyword in ("회의", "모두", "summon")):
            return "summon_meeting", target_id
        if any(keyword in normalized for keyword in ("롤백", "rollback", "배포 중단")):
            return "order", target_id
        if any(keyword in normalized for keyword in ("책임", "원인", "잘못", "비난", "accuse", "뒤집어")):
            return "accuse", target_id
        if any(keyword in normalized for keyword in ("묻", "질문", "알고", "무엇", "왜", "ask", "question")):
            return "ask", target_id
        if any(keyword in normalized for keyword in ("이동", "move", "자리", "회의실로")):
            return "move", target_id
        return "talk", target_id

    def _resolve_target(self, text: str) -> str | None:
        aliases = {
            "backend_01": ("backend", "백엔드", "백엔드 개발자", "서버"),
            "frontend_01": ("frontend", "프론트", "프론트엔드", "클라이언트"),
            "pm_01": ("pm", "기획", "planner", "플래너"),
            "qa_01": ("qa", "품질", "테스트", "qa 엔지니어"),
        }
        for npc_id, candidates in aliases.items():
            if any(candidate in text for candidate in candidates):
                return npc_id
        return None

    def _handle_action(self, session: GameSession, action: str, target_id: str | None, text: str) -> str:
        if action == "inspect":
            return self._inspect_evidence(session, text)
        if action == "show_evidence":
            return self._show_evidence(session, target_id)
        if action == "ask":
            return self._ask_npc(session, target_id)
        if action == "accuse":
            return self._accuse_npc(session, target_id)
        if action == "order":
            self._append_event(session, "Player", "배포 중단 및 롤백을 지시했습니다.", "command")
            session.incident_status = "MITIGATING"
            return "롤백 지시가 기록되었습니다."
        if action == "move":
            session.current_location = "meeting_room" if "회의" in text else "dev_area"
            self._append_event(session, "Player", f"{session.current_location}으로 이동했습니다.", "movement")
            return "현재 위치가 변경되었습니다."
        if action == "summon_meeting":
            session.current_location = "meeting_room"
            self._append_event(session, "Player", "팀원들을 회의실로 소집했습니다.", "command")
            return "회의실에 팀원들이 모였습니다."
        if action == "report_conclusion":
            self._append_event(session, "System", "보고서 화면에서 최종 원인과 기여 요인을 제출하세요.", "prompt")
            return "최종 보고서를 입력하면 사건을 종료할 수 있습니다."

        self._append_event(session, "Player", f'"{text.strip()}"', "dialogue")
        return "행동이 기록되었습니다. 대상을 명시하면 더 정확한 반응을 얻을 수 있습니다."

    def _inspect_evidence(self, session: GameSession, text: str) -> str:
        evidence_id = self._evidence_from_text(text) or "release_timeline"
        self._discover_evidence(session, evidence_id)
        evidence = session.evidences[evidence_id]
        self._append_event(session, "Player", f"Evidence 확인: {evidence.title}", "evidence")
        return evidence.content

    def _show_evidence(self, session: GameSession, target_id: str | None) -> str:
        if not session.discovered_evidence:
            self._discover_evidence(session, "qa_warning_message")
        evidence_id = next(iter(session.discovered_evidence))
        evidence = session.evidences[evidence_id]
        target = target_id or "qa_01"
        if target in session.npcs:
            self._append_event(session, "Player", f"{session.npcs[target].name}에게 {evidence.title}를 제시했습니다.", "evidence")
            if target == "backend_01" and evidence_id == "qa_warning_message":
                self._update_backend_after_warning(session)
                return "Backend Developer가 QA 경고를 확인했습니다. 위험을 알고도 배포했는지 되짚기 시작합니다."
        return evidence.content

    def _ask_npc(self, session: GameSession, target_id: str | None) -> str:
        target_id = target_id or "qa_01"
        npc = session.npcs.get(target_id)
        if npc is None:
            self._append_event(session, "System", "질문할 NPC를 찾지 못했습니다.", "guardrail")
            return "질문할 NPC를 찾지 못했습니다."
        if target_id == "qa_01":
            dialogue = "배포 20분 전에 Critical Issue를 발견했고, 배포를 막아야 한다고 메시지를 보냈습니다."
        elif target_id == "backend_01":
            dialogue = "API 응답 스키마를 바꿨습니다. 일정이 촉박했지만 배포는 진행해야 한다고 판단했습니다."
        elif target_id == "frontend_01":
            dialogue = "API 변경을 늦게 전달받았습니다. 제 쪽 로컬 검증은 통과한 상태였습니다."
        else:
            dialogue = "일정이 하루 당겨졌고, 사업 측 압박이 있었습니다."
        decision = AgentDecision(
            npc_id=target_id,
            emotion=npc.dynamic_state.emotion,
            stress_delta=0,
            trust_delta=0,
            cooperation_delta=0,
            action_type="dialogue",
            dialogue=dialogue,
        )
        self._apply_decision(session, npc, decision, f"Player asked {npc.name} about the incident.")
        self._append_event(session, npc.name, decision.dialogue, "dialogue", npc.id)
        return decision.dialogue

    def _accuse_npc(self, session: GameSession, target_id: str | None) -> str:
        target_id = target_id or "qa_01"
        npc = session.npcs.get(target_id)
        if npc is None:
            self._append_event(session, "System", "책임을 물을 NPC를 찾지 못했습니다.", "guardrail")
            return "책임을 물을 NPC를 찾지 못했습니다."
        if target_id == "qa_01":
            decision = AgentDecision(
                npc_id=target_id,
                emotion="defensive",
                stress_delta=10,
                trust_delta=-15,
                cooperation_delta=-10,
                memory_candidate=Memory(summary="Player blamed QA despite the existing warning.", importance=0.85, turn=session.turn),
                action_type="show_evidence",
                action_target="qa_warning_message",
                dialogue="저를 탓하기 전에 경고 메시지를 확인해 주세요. 배포 전에 이미 위험을 보고했습니다.",
            )
        else:
            decision = AgentDecision(
                npc_id=target_id,
                emotion="defensive",
                stress_delta=8,
                trust_delta=-10,
                cooperation_delta=-5,
                action_type="dialogue",
                dialogue="제 책임만으로 단정하기에는 일정과 공유 과정에도 문제가 있었습니다.",
            )
        self._apply_decision(session, npc, decision, f"Player accused {npc.name}.")
        self._append_event(session, npc.name, decision.dialogue, "dialogue", npc.id)
        if decision.action_type == "show_evidence" and decision.action_target:
            self._discover_evidence(session, decision.action_target)
            self._append_event(session, npc.name, "QA Warning evidence를 공개했습니다.", "evidence", npc.id)
        return decision.dialogue

    def _apply_decision(self, session: GameSession, npc: NPCState, decision: AgentDecision, event: str) -> None:
        checks = self._validate_decision(session, npc, decision)
        rejected = any(not check.passed for check in checks)
        if rejected:
            trace_decision = self._safe_fallback(npc)
            guardrails = checks + [GuardrailCheck(name="fallback_action", passed=True, detail="Safe dialogue fallback applied.")]
            fallback_used = True
        else:
            trace_decision = decision
            guardrails = checks
            fallback_used = False

        npc.dynamic_state = self._bounded_dynamic_state(npc.dynamic_state, trace_decision)
        for belief in trace_decision.belief_updates:
            self._upsert_belief(npc, belief)
        if trace_decision.memory_candidate:
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
                context_summary=f"{npc.name} evaluated the player's latest action using its private knowledge boundary.",
                known_facts=list(npc.known_facts),
                retrieved_rules=list(INCIDENT_RULES[:1]) if npc.id == "qa_01" else [],
                decision=trace_decision,
                guardrails=guardrails,
                fallback_used=fallback_used,
            )
        )

    def _validate_decision(self, session: GameSession, npc: NPCState, decision: AgentDecision) -> list[GuardrailCheck]:
        action_targets = set(session.evidences) | set(session.npcs) | {None}
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
                context_summary="Backend Developer evaluated newly revealed evidence against its existing belief.",
                known_facts=list(backend.known_facts),
                retrieved_rules=list(INCIDENT_RULES),
                decision=AgentDecision(
                    npc_id=backend.id,
                    emotion=backend.dynamic_state.emotion,
                    stress_delta=8,
                    trust_delta=3,
                    cooperation_delta=0,
                    belief_updates=[belief],
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
        average_trust = sum(npc.dynamic_state.trust_toward_player for npc in session.npcs.values()) / len(session.npcs)
        team_trust = max(0, min(100, round(60 + average_trust / 2)))
        efficiency = max(20, min(100, 100 - max(0, session.turn - 5) * 4))
        return GameResult(
            incident_diagnosis=diagnosis,
            evidence_coverage=coverage,
            team_trust=team_trust,
            recovery_efficiency=efficiency,
            summary="API schema 변경이 QA 검증 완료 전에 배포된 것이 직접 원인으로 평가되었습니다.",
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

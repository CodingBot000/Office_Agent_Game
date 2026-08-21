from app.models import AgentDecision, IntentClassification, Memory
from app.providers.base import AgentProvider, DecisionContext, IntentContext, IntentProvider, ProviderError


class DeterministicIntentProvider:
    """Keyword fallback used only when the semantic intent provider fails."""

    name = "deterministic-mock"
    model = "deterministic-rules"

    def classify(self, context: IntentContext) -> IntentClassification:
        normalized = context.player_input.strip().lower()
        target_npc_id = self._resolve_target(normalized)

        if any(keyword in normalized for keyword in ("최종 보고", "보고 제출", "report", "결론 제출")):
            return self._result("report_conclusion", target_npc_id, 0.8)
        if any(keyword in normalized for keyword in ("증거", "메시지 기록", "로그 확인", "기록 확인", "inspect", "조사")):
            if any(keyword in normalized for keyword in ("보여", "제시", "전달", "공개", "backend", "백엔드")):
                return self._result("show_evidence", target_npc_id, 0.8, self._resolve_evidence(normalized))
            return self._result("inspect", target_npc_id, 0.8, self._resolve_evidence(normalized))
        if any(keyword in normalized for keyword in ("회의", "모두", "summon")):
            return self._result("summon_meeting", target_npc_id, 0.8, location="meeting_room")
        if any(keyword in normalized for keyword in ("롤백", "rollback", "배포 중단")):
            return self._result("order", target_npc_id, 0.8)
        if any(keyword in normalized for keyword in ("책임", "원인", "잘못", "비난", "accuse", "뒤집어")):
            return self._result("accuse", target_npc_id, 0.8)
        if any(keyword in normalized for keyword in ("묻", "질문", "알고", "무엇", "왜", "뭐야", "뭐가", "무슨", "알려", "설명", "말해", "궁금", "ask", "question")):
            return self._result("ask", target_npc_id, 0.75)
        if any(keyword in normalized for keyword in ("이동", "move", "자리", "회의실로")):
            return self._result("move", target_npc_id, 0.8, location=self._resolve_location(normalized))
        return self._result("talk", target_npc_id, 0.35)

    def _result(
        self,
        intent: str,
        target_npc_id: str | None,
        confidence: float,
        evidence_id: str | None = None,
        location: str | None = None,
    ) -> IntentClassification:
        return IntentClassification(
            intent=intent,  # type: ignore[arg-type]
            target_npc_id=target_npc_id,
            evidence_id=evidence_id,
            location=location,  # type: ignore[arg-type]
            confidence=confidence,
        )

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

    def _resolve_evidence(self, text: str) -> str | None:
        if "api" in text or "스키마" in text:
            return "api_schema_diff"
        if "일정" in text or "timeline" in text:
            return "release_timeline"
        if "qa" in text or "경고" in text or "warning" in text or "메시지" in text:
            return "qa_warning_message"
        return None

    def _resolve_location(self, text: str) -> str:
        if "회의" in text:
            return "meeting_room"
        if any(keyword in text for keyword in ("qa", "품질", "테스트")):
            return "qa_desk"
        if any(keyword in text for keyword in ("pm", "기획", "planner", "플래너")):
            return "pm_desk"
        return "dev_area"


class DeterministicDecisionProvider:
    name = "deterministic-mock"
    model = "deterministic-rules"

    def decide(self, context: DecisionContext) -> AgentDecision:
        npc = context.npc
        if context.mode == "ask":
            if npc.id == "qa_01":
                dialogue = "배포 20분 전에 Critical Issue를 발견했고, 배포를 막아야 한다고 메시지를 보냈습니다."
            elif npc.id == "backend_01":
                dialogue = "API 응답 스키마를 바꿨습니다. 일정이 촉박했지만 배포는 진행해야 한다고 판단했습니다."
            elif npc.id == "frontend_01":
                dialogue = "API 변경을 늦게 전달받았습니다. 제 쪽 로컬 검증은 통과한 상태였습니다."
            else:
                dialogue = "일정이 하루 당겨졌고, 사업 측 압박이 있었습니다."
            return AgentDecision(
                npc_id=npc.id,
                emotion=npc.dynamic_state.emotion,
                stress_delta=0,
                trust_delta=0,
                cooperation_delta=0,
                action_type="dialogue",
                dialogue=dialogue,
            )

        if context.mode == "accuse":
            if npc.id == "qa_01":
                return AgentDecision(
                    npc_id=npc.id,
                    emotion="defensive",
                    stress_delta=10,
                    trust_delta=-15,
                    cooperation_delta=-10,
                    memory_candidate=Memory(
                        summary="Player blamed QA despite the existing warning.",
                        importance=0.85,
                        turn=context.turn,
                    ),
                    action_type="show_evidence",
                    action_target="qa_warning_message",
                    dialogue="저를 탓하기 전에 경고 메시지를 확인해 주세요. 배포 전에 이미 위험을 보고했습니다.",
                )
            return AgentDecision(
                npc_id=npc.id,
                emotion="defensive",
                stress_delta=8,
                trust_delta=-10,
                cooperation_delta=-5,
                action_type="dialogue",
                dialogue="제 책임만으로 단정하기에는 일정과 공유 과정에도 문제가 있었습니다.",
            )

        raise ProviderError(f"Unsupported deterministic decision mode: {context.mode}")

from app.models import AgentDecision, IntentClassification, Memory, SocialImpactClassification
from app.providers.base import (
    AgentProvider,
    DecisionContext,
    IntentContext,
    IntentProvider,
    ProviderError,
    SocialImpactContext,
    SocialImpactProvider,
)


class DeterministicIntentProvider:
    """Keyword fallback used only when the semantic intent provider fails."""

    name = "deterministic-mock"
    model = "deterministic-rules"

    def classify(self, context: IntentContext) -> IntentClassification:
        normalized = context.player_input.strip().lower()
        target_npc_id = context.target_hint or self._resolve_target(normalized)

        if any(keyword in normalized for keyword in ("최종 보고", "보고 제출", "report", "결론 제출")):
            return self._result("report_conclusion", target_npc_id, 0.8)
        if any(
            keyword in normalized
            for keyword in (
                "키보드",
                "모니터",
                "서류를 찢",
                "빼앗",
                "뺏",
                "던져",
                "던진",
                "부숴",
                "파손",
                "윽박",
                "소리 질",
                "욕",
                "무능",
                "협박",
                "위협",
                "때려",
                "때린",
                "폭행",
                "밀쳐",
                "밀친",
                "사과",
                "중재",
                "보상",
                "새 키보드",
            )
        ):
            return self._result("social_action", target_npc_id, 0.8)
        if any(keyword in normalized for keyword in ("증거", "메시지", "로그 확인", "기록 확인", "inspect", "조사")):
            if any(keyword in normalized for keyword in ("보여", "보여줘", "보여줄", "확인", "요청", "알려")):
                return self._result("request_evidence", target_npc_id, 0.8, self._resolve_evidence(normalized))
            if any(keyword in normalized for keyword in ("제시", "전달", "공개", "backend", "백엔드")):
                return self._result("show_evidence", target_npc_id, 0.8, self._resolve_evidence(normalized))
            return self._result("inspect", target_npc_id, 0.8, self._resolve_evidence(normalized))
        if any(keyword in normalized for keyword in ("회의", "모두", "summon")):
            return self._result("summon_meeting", target_npc_id, 0.8, location="meeting_room")
        if any(keyword in normalized for keyword in ("롤백", "rollback", "배포 중단")):
            return self._result("order", target_npc_id, 0.8)
        if any(keyword in normalized for keyword in ("옹호", "두둔", "잘못이 아니", "책임이 없", "defend")):
            return self._result("defend", target_npc_id, 0.8)
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
        if context.mode == "talk":
            dialogue_by_npc = {
                "qa_01": "현재 장애 원인을 확인하려면 배포 전 경고와 승인 과정을 먼저 살펴봐야 합니다.",
                "backend_01": "API 변경과 배포 판단 과정을 확인하면 사고 흐름을 파악할 수 있습니다.",
                "frontend_01": "API 변경 전달 시점과 실제 반영 여부를 확인해 주세요.",
                "pm_01": "일정 변경과 의사결정 과정은 제가 설명드릴 수 있습니다.",
            }
            return AgentDecision(
                npc_id=npc.id,
                emotion=npc.dynamic_state.emotion,
                stress_delta=0,
                trust_delta=0,
                cooperation_delta=1,
                knowledge_refs=list(npc.known_fact_ids[:2]),
                action_type="dialogue",
                dialogue=dialogue_by_npc.get(npc.id, "현재 상황에서 제가 알고 있는 범위부터 설명드리겠습니다."),
            )

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
                knowledge_refs=list(npc.known_fact_ids),
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
                    knowledge_refs=["qa_found_critical_issue", "qa_sent_warning", "warning_recommended_deploy_block"],
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
                knowledge_refs=list(npc.known_fact_ids[:2]),
                action_type="dialogue",
                dialogue="제 책임만으로 단정하기에는 일정과 공유 과정에도 문제가 있었습니다.",
            )

        if context.mode == "defend":
            return AgentDecision(
                npc_id=npc.id,
                emotion="relieved",
                stress_delta=-8,
                trust_delta=10,
                cooperation_delta=5,
                memory_candidate=Memory(
                    summary=f"Player publicly defended {npc.name} during the incident review.",
                    importance=0.75,
                    turn=context.turn,
                ),
                knowledge_refs=list(npc.known_fact_ids[:2]),
                action_type="dialogue",
                dialogue="제 설명을 고려해 주셔서 감사합니다. 알고 있는 사실을 더 적극적으로 공유하겠습니다.",
            )

        raise ProviderError(f"Unsupported deterministic decision mode: {context.mode}")


class DeterministicSocialImpactProvider:
    """Keyword fallback used only when semantic social-impact classification fails."""

    name = "deterministic-mock"
    model = "deterministic-rules"

    def classify_social_impact(self, context: SocialImpactContext) -> SocialImpactClassification:
        text = context.player_input.strip().lower()
        target_id = self._resolve_target(text, context)
        object_id = self._resolve_object(text, target_id, context)

        if any(keyword in text for keyword in ("때려", "때린", "폭행", "주먹", "발로 차")):
            return self._result("physical_assault", target_id, object_id, 5, ["physical_danger"], True)
        if object_id and any(keyword in text for keyword in ("던져", "던진", "부숴", "파손", "깨뜨", "찢")):
            return self._result(
                "property_aggression",
                target_id,
                object_id,
                4,
                ["property_violation", "property_damage", "physical_danger"],
                True,
            )
        if any(keyword in text for keyword in ("던져", "던진", "부숴", "파손", "깨뜨", "찢")):
            return self._result("verbal_pressure", target_id, None, 2, ["coercion"], True)
        if any(keyword in text for keyword in ("밀쳐", "밀친", "가로막", "물리적으로 위협")):
            return self._result("physical_intimidation", target_id, object_id, 4, ["physical_danger"], True)
        if any(keyword in text for keyword in ("협박", "가만두지", "해고시켜", "위협")):
            return self._result("threat", target_id, object_id, 4, ["credible_threat", "coercion"], True)
        if object_id and any(keyword in text for keyword in ("빼앗", "뺏", "숨겨", "가져가")):
            return self._result("property_interference", target_id, object_id, 3, ["property_violation"], True)
        if any(keyword in text for keyword in ("모두 앞", "공개적으로", "망신", "쪽팔")):
            return self._result("public_humiliation", target_id, object_id, 3, ["personal_attack", "public_exposure"], True)
        if any(keyword in text for keyword in ("윽박", "소리 질", "당장 대답", "강압")):
            return self._result("verbal_pressure", target_id, object_id, 3, ["coercion"], True)
        if any(keyword in text for keyword in ("무능", "멍청", "욕", "한심")):
            return self._result("insult", target_id, object_id, 3, ["personal_attack"], True)
        if any(keyword in text for keyword in ("사과", "미안")):
            return self._result("apology", target_id, object_id, 2, ["accountability"], False)
        if any(keyword in text for keyword in ("보상", "새 키보드", "수리", "복구")):
            return self._result("repair_action", target_id, object_id, 2, ["repair"], False)
        if any(keyword in text for keyword in ("중재", "화해", "조정")):
            return self._result("mediation", target_id, object_id, 2, ["mediation"], True)
        if any(keyword in text for keyword in ("옹호", "도와", "지지")):
            return self._result("support", target_id, object_id, 2, ["support"], True)
        if any(keyword in text for keyword in ("증거", "근거", "로그")):
            return self._result(
                "evidence_based_confrontation",
                target_id,
                object_id,
                2,
                ["factual_challenge"],
                True,
                evidence_based=True,
            )
        if any(keyword in text for keyword in ("숨겨", "거짓", "속여")):
            return self._result("deception", target_id, object_id, 3, ["dishonesty"], False)
        if any(keyword in text for keyword in ("방해", "삭제", "차단")):
            return self._result("sabotage", target_id, object_id, 3, ["work_disruption"], True)
        return self._result("constructive_dialogue", target_id, object_id, 1, ["constructive"], False)

    def _result(
        self,
        action_family: str,
        target_id: str | None,
        object_id: str | None,
        severity: int,
        reason_codes: list[str],
        observable: bool,
        evidence_based: bool = False,
    ) -> SocialImpactClassification:
        target_ids = [target_id] if target_id else []
        return SocialImpactClassification(
            action_family=action_family,  # type: ignore[arg-type]
            direct_target_ids=target_ids,
            affected_target_ids=[],
            object_id=object_id,
            severity=severity,
            intentionality="deliberate",
            observable=observable,
            evidence_based=evidence_based,
            reason_codes=reason_codes,  # type: ignore[arg-type]
            confidence=0.72,
        )

    def _resolve_target(self, text: str, context: SocialImpactContext) -> str | None:
        aliases = {
            "backend_01": ("backend", "백엔드", "서버"),
            "frontend_01": ("frontend", "프론트", "클라이언트"),
            "qa_01": ("qa", "품질", "테스트"),
            "pm_01": ("pm", "기획", "플래너"),
        }
        for npc_id, candidates in aliases.items():
            if npc_id in context.available_npc_ids and any(candidate in text for candidate in candidates):
                return npc_id
        if context.target_hint in context.available_npc_ids:
            return context.target_hint
        return context.available_npc_ids[0] if len(context.available_npc_ids) == 1 else None

    def _resolve_object(self, text: str, target_id: str | None, context: SocialImpactContext) -> str | None:
        for object_id in context.available_object_ids:
            if object_id in text:
                return object_id
        if "키보드" in text and target_id:
            candidate = target_id.removesuffix("_01") + "_keyboard"
            if candidate in context.available_object_ids:
                return candidate
        object_aliases = {
            "meeting_room_monitor": ("모니터", "화면"),
            "release_document": ("배포 문서", "릴리스 문서"),
            "qa_warning_printout": ("경고 출력물", "경고 문서"),
        }
        for object_id, aliases in object_aliases.items():
            if object_id in context.available_object_ids and any(alias in text for alias in aliases):
                return object_id
        return None

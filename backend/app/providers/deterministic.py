import re

from app.models import AgentDecision, IntentClassification, Memory, SocialImpactClassification
from app.game.seed import RESPONSIBILITY_FACT_IDS, NPC_ALIASES
from app.providers.base import (
    AgentProvider,
    DecisionContext,
    IntentContext,
    IntentProvider,
    ProviderError,
    SocialImpactContext,
    SocialImpactProvider,
)


RESPONSIBILITY_QUESTION_TERMS = (
    "책임자",
    "담당자",
    "배포 담당",
    "누가 배포",
    "누구 책임",
    "누구에게 물어",
    "누구한테 물어",
    "어디에 물어",
    "owner",
    "responsible",
    "who deployed",
    "who owns",
)


def is_responsibility_question(text: str) -> bool:
    normalized = text.casefold()
    if any(term in normalized for term in ("책임자라고", "책임을 묻", "비난", "탓한다", "탓하")):
        return False
    return any(term in normalized for term in RESPONSIBILITY_QUESTION_TERMS)


class DeterministicIntentProvider:
    """Keyword fallback used only when the semantic intent provider fails."""

    name = "deterministic-mock"
    model = "deterministic-rules"

    def classify(self, context: IntentContext) -> IntentClassification:
        normalized = context.player_input.strip().lower()
        target_npc_id = context.target_hint or self._resolve_target(normalized)

        if is_responsibility_question(normalized):
            return self._result(
                "ask",
                target_npc_id,
                0.98,
                question_type="responsibility_routing",
            )

        game_action_family = self._resolve_game_action_family(normalized)
        if game_action_family is not None:
            return self._result(
                "social_action",
                target_npc_id,
                0.98,
                interaction_kind="game_action_attempt",
                game_action_family=game_action_family,
            )

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
        if any(
            keyword in normalized
            for keyword in (
                "증거",
                "메시지",
                "로그 확인",
                "기록 확인",
                "inspect",
                "조사",
                "에러명",
                "오류명",
                "에러 내용",
                "오류 내용",
                "무슨 이슈",
                "어떤 이슈",
                "치명적 이슈",
                "critical issue",
            )
        ):
            if any(keyword in normalized for keyword in ("보여", "보여줘", "보여줄", "확인", "요청", "알려")):
                return self._result(
                    "request_evidence",
                    target_npc_id,
                    0.8,
                    self._resolve_evidence(normalized),
                    question_type="evidence_request",
                    reference_scope="explicit",
                )
            if any(keyword in normalized for keyword in ("제시", "전달", "공개", "backend", "백엔드")):
                return self._result(
                    "show_evidence",
                    target_npc_id,
                    0.8,
                    self._resolve_evidence(normalized),
                    reference_scope="explicit",
                )
            return self._result(
                "inspect",
                target_npc_id,
                0.8,
                self._resolve_evidence(normalized),
                question_type="evidence_request",
                reference_scope="explicit",
            )
        if any(keyword in normalized for keyword in ("회의", "모두", "summon")):
            return self._result("summon_meeting", target_npc_id, 0.8, location="meeting_room")
        if any(keyword in normalized for keyword in ("롤백", "rollback", "배포 중단")):
            negated = bool(re.search(r"(?:롤백|배포\s*중단).{0,12}(?:하지\s*(?:마|말|않)|금지)|(?:do not|don't|never)\s+roll\s*back", normalized))
            return self._result("order", target_npc_id, 0.8, command_kind=None if negated else "rollback")
        if any(keyword in normalized for keyword in ("옹호", "두둔", "잘못이 아니", "책임이 없", "defend")):
            return self._result("defend", target_npc_id, 0.8)
        latest_evidence_id = context.latest_discovered_evidence_id
        if latest_evidence_id and any(
            keyword in normalized
            for keyword in ("이게", "이거", "이 증거", "이 메시지", "이 내용", "무슨 뜻", "왜 중요", "설명해", "자세히")
        ):
            return self._result(
                "ask",
                target_npc_id,
                0.85,
                latest_evidence_id,
                question_type="evidence_followup",
                reference_scope="latest_discovered",
            )
        if any(keyword in normalized for keyword in ("승인", "일정", "릴리스 절차", "배포 절차")):
            return self._result("ask", target_npc_id, 0.8, question_type="approval_process")
        if any(keyword in normalized for keyword in ("원인", "왜 발생", "왜 장애", "사고 이유")) and any(
            keyword in normalized for keyword in ("누구", "무엇", "뭐", "왜", "설명", "알려")
        ):
            return self._result("ask", target_npc_id, 0.82, question_type="cause_analysis")
        if any(keyword in normalized for keyword in ("책임", "원인", "잘못", "비난", "accuse", "뒤집어")):
            return self._result("accuse", target_npc_id, 0.8)
        if any(keyword in normalized for keyword in ("묻", "질문", "알고", "무엇", "왜", "뭐야", "뭐가", "무슨", "알려", "설명", "말해", "궁금", "ask", "question")):
            return self._result("ask", target_npc_id, 0.75, question_type="general_status")
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
        interaction_kind: str = "dialogue",
        game_action_family: str | None = None,
        question_type: str = "none",
        reference_scope: str = "none",
        command_kind: str | None = None,
    ) -> IntentClassification:
        return IntentClassification(
            intent=intent,  # type: ignore[arg-type]
            command_kind=command_kind,
            interaction_kind=interaction_kind,  # type: ignore[arg-type]
            game_action_family=game_action_family,  # type: ignore[arg-type]
            question_type=question_type,  # type: ignore[arg-type]
            reference_scope=reference_scope,  # type: ignore[arg-type]
            target_npc_id=target_npc_id,
            evidence_id=evidence_id,
            location=location,  # type: ignore[arg-type]
            confidence=confidence,
        )

    def _resolve_game_action_family(self, text: str) -> str | None:
        object_words = ("키보드", "모니터", "서류", "문서", "물건")
        if not any(word in text for word in object_words):
            return None
        if any(keyword in text for keyword in ("부숴", "부수", "부순", "깨뜨", "깨", "파손")):
            return "break_held_object"
        if any(keyword in text for keyword in ("내려놓", "놓아", "놓는다")):
            return "drop_held_object"
        if any(keyword in text for keyword in ("던져", "던진", "집어", "잡아", "뺏", "빼앗", "들어")):
            return "pick_up_object"
        return None

    def _resolve_target(self, text: str) -> str | None:
        for npc_id, candidates in NPC_ALIASES.items():
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
        if context.mode == "social_reaction":
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
            family = context.social_classification.action_family if context.social_classification else "constructive_dialogue"
            return AgentDecision(
                npc_id=npc.id, emotion=npc.dynamic_state.emotion, stress_delta=0, trust_delta=0,
                cooperation_delta=0, action_type="dialogue", grounding_type="acknowledgement",
                dialogue=reactions.get(family, "말씀을 확인했습니다."),
                response_kind=context.required_response_kind,
            )
        if context.question_type == "evidence_followup":
            explanation_by_id = {
                "qa_warning_message": (
                    "이 경고는 production-like 테스트에서 API response mismatch를 발견했고, "
                    "계약 검증 전까지 배포를 막으라고 권고한 내용입니다."
                ),
                "api_schema_diff": (
                    "이 기록은 response.data.items가 response.payload.items로 바뀌었지만 "
                    "Frontend 반영이 같은 릴리스에 포함되지 않았다는 뜻입니다."
                ),
                "release_timeline": (
                    "이 기록은 일정이 하루 앞당겨졌고, 16:40 QA 경고 후 17:00에 Production 배포가 시작됐다는 뜻입니다."
                ),
            }
            dialogue = explanation_by_id.get(
                context.referenced_evidence_id or "",
                "이미 확보한 증거의 내용과 사건 기록을 함께 확인해 보겠습니다.",
            )
            return AgentDecision(
                npc_id=npc.id,
                emotion=npc.dynamic_state.emotion,
                stress_delta=0,
                trust_delta=0,
                cooperation_delta=0,
                grounding_type="acknowledgement",
                action_type="dialogue",
                dialogue=dialogue,
            )

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
            if context.question_type == "responsibility_routing":
                dialogue_by_npc = {
                    "qa_01": (
                        "저는 배포 권한이 없습니다. 실제 릴리스 배포와 API 계약 변경은 Backend Developer가 담당했습니다. "
                        "배포 실행과 API 변경은 Backend Developer에게, 일정과 승인 경위는 PM에게 확인해 주세요."
                    ),
                    "backend_01": (
                        "실제 배포 실행과 API 계약 변경은 제가 담당했습니다. "
                        "QA 경고와 일정 압박이 있었던 당시의 판단 경위를 설명드리겠습니다."
                    ),
                    "frontend_01": (
                        "API 계약 변경과 배포 실행은 Backend Developer에게 확인해 주세요. "
                        "저는 변경사항을 늦게 전달받았고 마지막 로컬 검증은 통과했습니다."
                    ),
                    "pm_01": (
                        "실제 Production 배포와 API 계약 변경은 Backend Developer가 담당했습니다. "
                        "저는 릴리스 일정이 앞당겨진 경위와 일정 압박을 설명하겠습니다."
                    ),
                }
                dialogue = dialogue_by_npc.get(npc.id, "Backend Developer에게 배포 실행과 API 변경을 확인해 주세요.")
            elif npc.id == "qa_01":
                dialogue = "배포 전 검증 로그와 API 응답을 대조하고 있습니다. 정확한 내용은 증거를 요청하시면 공유하겠습니다."
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
                knowledge_refs=(
                    [fact_id for fact_id in RESPONSIBILITY_FACT_IDS if fact_id in npc.known_fact_ids]
                    if context.question_type == "responsibility_routing"
                    else list(npc.known_fact_ids)
                ),
                action_type="dialogue",
                dialogue=dialogue,
            )

        if context.mode == "show_evidence":
            if "repeated_presentation" in context.player_input:
                dialogue = "이 증거는 이미 확인했습니다. 같은 내용을 다시 제시해도 판단은 달라지지 않습니다."
            elif "same_source_acknowledgement" in context.player_input:
                dialogue = "이 메시지는 제가 보낸 경고입니다. 이미 알고 있는 내용이니, 어떻게 처리됐는지 확인해 주세요."
            elif npc.id == "backend_01":
                dialogue = "QA 경고가 있었던 것은 확인했습니다. 제가 API 응답 스키마를 변경한 상태에서 배포를 진행했고, 당시 판단 과정을 다시 검토하겠습니다."
            elif npc.id == "frontend_01":
                dialogue = "QA 경고와 API 변경 내용을 함께 확인해 보겠습니다. 프론트엔드 반영 시점도 다시 점검하겠습니다."
            elif npc.id == "pm_01":
                dialogue = "배포 전에 이런 경고가 있었다면 일정과 승인 과정에서 검토했어야 합니다."
            else:
                dialogue = "제시된 증거를 확인했습니다. 이 내용이 어떻게 처리됐는지 함께 확인해 보겠습니다."

            return AgentDecision(
                npc_id=npc.id,
                emotion=npc.dynamic_state.emotion,
                stress_delta=0,
                trust_delta=0,
                cooperation_delta=0,
                grounding_type="acknowledgement",
                action_type="show_evidence",
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
        for npc_id, candidates in NPC_ALIASES.items():
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

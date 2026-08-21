from app.models import AgentDecision, Memory
from app.providers.base import AgentProvider, DecisionContext, ProviderError


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

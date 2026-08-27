# Backend

FastAPI 기반의 게임 세션 API입니다. CLI와 OpenAI Responses API provider를 같은 structured Intent/Decision 계약으로 지원하며, World State와 NPC State는 backend가 소유합니다.

## 환경 설정

```bash
cp .env.example .env
```

`backend/.env`는 Git에 커밋하지 않습니다. `AI_PROVIDER`로 실행 환경의 provider를 선택합니다.

```dotenv
# Local: existing CLI authentication/session
AI_PROVIDER=cli
AI_CLI_COMMAND=codex
AI_CLI_MODEL=gpt-5.6-luna
AI_CLI_TIMEOUT_SECONDS=120
```

```dotenv
# Remote: OpenAI API key
AI_PROVIDER=openai
OPENAI_API_KEY=your-local-openai-api-key
OPENAI_MODEL=your-supported-openai-model
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_TIMEOUT_SECONDS=30
```

```dotenv
# Tests: no credentials required
AI_PROVIDER=deterministic-mock
```

`OPENAI_API_KEY`는 frontend가 아니라 backend 환경변수에만 둡니다.

Session은 기본적으로 SQLite `data/office_agent.db`에 저장됩니다. 테스트에서는 `SESSION_STORAGE=memory`를 사용합니다.

```dotenv
SESSION_STORAGE=sqlite
SQLITE_PATH=data/office_agent.db
```

Provider가 실패하거나 guardrail에서 결과를 거부하면 deterministic fallback이 마지막 방어 수단으로 실행됩니다. 이 경우 화면 banner, Event Log, Agent Inspector, backend warning log에 fallback 원인이 표시됩니다.

보고서 평가는 예외입니다. CLI/OpenAI의 의미 추출이 실패하면 키워드 채점으로 대체하지 않고 HTTP 503을 반환하며 사건을 종료하지 않습니다. 입력을 유지한 뒤 다시 제출할 수 있습니다. `deterministic-mock`의 보고서 해석은 오프라인 데모용 제한된 어휘만 지원하며 실제 모델의 의미 이해 품질을 검증하는 용도가 아닙니다.

## 대화 및 상태 처리

- 관계 수치·월드 결과는 서버가 결정하고, 사회적 행동과 물건 액션의 NPC 반응은 그 결과를 컨텍스트로 받아 생성합니다. 대사 생성 결과가 효과를 다시 적용할 수는 없습니다. 이 경로에는 이전보다 provider 호출이 추가될 수 있습니다.
- Player가 확보한 증거와 NPC가 직접 확인한 증거를 구분합니다. 공유된 문서가 뒷받침하는 사실만 후속 답변의 근거로 사용하며, 새로운 이벤트는 증거·수신 NPC ID를 저장합니다.
- NPC 결정 문맥은 Player와 공유된 `visible_evidences`와 NPC 자신이 제공할 수 있는 `shareable_evidences`를 구분합니다. NPC는 자신의 미공개 자료 내용도 알고 있으며, 다른 NPC의 자료는 제시받기 전까지 알 수 없습니다.
- 일반 질문은 `ask`/`talk`로 유지합니다. 설명을 뒷받침할 자료를 공개할지는 AI가 현재 발화·해당 NPC와의 대화·자신의 자료를 보고 `AgentDecision.show_evidence`로 선택합니다. 서버는 자료 제공 권한과 참조를 검증한 뒤 획득 상태와 이벤트를 한 번만 저장합니다. 특정 단어나 대화 횟수가 공개를 강제하지 않습니다.
- 대사에는 표시 이름과 문서 제목을 사용합니다. 내부 ID는 구조화된 참조 필드에 남기고, `game/dialogue_text.py`가 최종 대사의 ID 주석·직접 표기를 정리합니다. 이 표시 처리는 의도 분류나 증거 공개 여부를 바꾸지 않습니다.
- `order`는 명시적인 `command_kind=rollback`으로 분류된 경우에만 실행합니다. 지원되지 않는 업무 지시와 부정된 명령은 롤백으로 바꾸지 않습니다.
- 보고서는 원인 주장·부정·기여 요인을 구조화한 뒤 서버의 시나리오 기준으로 평가합니다. 점수와 요약은 실제 충족·누락·모순 항목에서 계산합니다.

## 동시성 및 저장 호환성

세션은 revision을 비교해 원자적으로 저장합니다. 겹친 요청 중 오래된 상태를 저장하려는 요청은 HTTP 409를 받습니다. 클라이언트는 `GET /sessions/{id}`로 최신 상태를 조회하고 사용자 확인 후 재시도해야 합니다. 서버는 충돌한 AI 요청을 자동으로 재실행하지 않습니다. reset은 기존 세션을 새 ID의 세션으로 원자적으로 교체합니다.

기존 REST 경로와 JSON 필드는 유지하며 `revision`, 관찰 증거·참조·평가 메타데이터를 추가했습니다. 세션 payload는 schema 10이고 이전 payload와 revision 컬럼이 없는 SQLite DB는 자동 마이그레이션합니다. 배포 전에 DB를 백업하세요. 이전 버전 애플리케이션으로 되돌릴 때는 schema 10 데이터를 그대로 읽을 수 없으므로 백업 복원 또는 호환 migration이 필요합니다.

주요 책임은 `game/session.py`, `session_codec.py`, `conversation.py`, `evidence_policy.py`, `social_state.py`, `state_transitions.py`, `reporting.py`로 분리되어 있습니다. `GameEngine`은 요청 순서와 provider 호출을 연결합니다.

```bash
uv sync --extra test
uv run uvicorn app.main:app --reload --port 8000
```

테스트:

```bash
uv run pytest
```

주요 API:

- `POST /api/v1/sessions`
- `GET /api/v1/sessions/{session_id}`
- `POST /api/v1/sessions/{session_id}/actions`
- `POST /api/v1/sessions/{session_id}/report`
- `POST /api/v1/sessions/{session_id}/reset`

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

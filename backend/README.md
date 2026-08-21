# Backend

FastAPI 기반의 게임 세션 API입니다. 현재는 외부 LLM 없이 deterministic mock decision으로 Phase 1 루프를 검증합니다.

## 환경 설정

```bash
cp .env.example .env
```

`backend/.env`는 Git에 커밋하지 않습니다. `AI_PROVIDER`로 실행 환경의 provider를 선택합니다.

```dotenv
# Local: existing CLI authentication/session
AI_PROVIDER=cli
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

현재 Phase 1 engine은 provider adapter를 연결하기 전까지 deterministic decision을 사용합니다. `cli`와 `openai` 값은 provider adapter 연결을 위한 설정 계약으로 먼저 고정해둔 상태입니다.

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

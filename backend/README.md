# Backend

FastAPI 기반의 게임 세션 API입니다. 현재는 외부 LLM 없이 deterministic mock decision으로 Phase 1 루프를 검증합니다.

## 환경 설정

```bash
cp .env.example .env
```

`backend/.env`는 Git에 커밋하지 않습니다. Phase 1은 API Key 없이 실행되도록 `AI_PROVIDER=deterministic-mock`이 기본값입니다. OpenAI provider를 활성화하는 단계에서는 아래 값을 실제 로컬 값으로 교체합니다.

```dotenv
AI_PROVIDER=openai
OPENAI_API_KEY=your-local-openai-api-key
OPENAI_MODEL=your-supported-openai-model
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_TIMEOUT_SECONDS=30
```

`OPENAI_API_KEY`는 frontend가 아니라 backend 환경변수에만 둡니다.

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

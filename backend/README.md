# Backend

FastAPI 기반의 게임 세션 API입니다. 현재는 외부 LLM 없이 deterministic mock decision으로 Phase 1 루프를 검증합니다.

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

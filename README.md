# AI Office Agent MVP

웹 기반 MUD/Text Game 스타일의 AI 사무실 사건 시뮬레이터입니다.

현재 저장소는 `frontend/`와 `backend/`를 독립 실행 가능한 프로젝트로 분리하고, Phase 1 deterministic game loop를 검증할 수 있는 최소 수직 슬라이스를 제공합니다.

## 구조

```text
frontend/   React + TypeScript + Vite UI
backend/    FastAPI + Pydantic 게임 API
docs/       로컬 개발계획서 및 작업 메모 (Git 제외)
```

프론트엔드와 백엔드는 HTTP API로만 통신하며, 게임의 World State와 NPC 상태는 backend가 권한을 가집니다.

## 실행

### Backend

```bash
cd backend
uv sync --extra test
uv run uvicorn app.main:app --reload --port 8000
```

API 문서: http://127.0.0.1:8000/docs

### Frontend

```bash
cd frontend
npm install
npm run dev
```

브라우저: http://127.0.0.1:5173

프론트 개발 서버의 `/api` 요청은 Vite proxy를 통해 `http://127.0.0.1:8000`으로 전달됩니다.

## 검증

```bash
cd backend && uv run pytest
cd frontend && npm run build
```

## 개발 커밋 규칙

기능 단위의 검증 가능한 상태마다 작은 커밋을 남깁니다.

- `chore: initialize frontend and backend workspaces`
- `feat: add deterministic session and action loop`
- `feat: add agent trace and guardrail surface`
- `test: cover knowledge boundary and invalid actions`

문서 변경은 로컬 `docs/`에만 남기며 커밋하지 않습니다. 이 저장소의 추적 가능한 변경은 코드, 테스트, 설정, 루트 문서로 제한합니다.

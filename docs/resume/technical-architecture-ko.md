# AI × Unity 기술 아키텍처 설명

## 1. 설계 원칙

이 프로젝트의 핵심 원칙은 다음과 같다.

```text
LLM = 컨텍스트 기반 판단 후보 생성기
FastAPI GameEngine = 게임 세계의 최종 권한
Unity = 입력·공간 상호작용·시각적 결과 표현
```

LLM 응답을 신뢰해 게임 상태를 직접 수정하지 않는다. LLM은 Pydantic 스키마에 맞는 후보만 반환하며, 서버는 현재 세션의 실제 NPC·위치·증거·오브젝트·관계 상태와 비교한 후 허용된 결과만 반영한다.

## 2. 전체 구성

```text
┌────────────────────────── Unity 2D Client ──────────────────────────┐
│ WASD 이동 / NPC 근접 감지 / 대화 / 액션 버튼 / 연출 / 상태 UI      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ JSON REST
                               │ POST /sessions
                               │ POST /actions
                               │ POST /game-actions
                               │ GET  /sessions/{id}
                               ▼
┌──────────────────────── FastAPI Backend ────────────────────────────┐
│ API Router → GameEngine → Provider Candidate → Guardrail            │
│                         → Relationship Policy → State Mutation      │
│                         → Repository Save → GameSnapshot            │
└───────────────┬───────────────────────────────┬──────────────────────┘
                │                               │
                ▼                               ▼
      CLI / OpenAI / Mock Provider       Memory / SQLite Repository
```

React/Vite 프론트엔드는 Unity 구현 전 동일 API와 상태 모델을 빠르게 검증하기 위한 테스트 클라이언트이며, 텍스트 모드와 2D Office View로 Unity 동작을 미러링한다.

## 3. 자연어 명령 처리 흐름

### 3.1 대화·조사 명령

```text
Player 자연어 입력
  → IntentProvider가 IntentClassification 후보 생성
  → Intent Guardrail
      - target NPC 존재 여부
      - evidence 존재 여부
      - location 존재 여부
      - Player의 evidence 소유 여부
  → 허용된 Action Handler 선택
  → NPC DecisionProvider가 AgentDecision 후보 생성
  → Decision Guardrail
      - 허용 action_type
      - Fact grounding / knowledge_refs 검증
      - relationship target 검증
      - 수치 범위 검증
  → GameEngine이 NPC 상태·대화·기억을 반영
  → Snapshot 저장 및 반환
```

Provider에는 전체 DB가 아니라 현재 판단에 필요한 최소 Context만 전달한다.

- 현재 턴과 위치
- 대상 NPC의 Personality/Dynamic State
- 해당 NPC가 아는 Fact ID
- 발견된 Evidence ID
- 최근 Event와 Memory
- 서버가 소유한 Incident Rule

### 3.2 물리·오브젝트 명령

물건 줍기, 부수기, 버리기, 던지기는 자연어만으로 실행하지 않는다.

```text
자연어 "키보드를 던진다"
  → game_action_attempt 분류
  → 서버가 실행 차단
  → "서버가 제공한 액션 버튼을 사용" 안내

Unity 버튼 클릭
  → 서버 Snapshot의 AvailableGameAction ID 전송
  → 서버가 같은 ID가 현재 상태에서도 유효한지 재검증
  → 유효할 때만 월드 상태 변경
```

이 구조는 프롬프트 인젝션이나 잘못된 LLM 분류가 게임 오브젝트를 임의 변경하는 경로를 차단한다.

## 4. 가드레일과 정책 통제

### 4.1 구조화 출력

- 모든 Provider 결과를 Pydantic 모델로 파싱
- JSON Schema의 모든 필드를 required로 지정
- `additionalProperties=false`로 임의 필드 차단
- action/target/evidence/location vocabulary를 서버 목록으로 제한
- Chain-of-thought 원문을 저장하거나 노출하지 않고 구조화 결과와 검증 Trace만 기록

### 4.2 Intent Guardrail

| 검증 | 통제 목적 |
|---|---|
| target_exists | 존재하지 않는 NPC 생성 방지 |
| evidence_exists | 가짜 증거 참조 방지 |
| location_exists | 맵에 없는 위치 이동 방지 |
| player_evidence_ownership | 미발견 증거 제시 방지 |
| interaction_kind | 자연어 월드 변경 행동 분리 |

### 4.3 Social Impact Guardrail

LLM은 `physical_assault`, `property_aggression`, `support`, `apology` 같은 사회적 행동 종류와 심각도만 분류한다. 관계 수치와 처벌은 생성하지 않는다.

서버는 다음을 검증한다.

- 허용된 action family와 severity 범위
- 대상이 실제 현재 위치에 접근 가능한지
- 오브젝트가 존재하고 현재 위치 또는 Player 손에 있는지
- 오브젝트가 portable/destructible 상태인지
- 관계 graph edge가 존재하는지
- 사과 → 복구 → 중재 단계 순서가 유효한지

### 4.4 Relationship Policy Guardrail

`RelationshipPolicyEngine`이 direct target, object owner, affected NPC, witness 역할에 따라 영향 계수를 적용한다.

- 유해 행동이 trust/respect를 증가시키지 못하게 방향성 검증
- 관계 delta를 서버 envelope 안으로 제한
- Witness 영향이 Direct Target보다 커지지 않게 제한
- Physical Assault에 Security 호출·대화 거부가 포함됐는지 확인
- Property Aggression에 Object Damage가 포함됐는지 확인
- 반복 행동·공개성·고의성·권력 남용 modifier 적용

### 4.5 Deterministic Fallback

다음 상황에서 deterministic provider로 전환한다.

- CLI/OpenAI 호출 실패 또는 timeout
- JSON 파싱/스키마 검증 실패
- Intent Guardrail 실패
- Social Classification Guardrail 실패
- NPC Decision Guardrail 실패

Fallback 발생 시 stage, provider, reason, turn을 `FallbackNotice`와 이벤트 로그에 남기며 UI에서 fallback 사용 사실을 확인할 수 있다.

## 5. NPC 지능 상태 모델

```text
NPCState
├─ Personality
│  ├─ assertiveness
│  ├─ cooperativeness
│  ├─ risk_aversion
│  └─ blame_sensitivity
├─ DynamicState
│  ├─ emotion
│  ├─ stress
│  ├─ trust_toward_player
│  └─ cooperation
├─ physical_state: normal | comatose
├─ known_fact_ids / known_facts
├─ beliefs
├─ recent_memories / important_memories
└─ directional relationships
```

Fact는 서버의 canonical registry에 존재하는 사실만 사용할 수 있다. Belief는 NPC의 주관적 해석이며 canonical truth를 변경하지 않는다. 중요 사건은 모든 관련 NPC의 Memory에 기록되어 이후 대화 Context에 포함된다.

## 6. Unity와 Backend 통신 규칙

### 6.1 세션 시작

```text
Unity Start
  → POST /api/v1/sessions
  → session_id + GameSnapshot 수신
  → CurrentSnapshot 저장
  → SnapshotUpdated event 발행
```

Unity는 앱 시작·복귀 시 `/health`를 즉시 확인하고, 활성 상태에서는 30초 주기로 `/health`를 확인해 연결 상태와 latency를 화면에 표시한다.

### 6.2 요청 종류

| Unity 요청 | Backend API | 용도 |
|---|---|---|
| 세션 생성 | `POST /api/v1/sessions` | 초기 세계 상태 생성 |
| Snapshot 조회 | `GET /api/v1/sessions/{id}` | 전체 상태 재동기화 |
| NPC 대화 | `POST /api/v1/sessions/{id}/actions` | 자연어 Intent/Decision 처리 |
| 위치 이동 | `POST /api/v1/sessions/{id}/actions` | 검증된 move hint 처리 |
| 월드 액션 | `POST /api/v1/sessions/{id}/game-actions` | 서버 발급 Action ID 실행 |
| 세션 초기화 | `POST /api/v1/sessions/{id}/reset` | 초기 상태 복구 |

### 6.3 Unity Client 요청 제어

`OfficeBackendClient`는 다음 규칙을 가진다.

- 한 번에 하나의 요청만 처리하는 `requestInFlight` gate
- 이동 요청에 version을 부여해 오래된 위치 응답이 최신 위치를 덮어쓰지 않도록 제어
- DTO가 잘못되거나 HTTP 요청이 실패하면 World State를 변경하지 않고 오류 callback 호출
- 서버 응답 Snapshot을 적용한 뒤 `SnapshotUpdated` event 발행
- UI, 인벤토리, 월드 Presenter가 Backend Client에 직접 결합하지 않고 event를 구독

### 6.4 Snapshot 적용

`OfficeWorldObjectStatePresenter`는 서버 Snapshot을 Unity 표현으로 변환한다.

```text
SnapshotUpdated
  ├─ World Object 상태/위치 갱신
  ├─ Player 소지품 Sprite 생성/제거
  ├─ 파괴 상태 변화 감지 및 파손 효과
  ├─ NPC 감정 라벨 갱신
  ├─ afraid/shocked 좌우 떨림 연출
  └─ comatose 쓰러짐 연출
```

투척은 서버 성공 전에 `PrepareThrow`로 시각 자료만 준비하고, 서버 성공 후 `ConfirmThrow`에서 투사체를 생성한다. 서버가 차단하거나 통신에 실패하면 `CancelThrow`로 로컬 연출을 취소한다.

이 순서로 Unity 화면이 서버보다 먼저 성공 상태를 확정하는 문제를 막는다.

## 7. 배포 및 운영 구조

```text
Vercel Production Frontend
        │ HTTPS / CORS
        ▼
AWS
  └─ Docker Compose
       └─ FastAPI Backend
            └─ Nginx path routing
```

배포 구성은 다음과 같이 역할을 분리했다.

- React/Vite Frontend는 Vercel Production에 배포
- FastAPI Backend는 AWS 환경에서 Docker Compose로 운영
- Nginx가 외부 HTTPS 요청을 내부 FastAPI 서비스로 전달
- `/health`와 FastAPI API Docs를 사용해 배포 상태와 API 응답을 확인
- Backend 재배포는 Compose 기반 재빌드·재시작 절차로 표준화
- Frontend Production에서 세션 생성, 대화, Game Action까지 실제 흐름을 검증

운영 환경 설정과 비밀값은 Repository에 포함하지 않고 배포 환경 변수로 분리한다. 공개 포트폴리오 문서에는 접속 IP, SSH 경로, 내부 포트, 환경 변수 파일 위치와 같은 운영 보안 정보는 기록하지 않는다.

## 8. Web Frontend 역할

React 프론트엔드는 Unity 본 구현 전에 다음을 빠르게 검증하기 위해 개발했다.

- 세션/대화/Game Action API 계약
- NPC State와 Relationship Inspector
- Event/Trace/Fallback 시각화
- 인벤토리와 월드 오브젝트 상태
- 2D 이동·근접 상호작용·투척·혼수상태의 브라우저 프로토타입

운영 제품의 주 클라이언트는 Unity이며, Web Frontend는 API와 게임 규칙을 먼저 검증한 기능 프로토타입이다.

## 9. 상태 저장과 검증

- `SessionRepository` Protocol로 Memory/SQLite 구현 분리
- Pydantic JSON serialization으로 전체 세션 저장
- 세션 schema version과 migration으로 모델 확장 대응
- OpenAPI 계약 테스트로 Snapshot 필수 필드 검증
- Provider 정상/실패/잘못된 응답 시나리오 테스트
- 관계 정책, 증거 grounding, 월드 액션, 저장 복원 테스트
- 현재 Backend Pytest 69개 통과
- Frontend TypeScript build 및 실제 브라우저 interaction 검증
- Unity 컴파일/Play Mode에서 API 통신과 시각 상태 검증

## 10. 운영 확장 시 고려사항

- Unity Backend URL과 API Key를 환경별 설정으로 분리
- SQLite를 managed Postgres로 전환
- 인증/사용자별 Session ownership 추가
- Optimistic concurrency 또는 session version 적용
- 장시간 AI 요청의 queue/streaming 적용
- Trace/latency/token usage observability 추가
- Unity EditMode/PlayMode 자동화 테스트 확대

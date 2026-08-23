# 대화 의도 분류 및 하드코딩 제거 리팩토링 계획서

## 1. 문서 목적

현재 대화 시스템은 AI provider와 deterministic 키워드·문구 분기가 혼합되어 있다. 이 구조는 예측 가능한 fallback에는 유리하지만, 표현이 조금만 달라져도 의도를 놓치거나 같은 증거를 다시 요청하는 문제가 발생한다.

이 계획서는 자연어 의도 파악은 AI가 담당하고, 게임 규칙과 사실 검증은 백엔드가 담당하도록 역할을 재분리하는 것을 목표로 한다.

## 2. 현재 문제

### 자연어 의도에 직접 의존하는 하드코딩

- `증거`, `오류`, `에러명`, `무슨 이슈` 등의 키워드로 증거 요청 여부를 판단한다.
- `이게`, `이거`, `무슨 뜻`, `설명해` 등의 목록으로 증거 후속 질문을 판단한다.
- `책임자`, `담당자`, `누구에게 물어` 등의 목록으로 책임자 질문을 판단한다.
- `API`, `스키마`, `일정`, `QA` 등의 단어로 증거 종류를 추정한다.
- NPC별 대화 응답과 증거별 설명 문장이 provider 코드에 직접 들어 있다.

### 발생한 문제

- 같은 의미의 다른 표현을 인식하지 못할 수 있다.
- 증거를 이미 확보했는데 다시 증거를 요청하라고 안내할 수 있다.
- AI provider와 deterministic provider의 응답 방향이 달라질 수 있다.
- `Team Lead`처럼 실제 NPC가 아닌 역할을 대화 대상으로 안내할 수 있다.
- 새로운 증거나 NPC를 추가할 때 분기 코드를 여러 곳 수정해야 한다.

## 3. 리팩토링 원칙

### AI가 담당할 영역

- 사용자의 자연어 의도 분류
- 질문 유형 분류
- 현재 대화에서 가리키는 대상 NPC와 증거 추론
- 다양한 표현의 의미 통합
- 사실에 근거한 자연스러운 답변 생성

### 백엔드가 고정해야 할 영역

- NPC·증거·아이템·위치의 실제 존재 여부
- 증거 확보 여부와 공개 권한
- canonical fact와 사건 타임라인
- NPC별 책임 범위와 대화 가능 여부
- 관계 수치, 물리 효과, 사건 상태 변경
- AI가 미확보 증거나 존재하지 않는 NPC를 참조하지 못하도록 하는 검증

자연어 문장을 하드코딩하지 않되, 게임 세계의 사실과 보안·무결성 규칙은 서버에서 계속 고정 관리한다.

## 4. 목표 아키텍처

```text
Unity / Web Client
        ↓ 자연어 입력
Intent Agent
        ↓ 구조화된 Intent
Backend Intent Resolver
        ↓ 대상·증거·권한 검증
NPC Decision Agent
        ↓ 자연어 응답 후보
Backend Guardrail
        ↓ 사실·정책·상태 검증
Unity / Web Snapshot + Dialogue Event
```

## 5. 구조화된 Intent 계약

기존 `ask`, `request_evidence` 등의 의도는 유지하되, 질문의 의미와 참조 대상을 별도 필드로 확장한다.

```json
{
  "intent": "ask",
  "question_type": "evidence_followup",
  "target_npc_id": "qa_01",
  "evidence_id": "qa_warning_message",
  "reference_scope": "latest_discovered",
  "confidence": 0.96
}
```

권장 `question_type`:

- `general_status`: 현재 상태·진행 상황 질문
- `cause_analysis`: 장애 원인 질문
- `evidence_request`: 아직 공개하지 않은 증거 요청
- `evidence_followup`: 이미 확보한 증거의 의미·내용 질문
- `responsibility_routing`: 담당자·책임자·문의 대상 질문
- `approval_process`: 일정·승인·배포 절차 질문
- `relationship_action`: 사과·옹호·비난 등 사회적 행동

`reference_scope`는 다음 값을 사용할 수 있다.

- `explicit`: 사용자가 증거를 직접 지목함
- `latest_discovered`: 현재 대화에서 가장 최근에 확보한 증거
- `conversation_context`: 최근 대화 이벤트에서 가리키는 증거
- `none`: 증거를 가리키지 않음

## 6. 증거 후속 질문 처리

1. Intent Agent가 질문을 `evidence_followup`으로 분류한다.
2. 백엔드가 `evidence_id`를 현재 세션의 확보 증거와 대조한다.
3. 확보된 증거가 아니면 내용을 공개하지 않고 증거 요청 절차로 안내한다.
4. 확보된 증거라면 해당 증거의 제목·원문·관련 canonical fact만 Decision Agent에 제공한다.
5. Decision Agent가 자연스러운 설명을 생성한다.
6. 백엔드는 증거에 없는 사실, 다른 증거, 미확보 정보를 응답에서 차단한다.
7. AI provider 오류나 guardrail 실패 시에만 증거별 deterministic fallback을 사용한다.

기존 `DISCOVERED_EVIDENCE_FOLLOWUP_TERMS`는 엔진의 주 경로에서 제거한다. 오프라인·provider 오류 상황의 `DeterministicIntentProvider`만 최소 키워드 fallback을 유지한다.

## 7. 책임자·담당자 라우팅

책임자 질문은 키워드 목록이 아니라 역할 관계 데이터와 Intent Agent의 `responsibility_routing` 결과로 처리한다.

| 책임 범위 | 담당 역할 | 실제 대화 대상 |
|---|---|---|
| Production 배포 실행 | Backend Developer | Backend Developer |
| API 계약·응답 스키마 변경 | Backend Developer | Backend Developer |
| 일정 단축·일정 압박 | PM / Planner | PM / Planner |
| 배포 전 검증·차단 경고 | QA Engineer | QA Engineer |
| 최종 승인 확인 누락 | 배경 사건 사실 | PM / Planner에게 경위 확인 |

`Team Lead`는 현재 실제 NPC가 아니므로 Intent Agent와 Decision Agent 모두 플레이어에게 찾거나 연락하라고 안내하지 않는다.

모든 NPC는 최소한 Backend의 배포·API 담당, PM의 일정 담당, QA의 검증 담당이라는 공통 책임 매핑을 known facts로 보유한다. NPC별 개인적인 사건 지식은 별도로 유지하되, 담당자 질문에 필요한 책임 매핑은 특정 NPC만 알고 있는 정보로 취급하지 않는다.

## 8. NPC Decision Agent 입력과 검증

Decision Agent에는 다음 컨텍스트를 제공한다.

- 현재 대상 NPC와 역할
- 질문 유형
- 확보된 증거와 증거 출처
- 관련 canonical fact
- 현재 NPC의 known facts와 beliefs
- 대화 이력에서 공개 가능한 내용
- 서버가 허용한 책임자 라우팅 정보

백엔드 guardrail은 다음을 확인한다.

- 미확보 증거 원문을 공개하지 않았는가
- 존재하지 않는 NPC를 대화 대상으로 안내하지 않았는가
- 배포 실행·API 변경 등 canonical fact를 부정하지 않았는가
- 증거에 없는 원인·인물·결과를 추가하지 않았는가
- AI가 관계 수치나 월드 상태를 직접 변경하려 하지 않았는가

## 9. 단계별 진행 계획

### Phase 1. 계약 및 데이터 모델 정리

- [x] `question_type`, `reference_scope` 필드 추가
- [x] Intent·Decision 모델의 허용 값 정의
- [x] 증거·역할·NPC 참조 관계를 서버 registry로 정리
- [x] Unity와 Web의 타입 정의 동기화

### Phase 2. Intent Agent 전환

- [x] 자연어 의미 기반 Intent prompt 작성
- [x] `evidence_followup`과 `responsibility_routing` 분류 추가
- [x] 최근 이벤트와 확보 증거 목록을 Intent 컨텍스트에 제공
- [x] deterministic keyword 분류를 provider 실패·오프라인 fallback으로 축소

### Phase 3. 참조 대상 Resolver 구현

- [x] 명시적 증거 ID 검증
- [x] `latest_discovered` 증거 해석
- [x] 최근 대화에서 증거 참조 대상 해석
- [x] 미확보 증거 참조 차단
- [x] 실제 존재하지 않는 NPC 라우팅 차단

### Phase 4. 자연어 응답 생성 및 Guardrail 정리

- [x] 증거별 고정 설명 문장을 기본 경로에서 제거
- [x] Decision Agent가 증거 기반 설명을 생성하도록 변경
- [x] 사실 모순·증거 누출·없는 NPC 언급 guardrail 유지
- [x] deterministic 문장은 provider 오류·오프라인 모드에서만 사용

### Phase 5. 클라이언트 호환성 확인

- [x] Unity 응답 DTO에 question type과 증거 참조 필드 반영
- [x] Web이 새 fallback stage와 question type을 처리하도록 타입 반영
- [x] 증거 확보·증거 제시·후속 질문 이벤트 중복 여부 확인
- [x] 대화 중 이동 잠금과 응답 대기 UI 유지 확인

### Phase 6. 테스트 및 전환

- [x] 같은 의미의 다양한 한국어 표현 테스트
- [x] 이미 확보한 증거 후속 질문 테스트
- [x] 미확보 증거 공개 차단 테스트
- [x] QA·Backend·PM 책임자 라우팅 테스트
- [x] Team Lead 안내 차단 테스트
- [x] AI가 배포 사실을 부정하는 응답의 fallback 테스트
- [x] OpenAI structured provider와 deterministic fallback 계약 비교

## 10. 테스트 예시

다음 표현들은 같은 의도로 분류되어야 한다.

```text
이게 뭐야?
이 경고는 무슨 뜻이야?
이 메시지가 왜 중요한데?
이 증거가 장애와 어떤 관계야?
자세히 설명해 줘.
```

책임자 질문도 표현과 관계없이 같은 라우팅 결과를 가져야 한다.

```text
누가 배포했어?
배포 담당자가 누구야?
이건 누구에게 확인해야 해?
실제 책임 범위가 어떻게 돼?
```

## 11. 배포 및 롤백 계획

1. 로컬 deterministic provider로 계약과 guardrail을 검증한다.
2. OpenAI provider를 사용해 다양한 표현의 Intent 분류를 검증한다.
3. 백엔드 테스트와 Web build를 통과시킨다.
4. AWS에 백엔드를 먼저 배포한다.
5. Unity를 Local·Remote 양쪽에서 새 세션으로 검증한다.
6. Web Production에서 증거 확보·후속 질문·책임자 라우팅을 검증한다.
7. 문제가 있으면 provider 설정 또는 Intent schema를 이전 버전으로 되돌린다.

프론트엔드와 Unity는 백엔드 계약이 호환되는 동안 별도 로직 롤백 없이 이전 응답을 계속 표시할 수 있어야 한다.

## 12. 완료 기준

- 사용자가 표현을 바꿔도 같은 의도로 분류된다.
- 확보한 증거를 다시 요청하라는 반복 응답이 발생하지 않는다.
- 미확보 증거는 자연어 표현과 무관하게 공개되지 않는다.
- 실제 NPC가 아닌 Team Lead를 찾아가라는 안내가 발생하지 않는다.
- Backend·PM·QA의 책임 범위가 자연어 질문에 따라 올바르게 안내된다.
- AI 응답이 canonical fact를 부정하면 서버 fallback이 적용된다.
- Unity와 Web이 동일한 백엔드 상태와 대화 결과를 표시한다.
- 전체 자동 테스트와 Production smoke test가 통과한다.

## 13. 구현 결과 (2026-08-24)

- 엔진의 자연어 직접 분류 함수와 증거별 기본 설명 map을 제거했다.
- CLI/OpenAI Intent Agent가 `question_type`, `reference_scope`, `evidence_id`를 구조화해 반환한다.
- 최근 대화 이벤트와 최신 확보 증거를 Intent Agent 컨텍스트에 제공한다.
- 백엔드 resolver가 명시적·최신·대화 맥락 증거 참조를 서버 상태와 대조한다.
- 확보된 증거만 Decision Agent에 원문을 전달하고, 일반 대화의 증거 누출은 차단한다.
- 책임자 질문은 공통 responsibility fact를 근거로 답하고, 실제 NPC가 아닌 Team Lead 안내를 차단한다.
- deterministic 키워드와 고정 설명은 provider 오류·오프라인 모드 fallback에만 남겼다.
- 실제 CLI smoke test에서 “릴리스의 오너십 구조”를 `responsibility_routing`으로, “방금 공개된 기록의 함의”를 `evidence_followup`으로 분류하는 것을 확인했다.

# Frontend

React + TypeScript + Vite 기반의 MUD/Text Game 스타일 UI입니다.

```bash
npm install
npm run dev
```

Backend가 `127.0.0.1:8000`에서 실행 중이어야 하며, Vite 개발 서버가 `/api` 요청을 backend로 proxy합니다.

## Game View 대화

NPC에게 가까이 간 뒤 E → 대화하기를 선택하면 게임 화면 위에 대화창이 열립니다. 이동은 잠기며, 다른 NPC 탭은 기록 조회만 가능합니다. 닫아도 기록과 진행 중인 응답은 해당 NPC에 남습니다. 전체 화면 DIALOGUE 모드는 상단 버튼으로 별도 선택할 수 있습니다.

E·I·WASD는 입력 언어와 무관한 물리 키(`KeyboardEvent.code`)를 사용합니다. 대화 입력 중에는 게임 단축키를 처리하지 않으며, 한글 IME 조합 확정 Enter와 전송 Enter를 구분합니다. 작은 화면의 방향 버튼은 누르고 이동하거나 짧게 클릭할 수 있고 키보드로도 활성화할 수 있습니다.

새로 발견한 증거는 출처 NPC의 기록에 한 번 표시됩니다. 확보한 증거 제시 버튼은 실제 대화 상대에게만 전송합니다. 409 충돌은 최신 상태를 조회한 뒤 알리며 자동 재전송하지 않습니다. 클라이언트 대기 제한은 120초이고, 시간 초과가 서버 처리 취소를 의미하지는 않습니다.

## 검증

```bash
npm test
npm run build
```

Vitest·React Testing Library·jsdom으로 대화 상태, 입력 포커스, IME 이벤트, 세션 충돌, 늦은 응답과 Game View 통합을 검사합니다. 이 테스트는 실제 OS 한글 입력기의 수동 검증이나 Unity 실행 화면의 시각 비교를 대신하지 않습니다.

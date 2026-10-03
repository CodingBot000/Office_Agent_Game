# Office Agent 운영 배포

기준일: 2026-10-04 KST. AWS에서 GCP로 이관 완료했으며 AWS `spring-api` 인스턴스와 Static IP는 삭제됐다. 재배포는 아래 GCP·Vercel 구성만 사용한다. 이전 Nginx 설정 파일은 이관 전 참고 자료이며 운영 배포에 사용하지 않는다.

## 운영 구성

- Frontend: Vercel `office-agent-frontend`, https://office-agent-frontend.vercel.app
- Production 환경변수: `VITE_API_BASE_URL=https://api.heartsignal.cloud`
- API: https://api.heartsignal.cloud
- GCP project: `nexuslink-490118`
- Cloud Run: `office-agent-api`, region `asia-northeast3`
- Firebase Hosting: `nexuslink-office-prod.web.app`, 모든 경로를 Cloud Run으로 전달
- Hosting 설정: `/Users/switch/Development/Web/aws-to-gcp-migration/deploy/firebase-office-prod/firebase.json`
- 중앙 이관 기록: `/Users/switch/Development/Web/aws-to-gcp-migration/docs/spring_api_instance_migration_runbook.md`
- 환경변수 원본: `/Users/switch/Development/Web/aws-to-gcp-migration/deploy/cloud-run/env/office-agent-api.yaml`

Firebase rewrite는 접두사를 제거하지 않는다. `/office-agent-backend`를 API 주소에 붙이면 404가 발생한다. `frontend/src/api.ts`는 base URL에 `/api/v1/...`를 붙인다.

현재 provider는 `deterministic-mock`, 세션 저장소는 `memory`이다. 이 작업은 두 설정을 변경하지 않는다. 메모리 세션은 인스턴스 재시작·교체 시 유지되지 않는다. 소스/이미지를 갱신할 때도 중앙 Office Agent 설정과 현재 서비스를 먼저 확인한다. Franchise Community 배포 가이드를 그대로 적용하지 않는다.

## CORS만 갱신하는 Cloud Run 재배포

현재 운영 이미지를 유지하고 CORS만 변경한다. 다른 환경변수와 Secret 연결은 유지된다.

```bash
gcloud run services update office-agent-api \
  --project nexuslink-490118 \
  --region asia-northeast3 \
  --update-env-vars '^|^CORS_ORIGINS=https://heartsignal.cloud,https://www.heartsignal.cloud,https://office-agent-frontend.vercel.app' \
  --quiet
```

환경변수 원본 YAML에도 동일한 CORS 목록을 기록한다. Firebase 설정은 변경하지 않는다.

## Vercel Production 재배포

`frontend/.vercel/project.json`의 연결 프로젝트가 `office-agent-frontend`인지 확인하고 실행한다. 환경변수 변경만으로 기존 번들은 바뀌지 않으므로 재빌드가 필요하다. 아래 명령은 기존 운영 소스를 재빌드한다.

```bash
cd /Users/switch/Development/Web/Office_Agent_MVP/frontend
vercel env update VITE_API_BASE_URL production --value https://api.heartsignal.cloud --yes
vercel redeploy https://office-agent-frontend.vercel.app --target production
```

## 운영 검증

```bash
curl -fsS https://api.heartsignal.cloud/health
curl -fsS -o /dev/null https://api.heartsignal.cloud/docs
curl -i -X OPTIONS https://api.heartsignal.cloud/api/v1/sessions \
  -H 'Origin: https://office-agent-frontend.vercel.app' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type'
```

모두 HTTP 200이어야 하며 OPTIONS 응답의 `Access-Control-Allow-Origin`은 `https://office-agent-frontend.vercel.app`이어야 한다. Production 브라우저에서 세션 생성, 세션 조회 API, 액션 호출 및 상태 갱신을 확인한다.

## 2026-10-04 배포 기록

- Cloud Run Ready revision: `office-agent-api-00003-db6`, 트래픽 100%.
- 기존 운영 이미지 유지, CORS에 Vercel Production origin 추가.
- `/health`, `/docs`, 세션 OPTIONS 검증: HTTP 200, origin 헤더 일치.
- Vercel Production Ready: `dpl_HiACKhRASKAJpmvZiDtq5XGegz83`, 기본 Production 도메인 alias 확인.
- 운영 JS 번들: 올바른 API base URL 확인, 이전 접두사 없음.
- Production 브라우저: 세션 생성 성공, QA 질문 액션 응답 및 TURN 01 갱신 확인.
- 동일 origin 헤더로 세션 생성 201·조회 200 및 세션 ID 일치 확인. UI는 새로고침 때 새 세션을 생성하므로 조회 API는 별도로 검증했다.

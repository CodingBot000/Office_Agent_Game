# Office Agent Backend 배포 정보

작성일: 2026-08-23
배포 대상: AWS Lightsail `spring-api`
배포 상태: 운영 배포 완료

## 1. 외부 접속 정보

```text
Base URL:
https://api.heartsignal.cloud/office-agent-backend/

Health:
https://api.heartsignal.cloud/office-agent-backend/health

API Docs:
https://api.heartsignal.cloud/office-agent-backend/docs
```

기존 Tarot 서비스가 사용하는 `api.heartsignal.cloud`에 path routing으로 추가했다. 별도 Cloudflare 도메인이나 DNS 레코드는 만들지 않았다.

## 2. AWS 인스턴스

```text
서비스: AWS Lightsail
인스턴스: spring-api
리전: us-west-2
가용 영역: us-west-2a
Public IP: 184.33.100.84
사양: 2 vCPU / 2GB RAM / 60GB SSD
```

배포 직후 확인값:

```text
RAM available: 약 708MB
Disk available: 약 37GB
```

## 3. Tarot 서비스 보존 구성

기존 Tarot 서비스는 다음 컨테이너로 유지된다.

```text
sajutok-web
  127.0.0.1:3000 -> container:80

sajutok-spring-service
  127.0.0.1:8080 -> container:8080
```

기존 `api.heartsignal.cloud/` 라우팅은 `127.0.0.1:8080`으로 유지하고, Office Agent path만 별도 Backend로 전달한다.

## 4. Office Agent Backend 원격 구성

```text
원격 소스 디렉터리:
/srv/office-agent-backend

Compose 파일:
/srv/office-agent-backend/compose.office-agent.yml

Production env:
/srv/office-agent-backend/.env.production

SQLite 데이터:
/srv/office-agent-backend/data/office_agent.db

Container:
office-agent-backend

Internal port:
127.0.0.1:8001 -> container:8001
```

컨테이너 설정:

```text
AI_PROVIDER=openai
OPENAI_MODEL=gpt-5.4-nano
SESSION_STORAGE=sqlite
```

`OPENAI_API_KEY`는 원격 `.env.production`에만 저장하며 Repository에는 포함하지 않는다. 원격 파일 권한은 `600`이다.

## 5. Nginx Path Routing

추적 가능한 설정 파일:

[api-heartsignal-office-agent-location.conf](/Users/switch/Development/Web/Office_Agent_MVP/deploy/nginx/api-heartsignal-office-agent-location.conf)

원격 설치 위치:

```text
/etc/nginx/snippets/office-agent-backend-location.conf
```

라우팅:

```text
/office-agent-backend
  -> /office-agent-backend/

/office-agent-backend/*
  -> http://127.0.0.1:8001/*
```

Nginx 설정 변경 전 백업:

```text
/var/backups/office-agent-nginx-default.before
```

## 6. 배포·재시작 명령

SSH 접속:

```bash
ssh -i ~/.ssh/lightsail-key-oregon.pem ubuntu@184.33.100.84
```

Office Agent만 재빌드·재시작:

```bash
cd /srv/office-agent-backend
docker compose -f compose.office-agent.yml up -d --build
```

Office Agent만 중지:

```bash
cd /srv/office-agent-backend
docker compose -f compose.office-agent.yml down
```

Tarot 컨테이너를 중지하지 않도록 반드시 Compose 파일을 명시한다.

## 7. 배포 확인

원격 내부 확인:

```bash
curl -fsS http://127.0.0.1:8001/health
docker ps
docker logs --tail 100 office-agent-backend
```

외부 확인:

```bash
curl -fsS https://api.heartsignal.cloud/office-agent-backend/health
curl -fsS -o /dev/null -w "%{http_code}\n" \
  https://api.heartsignal.cloud/office-agent-backend/docs
```

실제 배포 검증 결과:

```text
Health: HTTP 200
Docs: HTTP 200
OpenAI IntentClassification: 정상
AI_PROVIDER: openai
AI_MODEL: gpt-5.4-nano
Fallback: 0건
Tarot containers: 계속 실행 중
```

## 8. 배포 소스 및 커밋

배포 설정 커밋:

- `3c8c823 chore: add office agent backend deployment config`
- `77882ab chore: add office agent path proxy config`

현재 Repository `main`과 `origin/main`은 `77882ab`로 동기화되어 있다.

## 9. 주의사항

- `api.heartsignal.cloud`의 기존 Tarot 루트 라우팅을 수정하지 않는다.
- `8001` 포트는 외부에 공개하지 않고 localhost만 사용한다.
- `.env.production`을 Git에 추가하지 않는다.
- Backend 재배포 시 `docker compose -f compose.office-agent.yml`만 사용한다.
- Nginx 설정 변경 후 반드시 `sudo nginx -t`를 먼저 실행한다.
- 현재 Lightsail 인스턴스는 2GB RAM이므로 메모리 사용량을 주기적으로 확인한다.
- SQLite는 단일 플레이 기준이며 동일 세션 동시 요청에는 마지막 write 문제가 남아 있다.

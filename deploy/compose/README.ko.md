# 배포 (Docker Compose) — **소스에서 빌드해 올리는 쪽**

[English](README.md) · **한국어**

> 그냥 깔고 싶으면 여기가 아니다. **[`deploy/install/`](../install/README.ko.md)** 이
> 판을 붙여 낸 이미지를 당겨서 한 줄로 세운다. 이 디렉터리는 **소스를 고쳐
> 자기 이미지로 올리는** 사람을 위한 것이다.

`docker-compose.yml`(루트)이 개발용, 여기 파일들이 운영 오버라이드다.

```bash
docker compose -f docker-compose.yml -f deploy/compose/prod.yml up -d
```

이 경로는 **마이그레이션을 대신 돌려 주지 않는다.** 띄운 뒤에 한 번 친다:

```bash
docker compose -f docker-compose.yml -f deploy/compose/prod.yml \
    run --rm api alembic -c alembic.ini upgrade head
```

| 파일 | 용도 |
|---|---|
| `postgres-init/01-extensions.sql` | 최초 기동 시 확장 설치 (citext, pg_trgm, ltree, pgroonga) |
| `prod.yml` | 운영 오버라이드: 코드 마운트 제거, 리로드 끄기, 레플리카 |

## 운영에서 반드시 넣어야 하는 값

없으면 `docker compose` 가 **기동 전에** 멈춘다. 조용히 잘못된 기본값으로
도는 것보다 낫다.

| 변수 | 뜻 | 예 |
|---|---|---|
| `IEUM_SECRET_KEY` | 애플리케이션 암호화 키 파생의 뿌리 | `make secret` |
| `IEUM_BASE_URL` | 사람이 브라우저에 치는 앱 주소. 메일 안의 링크와 SSO 의 돌아올 주소 | `https://ieum.example.com` |
| `IEUM_S3_PUBLIC_ENDPOINT_URL` | **브라우저가** 볼 스토리지 주소 | `https://files.example.com` |
| `IEUM_WEB_ORIGINS` | API·스토리지가 허용할 웹 오리진(CORS) | `https://ieum.example.com` |
| `IEUM_DB_PASSWORD` | Postgres 비밀번호 (개발 기본값 `postgres` 를 안 물려받는다) | `make secret` |
| `IEUM_S3_ACCESS_KEY`·`IEUM_S3_SECRET_KEY` | 번들 MinIO 의 열쇠 (`minioadmin` 을 안 물려받는다) | |
| `IEUM_SMTP_HOST` | 메일 서버. **비워 두려면 `IEUM_SMTP_HOST=` 로 적는다**(안 보낸다는 뜻) | `smtp.example.com` |

메일은 `IEUM_SMTP_PORT`(기본 587)·`IEUM_SMTP_TLS`(기본 true)·`IEUM_SMTP_USER`·
`IEUM_SMTP_PASSWORD`·`IEUM_MAIL_FROM` 도 받는다. 밑바탕(`docker-compose.yml`)은
개발용 mailpit 을 박아 두는데, 운영 오버라이드가 그것을 덮지 않던 동안 운영의
초대·알림 메일이 전부 mailpit 의 상자로 들어가 아무에게도 안 갔다 — 오류도
없었다. 그래서 이제는 **안 적으면 기동 전에 멈춘다.** `.env.example` 을 베껴
`.env` 를 만들었다면 그 안의 `IEUM_SMTP_HOST=localhost` 를 꼭 고친다.

운영 오버라이드가 **일부러 안 받는 것**도 있다:

- `IEUM_API_URL` 은 빈 값으로 박는다. 이 스택은 api 의 포트를 닫으므로 API 는
  화면과 같은 오리진으로만 나가고, 비우면 앱이 `IEUM_BASE_URL` 을 쓴다.
- `SEED_MFA_ADMIN_*` 은 빈 값으로 박는다. 개발·E2E 용 2FA 관리자라 비밀번호와
  TOTP 시크릿이 저장소에 적혀 있다. 운영에 넘어가면 시드를 한 번 돌리는 순간
  **누구나 아는 2FA 관리자**가 생긴다. 시드도 `IEUM_ENV=production` 에서는 이
  계정을 만들지 않는다.

> **이미 이 경로로 운영 중이었다면** `mfa-admin@example.com` 계정이 있는지
> 본다. 예전 오버라이드로 시드를 돌렸다면 만들어져 있다 — 비활성화하고
> 감사 기록을 확인한다.

`IEUM_S3_PUBLIC_ENDPOINT_URL` 이 왜 따로 있나: 첨부는 브라우저가 스토리지로
**직접** 올린다(서버는 바이트를 경유하지 않는다). 컨테이너 안에서 보는 주소
(`http://minio:9000`)로 presigned URL 을 서명하면 브라우저는 그 호스트를
못 찾는다. 서버용과 브라우저용 주소를 따로 준다.

MinIO 의 CORS 는 `mc cors set` 으로 못 바꾼다 — MinIO 가 S3 의 CORS API 를
구현하지 않는다. `MINIO_API_CORS_ALLOW_ORIGIN` 환경변수가 유일한 경로다.

여러 대로 늘리려면 Helm 차트(`deploy/helm/ieum/`)를 본다. 한 노드면
Compose 로 충분하다 — 그것이 이 프로젝트의 1급 배포 경로다.

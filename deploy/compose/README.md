# 배포 (Docker Compose) — **소스에서 빌드해 올리는 쪽**

> 그냥 깔고 싶으면 여기가 아니다. **[`deploy/install/`](../install/)** 이
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
| `IEUM_S3_PUBLIC_ENDPOINT_URL` | **브라우저가** 볼 스토리지 주소 | `https://files.example.com` |
| `IEUM_WEB_ORIGINS` | 스토리지가 허용할 웹 오리진(CORS) | `https://ieum.example.com` |

`IEUM_S3_PUBLIC_ENDPOINT_URL` 이 왜 따로 있나: 첨부는 브라우저가 스토리지로
**직접** 올린다(서버는 바이트를 경유하지 않는다). 컨테이너 안에서 보는 주소
(`http://minio:9000`)로 presigned URL 을 서명하면 브라우저는 그 호스트를
못 찾는다. 서버용과 브라우저용 주소를 따로 준다.

MinIO 의 CORS 는 `mc cors set` 으로 못 바꾼다 — MinIO 가 S3 의 CORS API 를
구현하지 않는다. `MINIO_API_CORS_ALLOW_ORIGIN` 환경변수가 유일한 경로다.

여러 대로 늘리려면 Helm 차트(`deploy/helm/ieum/`)를 본다. 한 노드면
Compose 로 충분하다 — 그것이 이 프로젝트의 1급 배포 경로다(D-11).

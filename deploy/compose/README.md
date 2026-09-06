# 배포 (Docker Compose)

`docker-compose.yml`(루트)이 개발용, 여기 파일들이 운영 오버라이드다.

```bash
docker compose -f docker-compose.yml -f deploy/compose/prod.yml up -d
```

| 파일 | 용도 |
|---|---|
| `postgres-init/01-extensions.sql` | 최초 기동 시 확장 설치 (citext, pg_trgm, ltree, pgroonga) |
| `prod.yml` | 운영 오버라이드: 코드 마운트 제거, 리로드 끄기, 레플리카 |

Helm 차트는 M6. 그 전까지 Compose 가 유일한 공식 배포 경로다.

# 이음 Helm 차트

```sh
helm install ieum ./deploy/helm/ieum \
  --set config.webOrigins=https://ieum.example.com \
  --set config.s3PublicEndpointUrl=https://files.example.com \
  --set secrets.existingSecret=ieum-secrets
```

## 이 차트가 만들지 않는 것

**Postgres·Redis·S3·SMTP·OpenSearch 를 만들지 않는다.** 주소만 받는다. 이유가 셋이다.

1. **`helm uninstall` 이 데이터를 지운다.** 차트가 데이터베이스를 소유하면
   릴리스를 지우는 것과 데이터를 지우는 것이 같은 명령이 된다. 그 둘은 같은
   무게가 아니다.
2. **운영 DB 는 백업과 이중화를 따로 받아야 한다.** 그것은 차트가 줄 수 있는
   것이 아니고, 줄 수 있는 척하면 안 받고 지나간다.
3. **Postgres 는 PGroonga 확장이 필요하다** (한국어 전문검색).
   흔한 Postgres 차트로는 안 되고, 되는 것처럼 기본값을 두면 검색이 조용히
   안 되는 설치가 생긴다.

OpenSearch 도 같은 이유로 안 만든다. 기본 검색 백엔드는 PGroonga
이므로 **아무것도 더 필요 없다** — `search.backend` 를 `opensearch` 로 바꿀
때만 클러스터 주소를 준다.

개발·시험용으로 한 판 띄우려면 `deploy/compose` 를 쓴다.

## 비밀

**운영에서는 `secrets.existingSecret` 을 쓴다.**

```sh
kubectl create secret generic ieum-secrets \
  --from-literal=IEUM_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=IEUM_DATABASE_URL='postgresql+asyncpg://…' \
  --from-literal=IEUM_REDIS_URL='redis://…'
```

`secrets.secretKey` 처럼 값으로 주는 길도 열어 두었지만, 그것은 개발용이다.
값으로 준 비밀은 릴리스 이력에 남고 `helm get values` 로 되읽힌다 — 그 순간
클러스터 안에 평문으로 한 벌 더 생긴다.

## 스키마는 Job 이 올린다

`pre-install,pre-upgrade` 훅으로 `alembic upgrade head` 를 **한 번** 돌린다.

initContainer 로 두지 않은 이유: 파드 수만큼 동시에 돌아 서로 경쟁한다.
Alembic 이 그것을 막아 주지 않으므로, 운이 나쁘면 같은 리비전을 둘이 적용하려
들다 한쪽이 실패하고 그 파드가 기동 루프에 빠진다.

실패한 Job 은 **남긴다**(`migrate.keepFailed`). 지우면 왜 실패했는지 볼 수
없고, 마이그레이션 실패는 그 로그가 유일한 단서다.

`post-install,post-upgrade` 에서 시드가 돈다. 멱등이라 다시 돌아도 안전하고,
`upgrade` 에서도 도는 이유가 있다 — 새 판이 권한을 하나 더 정의했으면 내장
역할에 그것을 더해 줘야 한다. 안 하면 관리자가 새 화면에서 403 을 본다.

## 워커는 여러 개 띄워도 된다

근거가 셋이고, 셋 다 코드에 있다.

1. 아웃박스 폴링이 `FOR UPDATE SKIP LOCKED` 다 (`core/outbox.py`) — 같은
   이벤트를 두 번 보내지 않는다.
2. 주기 작업(스윕·다이제스트)은 arq 의 `unique` 크론이다. job id 가 실행
   시각으로 정해지므로, 워커 넷이 같은 것을 넣으면 **하나만** 큐에 들어간다.
   다이제스트 메일이 워커 수만큼 나가지 않는 이유가 이것이다.
3. 기동 직후 스윕은 워커마다 한 번 돌 수 있다(`run_at_startup`). 스윕은 15초
   마다 도는 멱등 작업이라 한 번 더 도는 것이 문제가 아니다.

2번은 실제로 확인했다: 워커 둘을 같은 Redis 에 붙여 1분을 돌렸고, 스윕은
한쪽에서만 실행됐다.

## 검색 백엔드를 바꿀 때

```yaml
search:
  backend: opensearch
  opensearch:
    url: https://search.internal:9200
```

주소를 안 주면 **배포가 실패한다.** 안 실패시키면 앱은 멀쩡히 뜨고 검색만
조용히 0건이 되는데, 0건은 "없다" 와 구별되지 않아서 아무도 고장이라고
생각하지 않는다.

**바꾼 뒤 한 번은 `ieum reindex` 를 돌려야 한다.** 켜기 전에 쌓인 것은 미러
큐에 없다. 빼먹으면 옛 문서가 검색에 하나도 안 나오고, 오류는 없다.

되돌리는 것은 `backend: postgres` 하나다 — 색인은 그동안 계속 Postgres 에
쓰이고 있었다.

## 프로브를 나눠 쓴다

- `readinessProbe` → `/readyz`. 붙는 것들(DB·Redis·S3)을 본다.
- `livenessProbe` → `/healthz`. 프로세스만 본다.

둘을 같이 쓰면 안 된다. DB 가 잠깐 흔들릴 때 쿠버네티스가 파드를 계속
재시작해 복구를 방해한다. 지금 모양이면 트래픽만 빠지고 파드는 살아 있다가,
DB 가 돌아오면 그대로 다시 받는다.

워커에는 HTTP 프로브가 없다. 포트를 안 열고, arq 의 건강 신호는 Redis 키다.
죽은 워커는 큐가 밀리는 것으로 드러나고 그 지표는 이미 `/metrics` 에 있다
(`outbox_pending`·`worker_last_run`).

## 어디까지 확인했는가

이 차트로 확인한 것과 못 한 것을 적어 둔다.

확인한 것:

- `helm lint`, 그리고 **실제 쿠버네티스 API 서버**로 매니페스트 검증
  (`kubectl apply --dry-run=server`) — 인그레스를 켠 경우까지.
- 설정 검사가 뜨기 **전에** 막는다: 웹 오리진, 스토리지 공개 주소, 비밀.
- 이미지의 네 명령이 **읽기 전용 루트 파일시스템 + 비루트(uid 10001)** 에서
  실제로 돈다: `alembic upgrade`, `uvicorn`, `arq`, `python -m ieum.cli seed`.
- 프로브가 실제로 응답한다. 스토리지를 끊으면 `/readyz` 가 503 과 함께
  `storage: error` 를 말하고, 붙이면 200 이 된다 — `/healthz` 는 그동안 200 이다.
- 워커 둘을 붙여 크론이 한 번만 도는 것(위).

**확인하지 못한 것: 파드가 실제로 스케줄되어 Ready 가 되는 것.** 이 저장소를
만든 샌드박스에서 k3s 의 runc 가 컨테이너 프로세스를 만들지 못한다
(`can't get final child's PID from pipe`) — 중첩 컨테이너 제약이고 차트의
문제가 아니지만, 그렇다고 확인한 것도 아니다. 진짜 클러스터에서 한 번 올려
보기 전까지 이 차트는 "매니페스트와 이미지가 맞는 것까지 본" 상태다.

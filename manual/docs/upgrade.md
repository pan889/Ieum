# 올리기와 되돌리기

## 올리는 순서

```bash
git pull
docker compose -f docker-compose.yml -f deploy/compose/prod.yml build
docker compose -f docker-compose.yml -f deploy/compose/prod.yml up -d
docker compose exec api alembic upgrade head
docker compose exec api python -m ieum.cli seed      # 멱등합니다
```

`seed` 를 **올릴 때마다** 돌립니다. 새 판이 권한을 하나 더 정의했을 때 내장
역할에 그것을 더해 주는 일이고, 안 돌리면 새 기능이 관리자에게도 403 으로
보입니다.

쿠버네티스에서는 이 셋이 훅 Job 으로 자동입니다 —
`pre-upgrade` 가 마이그레이션, `post-upgrade` 가 시드입니다.

## 마이그레이션을 먼저 보고 싶다면

```bash
docker compose exec api alembic upgrade head --sql > migration.sql
```

큰 표에 인덱스를 더하는 판이라면 이 파일에서 미리 보입니다.

## 편집 중인 문서와 롤링 업데이트

동시 편집 중인 문서는 **프로세스 메모리에** 있고, 3초에 한 번 저장됩니다.
파드를 내릴 때 앱이 열린 방을 닫고 **마지막 저장을 기다립니다** — 그래서
정상적인 종료에서는 편집이 사라지지 않습니다.

갑작스러운 죽음(SIGKILL, 노드 상실)에서는 마지막 스냅샷 이후가 사라집니다.
최대 3초입니다.

## 되돌리기

### 코드만 되돌린다

마이그레이션이 없던 판으로 돌아가는 경우입니다. 이미지 태그를 앞 판으로
바꿔 다시 띄우면 끝입니다.

### 마이그레이션까지 되돌린다

```bash
docker compose exec api alembic downgrade -1
```

!!! warning "열을 지우는 마이그레이션은 되돌리면 데이터가 사라집니다"
    `downgrade` 는 스키마를 되돌리지만 **그 열에 있던 값은 못 되살립니다.**
    열을 지우거나 좁히는 판을 올리기 전에는 백업을 먼저 확인하세요.

### 데이터까지 되돌린다

[운영 4절의 복원 절차](operations.md#4)를 씁니다. Postgres
덤프와 첨부 버킷, 그리고 **`IEUM_SECRET_KEY`** 가 함께 있어야 복원한 것이
열립니다.

## 판 번호 규칙

`MAJOR.MINOR.PATCH` 입니다.

| | |
|---|---|
| PATCH | 고침만. 마이그레이션이 있어도 되돌릴 수 있는 것만 |
| MINOR | 기능 추가. 마이그레이션은 앞 판과 함께 돌 수 있게 만듭니다 |
| MAJOR | 되돌릴 수 없는 변경, 설정 이름 변경, 지원 중단 |

API 는 `/api/v1` 로 판이 붙어 있습니다. MINOR 에서 **필드를 더하는 것**은
하지만 **빼거나 뜻을 바꾸는 것**은 하지 않습니다 — 웹훅을 받는 쪽과
액세스 토큰으로 부르는 스크립트가 그것으로 깨집니다.

각 판이 무엇을 바꿨는지는
[CHANGELOG](https://github.com/pan889/Ieum/blob/main/CHANGELOG.md)에 있습니다.

### 사전 공개 (`-rc1`)

`1.0.0-rc1` 처럼 꼬리가 붙은 것은 **`1.0.0` 을 향하는 후보**입니다.

- 이미지 태그에는 꼬리가 그대로 들어갑니다(`ieum-api:1.0.0-rc1`). 후보가
  정식 판의 이름을 쓰면 무엇이 돌고 있는지 말할 수 없습니다.
- GitHub 에서는 사전 공개로 표시됩니다.
- `apps/api/pyproject.toml` 에는 꼬리를 적지 않습니다 — 거기는 `1.0.0` 이고,
  릴리스 게이트는 **꼬리를 뗀 쪽**을 그 값과 맞춥니다.
- 릴리스 노트는 향하는 판(`1.0.0`)의 CHANGELOG 절을 그대로 씁니다.

**운영에는 사전 공개를 쓰지 마세요.** 되돌리기 절차는 같지만, 후보는 아직
이 문서의 "확인하지 못한 것" 을 줄이는 중입니다.

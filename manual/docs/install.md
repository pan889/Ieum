# 설치하기

!!! warning "치는 순서는 여기가 아니라 코드 저장소에 있습니다"
    이 저장소는 **공개하지 않습니다** — 개발의 핵심 내용과 의사결정 창구입니다.
    깔려는 사람이 읽어야 하는 단계는 코드 저장소의
    [`deploy/install/README.md`](https://github.com/pan889/Ieum/tree/main/deploy/install)
    에 있고, **거기가 정본입니다.** 여기에 옮겨 적지 않습니다 — 두 벌을 두면
    한 벌만 고쳐지는 날이 옵니다.

    이 문서는 **왜 그런 모양인지**와, 코드 저장소에 없는 것(운영·다중 인스턴스·
    관리형 백엔드)을 다룹니다.

한 줄로 섭니다.

```bash
sh install.sh --url https://ieum.example.com
```

물어보는 것은 **주소 하나**입니다. 그 값이 로그인 쿠키·첨부 링크·메일 안의
링크에 그대로 들어갑니다. 나머지(비밀 발급, 이미지 당기기, 스키마, 시드,
건강 대기)는 스크립트가 합니다.

| | |
|---|---|
| Docker | Compose v2 (`docker compose`, 하이픈 없음) |
| 메모리 | 4GB 이상. Postgres·Redis·MinIO·API·워커·웹이 함께 뜹니다 |
| 디스크 | 첨부 용량 + 여유 |

쿠버네티스는 [Helm 차트](https://github.com/pan889/Ieum/tree/main/deploy/helm/ieum)를
쓰고, 그 판단들은 [운영 5절](operations.md)에 있습니다.

## 주소를 하나만 묻는 이유

밖으로 여는 포트가 하나이기 때문입니다. 화면도 API 도 첨부도 같은 오리진으로
나갑니다 — `web` 컨테이너의 nginx 가 `/api/` 는 API 로, 버킷 경로
(`/ieum-attachments/`)는 스토리지로 넘깁니다.

그래서 앞에 TLS 를 세울 때 붙일 곳이 한 군데입니다.

```
https://ieum.example.com  →  127.0.0.1:8080
```

`.env` 에서 `IEUM_HTTP_BIND=127.0.0.1` 로 두면 밖에서 8080 을 직접 못 엽니다.
`IEUM_PUBLIC_URL` 은 **사람이 치는 주소**(`https://…`)여야 합니다.

!!! danger "IEUM_SECRET_KEY 를 잃으면 되돌릴 수 없습니다"
    이 키에서 **저장된 비밀을 푸는 키가 파생됩니다** — 2FA 시크릿, IdP
    클라이언트 시크릿, 웹훅 시크릿, 메일 채널 비밀번호, 저장소 연동 토큰.
    잃으면 그 데이터는 복구할 수 없습니다. `.env` 를 백업에 **함께**
    넣으세요 (백업만 있고 키가 없으면 복원해도 절반이 안 열립니다).

    인스턴스를 여러 개 띄운다면 **모두 같은 키**를 써야 합니다 — 이유는
    [운영 6절](operations.md)에 있습니다.

## 살아 있는지 본다

```bash
curl -s https://ieum.example.com/api/v1/../readyz | python3 -m json.tool
```

컨테이너 안에서 직접 보려면:

```bash
docker compose exec api python -c \
  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/readyz').read().decode())"
```

```json
{
  "status": "ready",
  "checks": { "database": "ok", "redis": "ok", "storage": "ok" }
}
```

`degraded` 가 나오면 어느 줄이 붉은지 보입니다. `/healthz` 는 프로세스만
보므로 로드밸런서의 liveness 에 쓰고, 트래픽 판단은 `/readyz` 로 합니다 —
[둘을 같이 쓰면 안 되는 이유](operations.md#1)가 있습니다.

## 처음에 꼭 하는 것

1. **비밀번호를 바꿉니다** — 스크립트가 찍어 준 것은 첫 열쇠일 뿐입니다.
2. **2FA 를 켭니다** — `설정 → 보안`. 관리자 계정이 비밀번호 하나로 열려
   있는 상태를 길게 두지 않습니다. 조직 전체 강제도 같은 화면입니다.
3. **메일을 켭니다** — `.env` 의 `IEUM_SMTP_*` 가 비어 있으면 초대도 알림도
   **안 나갑니다.** 앱은 멀쩡히 뜨므로 눈에 안 띕니다. 채우고
   `docker compose up -d` 를 다시 칩니다.
4. **백업을 겁니다** — [운영 4절](operations.md#4). Postgres
   볼륨, 첨부 볼륨, 그리고 **`.env`** 셋 다입니다.

## 판 올리기

```bash
cd <설치한 자리> &&./install.sh --version 1.1.0
```

비밀은 다시 만들지 않습니다. `compose.yml` 은 **그 판의 것으로 갈아 끼웁니다**
— 이미지만 새것이 되고 설정이 옛것으로 남으면 몇 달 뒤 엉뚱한 증상이 됩니다.
그래서 `compose.yml` 은 설치본이 소유합니다. 고칠 것이 있으면 옆에
`compose.override.yml` 을 두세요. compose 가 겹쳐 읽고, 스크립트는 그 파일을
건드리지 않습니다.

되돌리는 이야기는 [올리기와 되돌리기](upgrade.md)에 있습니다.

## 소스에서 빌드해 올리기

코드를 고쳐 자기 이미지로 돌린다면 이쪽입니다. 여기서는 주소를 **셋** 줘야
합니다 — 한 줄 설치본이 하나로 줄여 주는 것이 바로 이 셋입니다.

```bash
git clone https://github.com/pan889/Ieum.git && cd Ieum
cp.env.example.env
make secret          # 나온 값을.env 의 IEUM_SECRET_KEY 에
```

```bash
IEUM_BASE_URL=https://ieum.example.com          # 브라우저가 앱을 여는 주소
IEUM_WEB_ORIGINS=https://ieum.example.com       # CORS. 여기 없는 오리진은 막힙니다
IEUM_S3_PUBLIC_ENDPOINT_URL=https://files.example.com   # 브라우저가 첨부를 여는 주소
```

- `IEUM_WEB_ORIGINS` 가 틀리면 브라우저가 API 를 부르는 족족 프리플라이트에서
  막혀 **앱이 통째로 안 뜹니다.** `localhost` 와 `127.0.0.1` 은 브라우저에게
  서로 다른 오리진이니, 둘 다 쓴다면 콤마로 둘 다 넣습니다.
- `IEUM_S3_PUBLIC_ENDPOINT_URL` 이 틀리면 첨부 링크가 **아무도 못 여는
  주소로 계속 발급됩니다.** 서버는 컨테이너 이름으로 스토리지를 부르지만,
  서명된 링크는 브라우저가 엽니다.

셋이 비어 있으면 **기동을 거부합니다.** 조용히 기본값으로 뜨는 것보다 안 뜨는
쪽이 낫기 때문입니다.

```bash
docker compose -f docker-compose.yml -f deploy/compose/prod.yml up -d
docker compose exec api alembic -c alembic.ini upgrade head
docker compose exec api python -m ieum.cli seed
```

`seed` 는 멱등합니다 — 두 번 돌려도 같습니다. 관리자, 내장 역할 여덟 개,
기본 워크플로우, 이슈 종류를 만듭니다. `SEED_MFA_ADMIN_*` 세 줄은 **비워
둡니다** — 그건 자동 테스트용이고, 비어 있으면 시드가 아무것도 만들지 않습니다.

!!! warning "compose 프로젝트 이름이 겹칩니다"
    한 줄 설치본과 저장소의 compose 는 둘 다 프로젝트 이름이 `ieum` 입니다.
    한 기계에서 둘 다 굴리면 한쪽에서 친 `docker compose` 가 **다른 쪽의
    컨테이너를 자기 것으로 알고 치웁니다.** 한쪽에
    `COMPOSE_PROJECT_NAME=ieum-dev` 를 주세요.

## 다음

- [쓰는 법](guide/index.md) — 첫 프로젝트, 첫 문서, 첫 티켓
- [올리기와 되돌리기](upgrade.md) — 새 판으로 올릴 때
- [운영](operations.md) — 메트릭, 경보, 백업 복원 연습

## 붙는 것을 직접 준비한다면

한 줄 설치본은 Postgres·Redis·MinIO 를 함께 띄웁니다. 관리형 서비스를 쓴다면
주소만 바꾸면 되지만, **Postgres 는 PGroonga 확장이 필요합니다**(한국어
전문검색).

```sql
CREATE EXTENSION IF NOT EXISTS pgroonga;
-- citext, ltree, pg_trgm 도 필요합니다. 전체 목록은
-- deploy/install/compose.yml 의 `postgres-extensions` 설정에 있습니다.
```

PGroonga 가 없으면 검색이 **조용히 안 됩니다** — 오류가 아니라 0건으로
나타나므로, 설치 직후에 한국어로 한 번 검색해서 확인하세요.

첨부를 외부 S3 로 보낸다면 `IEUM_S3_ENDPOINT_URL` 과
`IEUM_S3_PUBLIC_ENDPOINT_URL` 을 그쪽으로 돌립니다. 그러면 같은 오리진으로
나가던 첨부가 그 주소로 나가고, `web` 안의 버킷 프록시는 그냥 안 쓰입니다.

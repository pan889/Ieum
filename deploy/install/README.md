# 한 노드에 깔기

한 줄이면 선다. Docker 와 Compose(v2)만 있으면 된다.

```bash
curl -fsSLO https://raw.githubusercontent.com/pan889/Ieum/v1.0.0/deploy/install/install.sh
sh install.sh --url https://ieum.example.com
```

> 남이 준 스크립트를 그냥 파이프로 넘기지 말고 **한 번 열어 보는 쪽**을 권한다.
> 280줄이고 하는 일이 다 적혀 있다. 저장소를 받아 뒀다면
> `deploy/install/install.sh` 를 그대로 쓰면 된다 — 옆의 `compose.yml` 을
> 알아서 집는다.

물어보는 것은 **주소 하나**다. 나머지(비밀 키·DB 비밀번호·스토리지 열쇠)는
그 자리에서 만들어 `.env` 에 넣는다.

## 무엇이 서는가

| | |
|---|---|
| `web` | 화면. **밖으로 나가는 포트는 이것 하나뿐이다** |
| `api`·`worker` | 앱과 배경 작업 |
| `postgres` | PGroonga 가 든 이미지 (한국어 검색이 이 위에 선다) |
| `redis` | 큐와 캐시 |
| `minio` | 첨부 저장소 |
| `migrate`·`seed`·`minio-init` | 한 번 돌고 끝나는 것들 |

포트가 하나인 이유: 앞에 TLS 를 세울 때 **붙일 곳이 한 군데**여야 하기
때문이다. 첨부도 같은 오리진으로 나간다 — `web` 의 nginx 가 버킷 경로
(`/ieum-attachments/`)를 스토리지로 그대로 넘긴다. 경로를 고치지 않는 것이
중요하다: presigned 서명에 경로와 Host 가 들어 있어서 한 글자만 달라져도
스토리지가 403 을 낸다.

## 앞에 프록시를 세울 때

```
https://ieum.example.com  →  127.0.0.1:8080
```

`.env` 에서 `IEUM_HTTP_BIND=127.0.0.1` 로 바꾸면 밖에서 8080 을 직접 못 연다.
`IEUM_PUBLIC_URL` 은 **사람이 치는 주소**(`https://…`)여야 한다 — 이 값이
쿠키·첨부 링크·메일 링크에 그대로 들어간다.

## 판 올리기

```bash
sh install.sh --version 1.1.0
```

비밀은 다시 만들지 않는다. `compose.yml` 은 **그 판의 것으로 갈아 끼운다** —
이미지만 새것이 되고 설정이 옛것으로 남으면 몇 달 뒤 엉뚱한 증상이 된다.
그래서 **`compose.yml` 은 우리 것**이다. 고칠 것이 있으면 옆에
`compose.override.yml` 을 두면 compose 가 겹쳐 읽고, 설치 스크립트는 그
파일을 건드리지 않는다.

## 백업할 것

| | |
|---|---|
| `.env` | **`IEUM_SECRET_KEY` 가 여기 있다.** 잃으면 MFA·웹훅 시크릿을 못 되살린다 |
| `ieum_pgdata` 볼륨 | 데이터 전부 |
| `ieum_miniodata` 볼륨 | 첨부 파일 |

`docker compose down` 은 볼륨을 지우지 않는다. `down -v` 는 **지운다.**

## 같은 기계에서 소스도 돌린다면

compose 프로젝트 이름이 둘 다 `ieum` 이다. 그러면 저장소에서 친
`docker compose` 가 **이 설치본의 컨테이너를 자기 것으로 알고 치운다.**
한 기계에서 둘 다 굴리려면 한쪽에 다른 이름을 준다:

```bash
COMPOSE_PROJECT_NAME=ieum-dev docker compose up -d   # 저장소 쪽에서
```

## 안 하는 것

- **이미지를 빌드하지 않는다.** 판을 붙여 낸 것을 레지스트리에서 당긴다 —
  그래야 깐 사람과 우리가 같은 바이트를 돈다. 소스에서 빌드해 돌리려면
  저장소 루트의 `docker-compose.yml` 과 `deploy/compose/prod.yml` 을 쓴다.
- **TLS 를 대신 끝내 주지 않는다.** 인증서는 배포마다 다르다(사내 CA,
  Let's Encrypt, 로드밸런서). 앞에 이미 있는 것을 쓰는 편이 낫다.
- **메일 서버를 세우지 않는다.** `.env` 의 `IEUM_SMTP_*` 를 채운다.
  비워 두면 앱은 뜨지만 초대·알림 메일이 안 나간다.
- **여러 대로 못 늘린다.** 이 파일은 한 노드짜리다. 여러 대는 Helm 차트
  (`deploy/helm/`)와 문서의 HA 안내를 본다.

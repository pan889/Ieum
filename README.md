# Ieum (이음)

> *이음* — 한국어로 "잇는 것". 흩어진 이슈·문서·요청을 하나로 잇는다는 뜻.
> 영어권 발음 안내: **ee-eum**.

Jira + Confluence + JSM(서비스데스크)를 대체하는 셀프호스팅 오픈소스 팀 협업 플랫폼.

- **이슈 트래킹** — Redmine의 기능 범위(프로젝트 계층, 커스텀 필드, 워크플로우, 시간추적, 간트/캘린더, 이슈 관계, 저장된 쿼리)
- **위키** — Confluence 동등(스페이스, 페이지 트리, 버전 비교, 인라인 코멘트, 템플릿, 매크로, 동시편집)
- **서비스데스크** — 고객 포털, 요청 유형, 큐, SLA, 이메일 채널, KB 연동, CSAT

## 왜 또 만드나

Redmine은 위키·서비스데스크가 약하고, Confluence 대체재는 이슈와 권한 모델이 분리돼 있다.
ieum는 **하나의 권한 모델, 하나의 검색, 하나의 알림 체계** 위에 세 제품을 올린다.

## 스택

Python 3.12 / FastAPI / PostgreSQL 16 / Redis / React 19 + TypeScript
자세한 내용은 [tech-stack.md](https://github.com/pan889/ieum-docs/blob/main/docs/architecture/tech-stack.md).

## 문서

개발 문서는 **별도 저장소 [pan889/ieum-docs](https://github.com/pan889/ieum-docs)** 가 단독으로 소유한다.
이 저장소에는 문서 사본을 두지 않는다.

| | |
|---|---|
| 개발 규칙(AI 포함) | [CLAUDE.md](https://github.com/pan889/ieum-docs/blob/main/CLAUDE.md) |
| 제품 비전 | [product/vision.md](https://github.com/pan889/ieum-docs/blob/main/docs/product/vision.md) |
| 기능 전수 목록 | [product/feature-map.md](https://github.com/pan889/ieum-docs/blob/main/docs/product/feature-map.md) |
| 아키텍처 | [architecture/overview.md](https://github.com/pan889/ieum-docs/blob/main/docs/architecture/overview.md) |
| 로드맵 | [roadmap.md](https://github.com/pan889/ieum-docs/blob/main/docs/roadmap.md) |
| 로컬 실행 | [contributing/dev-setup.md](https://github.com/pan889/ieum-docs/blob/main/docs/contributing/dev-setup.md) |

## 라이선스

AGPL-3.0

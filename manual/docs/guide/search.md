# 검색과 IQL

## 한 상자에서 찾기

위쪽 검색창은 **이슈와 문서를 함께** 찾습니다. 한국어는 형태소로 봅니다 —
`배포` 로 찾으면 `배포를`, `배포한` 이 적힌 것이 나옵니다.

찾은 것 중에 **볼 권한이 없는 것은 애초에 결과에 없습니다.** 걸러서 감추는
것이 아니라 질의에 권한이 얹혀 있어서, 개수도 제목 조각도 새지 않습니다.

낱말을 띄어 쓰면 둘 다 있는 것(AND), `OR` 로 나누면 하나라도 있는 것,
`-낱말` 은 없는 것, `"두 낱말"` 은 그 순서 그대로입니다.

## IQL — 목록을 정하는 말

이슈 목록·보드·큐·저장 필터·문서의 `::issues` 매크로가 **모두 같은 말**을
씁니다. 한 곳에서 만든 질의를 다른 곳에 붙여 쓸 수 있습니다.

```
project = ENG AND status IN ("진행 중", "검토")
  AND assignee = currentUser()
  AND due <= endOfWeek()
  ORDER BY priority DESC, updated DESC
```

### 쓰는 법

| | |
|---|---|
| 비교 | `=` `!=` `>` `>=` `<` `<=` |
| 포함 | `~` (텍스트 포함) `!~` (미포함) |
| 목록 | `IN` `NOT IN` |
| 빈 값 | `IS EMPTY` `IS NOT EMPTY` |
| 묶기 | `AND` `OR` `NOT` `( )` |
| 정렬 | `ORDER BY 필드 ASC|DESC` |

키워드는 대소문자를 안 따집니다. 공백이나 특수문자가 없으면 따옴표를
생략할 수 있습니다.

### 자주 쓰는 필드

| 분류 | 필드 |
|---|---|
| 기본 | `project` `key` `type` `status` `statusCategory` `priority` `assignee` `reporter` `labels` `component` `fixVersion` `parent` `resolution` |
| 날짜 | `created` `updated` `due` `startDate` `resolved` |
| 수치 | `estimate` `timeSpent` `progress` `votes` `commentCount` |
| 관계 | `linkedIssue` `subtaskOf` `watcher` |
| 스프린트 | `sprint` `sprintState` (`future`/`active`/`closed`) |
| 데스크 | `requestType` `organization` `slaBreached` `channel` |
| 커스텀 | `cf["필드키"]` 또는 등록한 별칭 |

### 함수

`currentUser()` · `now()` · `startOfDay(±n)` · `endOfDay(±n)` ·
`startOfWeek(±n)` · `endOfWeek(±n)` · `startOfMonth(±n)` · `endOfMonth(±n)` ·
`membersOf("그룹")` · `projectsWhereRole("역할")` · `watchedByMe()`

`endOfWeek(-1)` 은 지난주 끝입니다. 날짜를 손으로 적지 않으면 저장 필터가
계속 맞습니다.

### 이력으로 찾기

```
status WAS "진행 중" BY 지훈 DURING (startOfMonth(), now())
status CHANGED AFTER -7d
```

"한때 이 상태였던 것" 과 "최근에 바뀐 것" 입니다.

### 틀렸을 때

**어디가 틀렸는지 자리를 짚어 줍니다.** 오류 메시지만 주는 것이 아니라
그 낱말에 밑줄이 갑니다. 필드 이름과 값도 제안합니다 — `Ctrl/Cmd+Space`
로 목록을 열고, `Esc` 로 닫고, `Ctrl/Cmd+Enter` 로 실행합니다.

## 저장 필터

질의를 저장해 이름을 붙입니다. **주소에도 실립니다** — 질의를 그대로 담은
URL 을 복사해 남에게 보낼 수 있고, 받은 사람은 자기 권한으로 봅니다(남의
권한으로 보이지 않습니다).

## 검색이 안 될 때

- **한국어가 안 찾아진다** → Postgres 에 PGroonga 확장이 없을 수 있습니다.
  [설치 문서](../install.md)의 마지막 절을 보세요. 오류가 아니라 0건으로
  나타나므로 알아채기 어렵습니다.
- **방금 만든 것이 안 나온다** → 기본 설정에서는 **즉시** 나와야 합니다.
  안 나오면 검색 백엔드를 OpenSearch 로 바꾼 설치일 수 있습니다 — 그쪽은
  몇 초 뒤에 따라옵니다([운영 7절](../operations.md)).
- **`*` 하나로 검색했다** → 그건 낱말이 아니라 연산자의 조각입니다. 기본
  백엔드는 "읽을 수 없는 질의" 로 보고 적은 그대로 찾습니다.

"""소스의 낱말을 이 설치본의 어휘로 옮긴다.

## 추측하지 않는다

Redmine 의 `New` 가 우리 `열림` 인지 `접수` 인지 우리는 모른다. 번역표를
넣어 두면 그 표가 맞는 설치본에서만 맞고, **틀린 설치본에서는 조용히 틀린
자리로 들어간다** — 이관은 되돌리기 번거로우니 그 조용함이 가장 비싸다.

그래서 이 파일은 **이름이 같은 것만 잇는다**(대소문자·공백 무시). 나머지는
"못 이었다" 로 보고하고, 사람이 짝을 지어 주면 그 짝을 쓴다. 미리 보기
화면이 존재하는 이유가 이것이다.

## 우선순위만 예외다

우리 우선순위는 숫자 1~5 인데 소스는 이름이다. 이름이 같을 수가 없으므로
여기서는 **순서**로 잇는다 — 소스가 준 이름들을 낮은 것부터 늘어놓고 우리
다섯 칸에 고르게 편다. 그것도 짐작이라 보고서에 그렇게 적는다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from ieum.migrate.archive import Issue

#: 우리 우선순위. 1 이 가장 높다 (`issues/models.py`).
PRIORITY_HIGHEST = 1
PRIORITY_NORMAL = 3
PRIORITY_LOWEST = 5

_SPACES = re.compile(r"\s+")


def normalize(name: str) -> str:
    """이름을 견주기 좋게. 대소문자와 공백만 없앤다 — **글자는 안 건드린다.**

    한국어 이름이 섞이므로 ASCII 로 접거나 하지 않는다. `In Progress` 와
    `in  progress` 는 같은 것으로 보고, `진행중` 과 `진행 중` 도 같다.
    """
    return _SPACES.sub("", name.strip().casefold())


@dataclass(frozen=True, slots=True)
class Target:
    """이을 수 있는 우리 쪽 낱말 하나."""

    id: str
    name: str
    #: 같은 이름이 둘 이상일 때 사람이 구별할 꼬리표(워크플로우 이름 등).
    qualifier: str = ""


#: 어떻게 이었는가. 보고서가 이 값을 그대로 보여 준다.
BY_NAME = "name"
BY_OVERRIDE = "override"
BY_RANK = "rank"
UNMATCHED = "unmatched"


@dataclass(frozen=True, slots=True)
class Match:
    source: str
    target_id: str = ""
    target_name: str = ""
    how: str = UNMATCHED

    @property
    def ok(self) -> bool:
        return self.how != UNMATCHED


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """소스 묶음에 실제로 나온 낱말들. **정의 목록이 아니라 쓰인 목록이다.**

    소스가 상태를 스무 개 정의해 두고 셋만 썼다면 셋만 옮기면 된다. 안 쓴
    것까지 짝지으라고 하면 사람이 열일곱 번 헛일을 한다.
    """

    types: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    priorities: tuple[str, ...] = ()


def term(value: str) -> str:
    """묶음에서 온 낱말의 표준형.

    **모으는 쪽과 찾는 쪽이 반드시 같은 것을 써야 한다.** 한쪽만 `strip` 하면,
    소스에 앞뒤 공백이 붙은 상태 이름이 하나라도 있을 때 미리 보기는 멀쩡히
    통과하고 적재가 `KeyError` 로 500 이 난다 — 그것도 이슈를 절반쯤 만든
    뒤에.
    """
    return value.strip()


def used_vocabulary(issues: Sequence[Issue]) -> Vocabulary:
    """묶음에서 실제로 쓰인 낱말만 모은다. 나온 순서를 지킨다."""
    types: list[str] = []
    statuses: list[str] = []
    priorities: list[str] = []
    for issue in issues:
        for value, bucket in (
            (issue.type, types),
            (issue.status, statuses),
            (issue.priority, priorities),
        ):
            cleaned = term(value)
            if cleaned and cleaned not in bucket:
                bucket.append(cleaned)
    return Vocabulary(tuple(types), tuple(statuses), tuple(priorities))


def match_names(
    sources: tuple[str, ...],
    targets: list[Target],
    overrides: dict[str, str] | None = None,
) -> list[Match]:
    """이름이 같은 것끼리 잇는다. 사람이 준 짝이 있으면 그것이 먼저다.

    **같은 이름이 우리 쪽에 둘 이상이면 잇지 않는다.** 아무거나 고르면 그
    이슈들은 사람이 안 고른 워크플로우로 들어가고, 그 사실은 화면에 안
    나타난다. 못 이은 것으로 두고 사람에게 고르게 한다.
    """
    overrides = overrides or {}
    by_name: dict[str, list[Target]] = {}
    for target in targets:
        by_name.setdefault(normalize(target.name), []).append(target)
    by_id = {target.id: target for target in targets}

    out: list[Match] = []
    for source in sources:
        chosen = overrides.get(source)
        if chosen and chosen in by_id:
            target = by_id[chosen]
            out.append(Match(source, target.id, target.name, BY_OVERRIDE))
            continue
        candidates = by_name.get(normalize(source), [])
        if len(candidates) == 1:
            target = candidates[0]
            out.append(Match(source, target.id, target.name, BY_NAME))
        else:
            out.append(Match(source))
    return out


def match_priorities(
    sources: tuple[str, ...], overrides: dict[str, str] | None = None
) -> list[Match]:
    """이름을 우리 1~5 에 편다.

    **아는 이름이면 이름으로 잇는다.** 예전에는 묶음에 나온 순서만 보고
    5→1 로 폈는데, 그 순서는 소스가 정한 순서가 아니라 **이슈가 나온 순서**다
    (`used_vocabulary`). 그래서 묶음의 첫 이슈가 High 이면 High 가 5(가장
    낮음)가 됐다 — 주석은 "어댑터가 소스의 정의 순서대로 싣는다" 고 적어
    두었지만 묶음 형식에는 그런 목록이 없다.

    **전부 알아볼 때만** 이름으로 잇는다. 반만 알아보고 나머지를 순서로
    채우면 두 규칙이 한 목록 안에서 섞여 서로 어긋난다.

    하나도 못 알아보면 옛 방식대로 순서로 편다 — 그게 아무 짝도 안 지어
    주는 것보다는 낫고, `BY_RANK` 로 표시되어 미리 보기 화면이 "순서로
    짐작했다, 확인하라" 고 적는다.
    """
    overrides = overrides or {}
    named = _by_known_name(sources)
    out: list[Match] = []
    count = len(sources)
    for index, source in enumerate(sources):
        chosen = overrides.get(source, "")
        if chosen.isdigit() and PRIORITY_HIGHEST <= int(chosen) <= PRIORITY_LOWEST:
            out.append(Match(source, chosen, chosen, BY_OVERRIDE))
            continue
        if named is not None:
            rank = named[source]
            out.append(Match(source, str(rank), str(rank), BY_NAME))
            continue
        if count == 1:
            rank = PRIORITY_NORMAL
        else:
            # 이름을 하나도 못 알아봤다. 나온 순서가 낮은 것부터라고 보고 편다.
            span = PRIORITY_LOWEST - PRIORITY_HIGHEST
            rank = PRIORITY_LOWEST - round(index * span / (count - 1))
        out.append(Match(source, str(rank), str(rank), BY_RANK))
    return out


#: 널리 쓰이는 우선순위 이름 → 우리 1~5. 소문자로 견준다.
#:
#: Redmine 기본값(Low·Normal·High·Urgent·Immediate)과 Jira 기본값
#: (Lowest·Low·Medium·High·Highest)을 덮는다. 한국어 이름도 넣는다 — 두
#: 제품 모두 현지화된 이름을 그대로 쓰는 설치가 흔하다.
#:
#: **겹치는 것을 허용한다.** 소스가 다섯 단계보다 잘게 나눠 두었으면 몇 개는
#: 같은 자리로 모인다. 방향이 맞는 것이 자리 수가 맞는 것보다 중요하다.
_PRIORITY_NAMES: dict[str, int] = {
    "immediate": 1,
    "blocker": 1,
    "critical": 1,
    "highest": 1,
    "즉시": 1,
    "최고": 1,
    "urgent": 2,
    "high": 2,
    "긴급": 2,
    "높음": 2,
    "normal": 3,
    "medium": 3,
    "moderate": 3,
    "보통": 3,
    "중간": 3,
    "low": 4,
    "낮음": 4,
    "lowest": 5,
    "trivial": 5,
    "minor": 5,
    "최저": 5,
}


def _by_known_name(sources: tuple[str, ...]) -> dict[str, int] | None:
    """전부 알아보면 `{이름: 순위}`, 하나라도 모르면 None."""
    found: dict[str, int] = {}
    for source in sources:
        rank = _PRIORITY_NAMES.get(source.strip().lower())
        if rank is None:
            return None
        found[source] = rank
    return found or None


#: 사람을 왜 못 이었는가.
NO_EMAIL = "no_email"
UNKNOWN_EMAIL = "unknown_email"


@dataclass(frozen=True, slots=True)
class PersonMatch:
    source_id: str
    name: str
    email: str = ""
    user_id: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.user_id)


@dataclass(frozen=True, slots=True)
class Report:
    """무엇이 들어오고 무엇이 안 들어오는가.

    **못 옮기는 것을 세는 것이 이 보고서의 일이다.** 들어온 개수는 나중에도
    셀 수 있지만, 안 들어온 것은 여기서 안 적으면 아무 데도 안 남는다.
    """

    types: list[Match] = field(default_factory=list)
    statuses: list[Match] = field(default_factory=list)
    priorities: list[Match] = field(default_factory=list)
    people: list[PersonMatch] = field(default_factory=list)
    #: 소스에 있었지만 이 묶음 안에 없는 부모·관계 상대.
    dangling_parents: list[str] = field(default_factory=list)
    dangling_relations: list[str] = field(default_factory=list)
    #: 우리가 모르는 관계 종류. 버리지 않고 이름을 남긴다.
    unknown_relation_kinds: list[str] = field(default_factory=list)
    #: 종류·상태 칸이 **비어 있는** 이슈. 짝지을 낱말 자체가 없다.
    blank_type: list[str] = field(default_factory=list)
    blank_status: list[str] = field(default_factory=list)
    #: 고른 상태가 고른 종류의 워크플로우에 없는 짝. `("Bug", "완료")` 꼴.
    #:
    #: 프로젝트에 워크플로우가 둘 이상이면 생긴다 — 상태 목록은 프로젝트의
    #: 모든 워크플로우를 한 통에 담아 보여 주므로, 이름만 보고 고르면 다른
    #: 워크플로우의 상태가 걸릴 수 있다. 그대로 실으면 이슈는 만들어지는데
    #: 그 상태에서 나갈 전이가 하나도 없어 **아무도 못 옮기는 이슈**가 된다.
    crossed_workflows: list[tuple[str, str]] = field(default_factory=list)

    @property
    def blocking(self) -> list[str]:
        """이대로 실으면 **이슈가 안 만들어지는** 것들.

        빈 칸도 여기 든다. 그 자리를 우리가 골라 주면 그 이슈들은 아무도 안
        고른 종류·상태로 들어가고, 개수만 맞아서 **옮긴 사람은 성공으로
        본다.** 어댑터는 둘 다 채워 주므로(소스가 요구한다), 비어 있다는 것은
        묶음이 깨졌다는 뜻이다.
        """
        out = [f"종류: {m.source}" for m in self.types if not m.ok]
        out += [f"상태: {m.source}" for m in self.statuses if not m.ok]
        if self.blank_type:
            out.append(f"종류가 비어 있는 이슈 {len(self.blank_type)}개: {self.blank_type[:5]}")
        if self.blank_status:
            out.append(f"상태가 비어 있는 이슈 {len(self.blank_status)}개: {self.blank_status[:5]}")
        out += [
            f"종류 '{type_name}' 의 워크플로우에 없는 상태다: {status_name}"
            for type_name, status_name in self.crossed_workflows
        ]
        return out


__all__ = [
    "BY_NAME",
    "BY_OVERRIDE",
    "BY_RANK",
    "NO_EMAIL",
    "PRIORITY_HIGHEST",
    "PRIORITY_LOWEST",
    "PRIORITY_NORMAL",
    "UNKNOWN_EMAIL",
    "UNMATCHED",
    "Match",
    "PersonMatch",
    "Report",
    "Target",
    "Vocabulary",
    "match_names",
    "match_priorities",
    "normalize",
    "term",
    "used_vocabulary",
]

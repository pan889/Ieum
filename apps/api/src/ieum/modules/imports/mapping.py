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
            cleaned = value.strip()
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

    소스가 준 이름의 **순서를 우리는 모른다.** `("Low", "Normal", "High")` 가
    낮은 것부터인지 아닌지 파일에는 안 적혀 있다. 그래서 묶음에 나온 순서를
    그대로 쓴다 — 어댑터가 소스의 정의 순서대로 싣기 때문이고, Redmine 은
    실제로 낮은 것부터 준다.

    짐작이므로 `BY_RANK` 로 표시한다. 미리 보기 화면이 이것을 다르게 그린다.
    """
    overrides = overrides or {}
    out: list[Match] = []
    count = len(sources)
    for index, source in enumerate(sources):
        chosen = overrides.get(source, "")
        if chosen.isdigit() and PRIORITY_HIGHEST <= int(chosen) <= PRIORITY_LOWEST:
            out.append(Match(source, chosen, chosen, BY_OVERRIDE))
            continue
        if count == 1:
            rank = PRIORITY_NORMAL
        else:
            # 낮은 것부터 온다고 보고 5→1 로 편다.
            span = PRIORITY_LOWEST - PRIORITY_HIGHEST
            rank = PRIORITY_LOWEST - round(index * span / (count - 1))
        out.append(Match(source, str(rank), str(rank), BY_RANK))
    return out


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

    @property
    def blocking(self) -> list[str]:
        """이대로 실으면 **이슈가 안 만들어지는** 것들."""
        out = [f"종류: {m.source}" for m in self.types if not m.ok]
        out += [f"상태: {m.source}" for m in self.statuses if not m.ok]
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
    "used_vocabulary",
]

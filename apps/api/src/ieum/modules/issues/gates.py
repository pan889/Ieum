"""전이를 막을 수 있는 자리 (C12).

## 왜 훅인가

승인(Approval)은 데스크의 개념이다. 그런데 티켓을 옮기는 길은 이슈의 것이다
(티켓은 이슈다, ADR-0003) — 상담원은 `PATCH /issues/{key}/transition` 으로
티켓을 옮긴다. 그래서 검사는 **이슈의 전이 안에서** 일어나야 하는데,
`issues` 는 `desk` 를 import 할 수 없다(의존 방향, overview.md).

첨부 소유자 리졸버와 권한 관문이 이미 같은 모양이다: 프로토콜은 아래 계층이
갖고, 구현은 위 계층이 만들고, `wiring.py` 가 꽂아 넣는다. 여기도 그렇게 한다.

## 왜 조건(`conditions`)으로 안 만드는가

워크플로우 조건은 **관리자가 켜야** 걸린다. 승인을 조건으로 두면 승인을
설정한 요청 유형의 워크플로우에 그 조건을 손으로 붙여야 하고, 안 붙이면
승인은 요청되지만 아무것도 막지 못한다 — 그건 승인이 아니라 표시다.

관문은 반대로 **기본 거절**이다. 켜는 것을 잊어도 막힌다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor


@dataclass(frozen=True, slots=True)
class Gate:
    """관문이 볼 것. ORM 을 넘기지 않는다 — 위 계층이 이슈를 되짚어 읽으면
    경계가 이름만 남는다."""

    issue_id: UUID
    project_id: UUID
    #: 지금 상태의 갈래. 새 이슈면 `None`.
    from_category: str | None
    #: 가려는 상태의 갈래 (`todo`·`in_progress`·`done`).
    to_category: str


class TransitionGate(Protocol):
    """막을 이유가 있으면 **예외를 던진다.** 통과면 아무 말도 하지 않는다.

    코드를 돌려주게 두지 않는 이유가 둘이다.

    - **문구를 잃는다.** 코드만 받으면 `issues` 가 "지금은 이 전이를 할 수
      없다" 같은 일반적인 문구를 붙이게 되고, 막은 쪽이 아는 사정(누가
      승인해야 하는가)은 사라진다.
    - **에러 코드 게이트에 안 보인다.** `test_i18n_error_codes.py` 는
      `code="..."` 로 넘기는 코드를 모은다. `return "desk.xxx"` 는 그 검사에
      걸리지 않아서, 번역 없는 코드가 조용히 사용자 화면까지 간다 — 그 검사가
      막으려는 것이 바로 그것이다.
    """

    async def __call__(self, session: AsyncSession, actor: Actor, gate: Gate, /) -> None: ...


_gates: list[TransitionGate] = []


def register(gate: TransitionGate) -> None:
    """기동 시 한 번 부른다 (`wiring.py`)."""
    _gates.append(gate)


def clear() -> None:
    """시험용. 등록은 전역이라 시험끼리 새지 않게 지울 길이 필요하다."""
    _gates.clear()


async def check(session: AsyncSession, actor: Actor, gate: Gate) -> None:
    """등록된 관문을 차례로 묻는다. **첫 거절에서 멈춘다**(예외가 나간다) —
    사람에게는 지금 무엇이 막고 있는지 하나만 말하는 것이 낫다."""
    for gate_check in _gates:
        await gate_check(session, actor, gate)


__all__ = ["Gate", "TransitionGate", "check", "clear", "register"]

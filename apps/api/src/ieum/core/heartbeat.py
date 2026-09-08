"""워커가 살아 있다는 증거.

**워커는 다른 프로세스다.** 자기 메모리의 지표는 API 의 `/metrics` 에 실을
수 없고, 워커에 포트를 따로 열면 배포에 문 하나가 는다. 그래서 DB 에 한 줄을
남기고 API 가 그것을 읽어 낸다 — 어차피 둘 다 같은 DB 를 본다.

## 왜 이게 필요한가

워커가 죽으면 **아무 일도 안 일어난다.** 알림도, 메일도, 웹훅도, SLA 클럭도
멈추는데 화면은 멀쩡하다. 티켓은 계속 들어오고 아무도 답을 못 받는다. 로그를
읽는 사람만 알 수 있는 실패는, 아무도 안 읽으므로 아무도 모르는 실패다.

한 줄에 담는 것: 마지막으로 **끝난** 시각, 걸린 시간, 실패했다면 그 말.
시작 시각이 아니라 끝난 시각이다 — 시작만 남기면 도중에 멈춘 워커가 계속
살아 있는 것으로 보인다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from ieum.core.time import utcnow
from ieum.db.base import Base


class Heartbeat(Base):
    """주기 작업 하나의 마지막 실행.

    작업 이름이 PK 다. 이력이 아니라 **지금 상태**를 담는 표이고, 이력은
    로그가 갖는다 — 여기에 행이 쌓이면 청소할 사람이 필요해진다.
    """

    __tablename__ = "heartbeat"

    task: Mapped[str] = mapped_column(String(64), primary_key=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: 실패했으면 그 말. 성공하면 비운다 — 지난 실패가 남아 있으면 지금
    #: 실패한 것처럼 보인다.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


async def beat(
    session: AsyncSession, task: str, *, duration_seconds: float, error: str | None = None
) -> None:
    """한 번 돌았다고 적는다. 있으면 덮어쓴다.

    `INSERT ... ON CONFLICT` 로 한 번에 한다. 읽고 없으면 넣는 식이면 워커가
    둘일 때 같은 순간에 둘 다 넣으려다 하나가 터진다.
    """
    stmt = insert(Heartbeat).values(
        task=task,
        finished_at=utcnow(),
        duration_seconds=duration_seconds,
        last_error=error,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Heartbeat.task],
            set_={
                "finished_at": stmt.excluded.finished_at,
                "duration_seconds": stmt.excluded.duration_seconds,
                "last_error": stmt.excluded.last_error,
            },
        )
    )


async def all_beats(session: AsyncSession) -> list[Heartbeat]:
    return list((await session.execute(select(Heartbeat))).scalars().all())

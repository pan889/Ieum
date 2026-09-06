"""ORM 베이스. 모든 테이블이 따르는 공통 규칙을 여기서 강제한다.

- PK 는 UUIDv7 (D-10)
- created_at / updated_at 는 timezone-aware UTC, 예외 없음
- 삭제는 archived_at (soft delete) 이 기본
- lazy 로딩 금지: 관계는 반드시 명시적으로 로딩한다 (module-guide 성능 규칙)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ieum.core.ids import new_id

# 제약·인덱스 이름을 규칙화한다. 이름이 없으면 alembic autogenerate 가
# 익명 제약을 다루지 못해 다운그레이드가 깨진다.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # 관계는 반드시 lazy="raise" 로 선언한다. 그래야 로딩을 깜빡한 접근이
    # 조용한 N+1 이 아니라 즉시 예외로 드러난다 (module-guide 성능 규칙).

    # 서버가 만드는 값(created_at/updated_at)을 INSERT·UPDATE 의 RETURNING 으로
    # 함께 받아온다. 이게 없으면 onupdate 컬럼이 만료 표시되고, 커밋 뒤 접근할 때
    # 동기 lazy refresh 가 일어나 async 컨텍스트에서 MissingGreenlet 이 난다.
    __mapper_args__: dict[str, Any] = {"eager_defaults": True}  # noqa: RUF012

    def __repr__(self) -> str:
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


class UUIDPrimaryKey:
    """UUIDv7 PK 믹스인."""

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)


class Timestamped:
    """생성·수정 시각 믹스인. DB 서버 시각(UTC)을 기준으로 한다."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Archivable:
    """soft delete 믹스인. 실삭제는 휴지통 정책에 따라 워커가 한다."""

    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class Entity(Base, UUIDPrimaryKey, Timestamped):
    """대부분의 테이블이 상속하는 기본 엔티티."""

    __abstract__ = True

    def as_dict(self) -> dict[str, Any]:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

"""org ORM 모델 (docs/architecture/data-model.md org 절)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ieum.db.base import Archivable, Entity

SCOPE_KINDS = ("global", "project", "space", "queue")
PRINCIPAL_KINDS = ("user", "group")


class Workspace(Entity):
    """설치당 1조직. 초기엔 단일 행이다.

    SaaS 로 갈 때 행 단위 격리(모든 테이블에 workspace_id + RLS)로 확장할 수
    있도록 테이블만 미리 둔다 (overview.md 멀티테넌시).
    """

    __tablename__ = "workspace"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Project(Entity, Archivable):
    __tablename__ = "project"

    #: 이슈 키 접두사. PROJ-123 의 PROJ.
    key: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 프로젝트 계층. 하위는 상위의 역할 할당을 상속한다.
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="RESTRICT"), nullable=True
    )
    lead_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: 이슈 키 채번용 카운터. UPDATE ... RETURNING 으로 원자적으로 올린다.
    issue_counter: Mapped[int] = mapped_column(nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("key = upper(key)", name="project_key_uppercase"),
        Index("ix_project_parent_id", "parent_id"),
        Index("ix_project_live", "key", postgresql_where=text("archived_at IS NULL")),
    )


class Role(Entity):
    __tablename__ = "role"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 내장 역할은 삭제할 수 없다.
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: 이 역할을 받은 사람에게 2FA 를 강제한다 (auth.md 3절). 관리자·상담원처럼
    #: 남의 데이터를 볼 수 있는 자리에 붙인다.
    require_mfa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint(scope_kind.in_(SCOPE_KINDS), name="role_scope_kind"),
        UniqueConstraint("name", "scope_kind", name="uq_role_name_scope_kind"),
    )


class PermissionGrant(Entity):
    """역할이 가진 권한 하나. 값은 core.permissions 레지스트리의 키다."""

    __tablename__ = "permission_grant"

    role_id: Mapped[UUID] = mapped_column(ForeignKey("role.id", ondelete="CASCADE"), nullable=False)
    permission: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (
        UniqueConstraint("role_id", "permission", name="uq_permission_grant_role_id_permission"),
        Index("ix_permission_grant_permission", "permission"),
    )


class RoleAssignment(Entity):
    """주체(사용자·그룹)에게 특정 스코프의 역할을 준다."""

    __tablename__ = "role_assignment"

    role_id: Mapped[UUID] = mapped_column(ForeignKey("role.id", ondelete="CASCADE"), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: global 스코프는 NULL.
    scope_id: Mapped[UUID | None] = mapped_column(nullable=True)
    principal_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    principal_id: Mapped[UUID] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint(scope_kind.in_(SCOPE_KINDS), name="role_assignment_scope_kind"),
        CheckConstraint(principal_kind.in_(PRINCIPAL_KINDS), name="role_assignment_principal_kind"),
        CheckConstraint(
            "(scope_kind = 'global') = (scope_id IS NULL)",
            name="role_assignment_scope_id_matches_kind",
        ),
        UniqueConstraint(
            "role_id",
            "scope_kind",
            "scope_id",
            "principal_kind",
            "principal_id",
            name="uq_role_assignment_unique",
        ),
        # 권한 평가의 주 경로. 주체 집합으로 한 번에 조회한다.
        Index("ix_role_assignment_principal", "principal_kind", "principal_id"),
        Index("ix_role_assignment_scope", "scope_kind", "scope_id"),
    )


class EntityLink(Entity):
    """모듈 간 순환 참조를 피하는 중립 링크 (overview.md 모듈 의존 그래프).

    위키↔이슈처럼 서로 알아야 하는 관계를 여기로 뺀다. 어느 쪽도 상대 모듈을
    import 하지 않는다.
    """

    __tablename__ = "entity_link"

    from_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_id: Mapped[UUID] = mapped_column(nullable=False)
    to_type: Mapped[str] = mapped_column(String(32), nullable=False)
    to_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "from_type", "from_id", "to_type", "to_id", "kind", name="uq_entity_link_unique"
        ),
        Index("ix_entity_link_from", "from_type", "from_id"),
        Index("ix_entity_link_to", "to_type", "to_id"),
    )

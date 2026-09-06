"""issues ORM 모델 (docs/architecture/data-model.md issues 절)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ieum.db.base import Archivable, Base, Entity

STATE_CATEGORIES = ("todo", "in_progress", "done")
LINK_KINDS = ("blocks", "relates", "duplicates", "precedes", "copied")
VERSION_STATUSES = ("open", "released", "archived")
#: data-model.md 의 커스텀 필드 9종
FIELD_KINDS = (
    "text",
    "number",
    "date",
    "select",
    "multi_select",
    "user",
    "version",
    "bool",
    "url",
)


class Workflow(Entity):
    __tablename__ = "workflow"

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class WorkflowState(Entity):
    """워크플로우의 상태 하나.

    `category` 는 보드 컬럼·번다운·"완료 여부" 판정의 근거다. 사용자가 상태
    이름을 뭐라고 짓든 시스템은 category 로 판단한다.
    """

    __tablename__ = "workflow_state"

    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 워크플로우당 하나. 새 이슈가 여기서 시작한다.
    is_initial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint(category.in_(STATE_CATEGORIES), name="workflow_state_category"),
        UniqueConstraint("workflow_id", "name", name="uq_workflow_state_workflow_id_name"),
        Index("ix_workflow_state_workflow_id", "workflow_id"),
        # 시작 상태는 워크플로우당 정확히 하나여야 한다. 둘이면 새 이슈의
        # 상태가 비결정적이 된다.
        Index(
            "uq_workflow_state_initial",
            "workflow_id",
            unique=True,
            postgresql_where=text("is_initial"),
        ),
    )


class WorkflowTransition(Entity):
    """상태 전이 규칙.

    조건과 후처리는 **등록된 이름 + 파라미터**로만 저장한다. 임의 코드 실행은
    지원하지 않는다 (module-guide 워크플로우 엔진 규약).
    """

    __tablename__ = "workflow_transition"

    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: NULL 이면 "모든 상태에서" 전이할 수 있다 (Jira 의 global transition).
    from_state_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workflow_state.id", ondelete="CASCADE"), nullable=True
    )
    to_state_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_state.id", ondelete="CASCADE"), nullable=False
    )
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    post_functions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_workflow_transition_workflow_id", "workflow_id"),
        Index("ix_workflow_transition_from_state_id", "from_state_id"),
    )


class IssueType(Entity):
    __tablename__ = "issue_type"

    #: NULL 이면 전역 유형. 프로젝트가 지정되면 그 프로젝트 전용.
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_subtask: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_issue_type_project_id", "project_id"),
        Index("ix_issue_type_workflow_id", "workflow_id"),
    )


class Version(Entity):
    __tablename__ = "version"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")

    __table_args__ = (
        CheckConstraint(status.in_(VERSION_STATUSES), name="version_status"),
        UniqueConstraint("project_id", "name", name="uq_version_project_id_name"),
    )


class IssueCategory(Entity):
    __tablename__ = "issue_category"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    default_assignee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_issue_category_project_id_name"),
    )


class SecurityLevel(Entity):
    """이슈 보안 레벨. 지정된 역할·사용자만 조회할 수 있다 (auth.md 5절)."""

    __tablename__ = "security_level"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 조회를 허용할 주체. [{"kind": "user"|"group"|"role", "id": "..."}]
    grantees: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_security_level_project_id_name"),
    )


class Issue(Entity, Archivable):
    __tablename__ = "issue"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="RESTRICT"), nullable=False
    )
    #: 프로젝트별 순번. 표시용 키는 project.key + '-' + key_seq.
    key_seq: Mapped[int] = mapped_column(Integer, nullable=False)

    type_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue_type.id", ondelete="RESTRICT"), nullable=False
    )
    state_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_state.id", ondelete="RESTRICT"), nullable=False
    )

    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    #: 마크다운 원문. 렌더 결과나 AST 를 저장하지 않는다 (ADR-0008).
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    reporter_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    assignee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    #: 1(highest) ~ 5(lowest). 숫자가 작을수록 높다 — 정렬이 자연스럽다.
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)

    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("issue.id", ondelete="RESTRICT"), nullable=True
    )
    category_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("issue_category.id", ondelete="SET NULL"), nullable=True
    )
    fix_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("version.id", ondelete="SET NULL"), nullable=True
    )
    security_level_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("security_level.id", ondelete="SET NULL"), nullable=True
    )

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    estimate_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 0~100. 하위 이슈가 있으면 롤업으로 계산한다.
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: 낙관적 잠금. If-Match 불일치 시 409 (conventions.md API 규약).
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("project_id", "key_seq", name="uq_issue_project_id_key_seq"),
        CheckConstraint("priority BETWEEN 1 AND 5", name="issue_priority_range"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="issue_progress_range"),
        CheckConstraint("id <> parent_id", name="issue_parent_not_self"),
        # 목록 화면의 기본 정렬 + 필터 조합 (data-model 인덱스 체크리스트)
        Index(
            "ix_issue_project_state_updated",
            "project_id",
            "state_id",
            "updated_at",
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index("ix_issue_assignee_id", "assignee_id"),
        Index("ix_issue_reporter_id", "reporter_id"),
        Index("ix_issue_parent_id", "parent_id"),
        Index("ix_issue_type_id", "type_id"),
        Index("ix_issue_fix_version_id", "fix_version_id"),
        Index("ix_issue_security_level_id", "security_level_id"),
        Index("ix_issue_due_date", "due_date"),
    )

    def key(self, project_key: str) -> str:
        """표시용 키. 프로젝트 키는 호출자가 넘긴다 (모델은 조인하지 않는다)."""
        return f"{project_key}-{self.key_seq}"


class IssueLabel(Base):
    """이슈 라벨. 자유 문자열이라 별도 정의 테이블을 두지 않는다."""

    __tablename__ = "issue_label"

    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), primary_key=True
    )
    label: Mapped[str] = mapped_column(String(100), primary_key=True)

    __table_args__ = (Index("ix_issue_label_label", "label"),)


class IssueLink(Entity):
    __tablename__ = "issue_link"

    from_issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), nullable=False
    )
    to_issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        CheckConstraint(kind.in_(LINK_KINDS), name="issue_link_kind"),
        CheckConstraint("from_issue_id <> to_issue_id", name="issue_link_not_self"),
        UniqueConstraint("from_issue_id", "to_issue_id", "kind", name="uq_issue_link_unique"),
        Index("ix_issue_link_from_issue_id", "from_issue_id"),
        Index("ix_issue_link_to_issue_id", "to_issue_id"),
    )


class IssueComment(Entity, Archivable):
    __tablename__ = "issue_comment"

    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: 데스크의 내부 노트. 고객에게 절대 노출하지 않는다 (auth.md 5절).
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_issue_comment_issue_created",
            "issue_id",
            "created_at",
            postgresql_where=text("archived_at IS NULL"),
        ),
    )


class IssueHistory(Entity):
    """변경 이력. append-only 다."""

    __tablename__ = "issue_history"

    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    #: [{"field": "status", "from": "Open", "to": "In Progress"}, ...]
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (Index("ix_issue_history_issue_created", "issue_id", "created_at"),)


class Worklog(Entity):
    __tablename__ = "worklog"

    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    spent_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("spent_minutes > 0", name="worklog_positive"),
        Index("ix_worklog_issue_id", "issue_id"),
        Index("ix_worklog_user_work_date", "user_id", "work_date"),
    )


class FieldDefinition(Entity):
    """커스텀 필드 정의. 값은 issue_field_value(JSONB) 에 들어간다.

    컬럼을 추가하지 않는다 (D-18). 정렬·필터가 매우 잦은 필드만 생성 컬럼으로
    승격하고 ADR 로 남긴다.
    """

    __tablename__ = "field_definition"

    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: kind 별 설정: select 의 options, number 의 min/max, text 의 max_length 등
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: NULL 이면 전역. 지정하면 해당 프로젝트에서만 보인다.
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=True
    )
    #: NULL 이면 모든 유형. 지정하면 해당 유형에서만 보인다.
    issue_type_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("issue_type.id", ondelete="CASCADE"), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(kind.in_(FIELD_KINDS), name="field_definition_kind"),
        CheckConstraint("key = lower(key)", name="field_definition_key_lowercase"),
        Index("ix_field_definition_scope", "project_id", "issue_type_id"),
    )


class IssueFieldValue(Base):
    """커스텀 필드 값. GIN 인덱스로 IQL 조회를 받는다."""

    __tablename__ = "issue_field_value"

    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), primary_key=True
    )
    field_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: 스칼라도 JSONB 로 감싼다. kind 별 해석은 밸리데이터가 담당한다.
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_issue_field_value_key", "field_key"),
        Index("ix_issue_field_value_gin", "value", postgresql_using="gin"),
    )

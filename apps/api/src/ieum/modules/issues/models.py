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
    #: 어느 스프린트에 들어 있나 (M5). `NULL` 이면 백로그다.
    #:
    #: `SET NULL` 이다 — 스프린트를 지운다고 이슈가 사라지면 안 된다.
    sprint_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sprint.id", ondelete="SET NULL"), nullable=True
    )

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
        # 스프린트 합계·번다운·닫기가 전부 이 컬럼으로 훑는다 (M5).
        Index("ix_issue_sprint_id", "sprint_id"),
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


class SavedFilter(Entity):
    """저장된 IQL 필터.

    **원문 문자열을 저장한다. AST 를 저장하지 않는다** — 문법이 진화하기
    때문이다 (query-language.md 6절). 실행은 항상 실행자 권한으로 한다.
    필터 소유자의 권한을 승계하면 공유가 곧 권한 상승이 된다.
    """

    __tablename__ = "saved_filter"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    iql: Mapped[str] = mapped_column(Text, nullable=False)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_saved_filter_owner_id_name"),
        Index("ix_saved_filter_shared", "is_shared", postgresql_where=text("is_shared")),
    )


#: 보드가 스프린트를 다루는 방식.
#:
#: - `"all"`: 스프린트를 모른다. 컬럼 IQL 이 고른 것 전부.
#: - `"active"`: 도는 스프린트의 이슈만. 도는 스프린트가 없으면 거르지 않는다.
BOARD_SPRINT_MODES = ("all", "active")


class Board(Entity):
    """칸반 보드.

    **컬럼은 IQL 로 정의한다** (D-44). 상태 목록을 따로 들고 있으면 보드가
    또 하나의 필터 포맷이 되고, 워크플로우가 바뀔 때 두 곳을 고쳐야 한다.
    """

    __tablename__ = "board"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: [{"name": "진행 중", "iql": "statusCategory = in_progress", "wip_limit": 3}]
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    #: 스윔레인 기준 필드 이름. NULL 이면 레인 없이 한 줄.
    swimlane_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 보드가 다루는 이슈 범위. 컬럼 IQL 과 AND 로 묶인다.
    base_iql: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 스프린트를 아는가. `"active"` 면 도는 스프린트의 이슈만 보여 준다.
    #:
    #: 새로 만드는 보드의 기본값이 `"active"` 인 이유: 스프린트를 쓰기
    #: 시작하면 보드가 "이번 주기" 를 뜻해야 한다. 스프린트가 아직 없으면
    #: 거를 것이 없으므로 그냥 전부 보인다 — 빈 보드가 뜨지는 않는다.
    sprint_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(sprint_mode.in_(BOARD_SPRINT_MODES), name="board_sprint_mode"),
        UniqueConstraint("project_id", "name", name="uq_board_project_id_name"),
        Index("ix_board_project_id", "project_id"),
    )


#: 스프린트의 삶. **되돌아가지 않는다** — 닫은 스프린트를 다시 열면 그때 찍힌
#: 번다운이 거짓이 된다.
SPRINT_STATES = ("future", "active", "closed")


class Sprint(Entity):
    """기간이 있는 묶음 (M5).

    **라벨이 아니라 행이다.** 라벨로 두면 "언제부터 언제까지" 를 담을 곳이
    없고, 그러면 번다운을 그릴 수 없다 — 남은 일을 시간축에 놓는 것이
    번다운이므로 시간이 먼저 있어야 한다.

    한 프로젝트에 **활성 스프린트는 하나**다(부분 유니크 인덱스). 둘을
    허용하면 보드가 "지금 무엇을 보여 주는가" 에 답할 수 없고, 그 답이
    없으면 보드는 다시 "열려 있는 것 전부" 가 된다.
    """

    __tablename__ = "sprint"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 이번에 무엇을 이루려 하는가. 한 줄이면 충분하고, 없으면 없는 대로 둔다.
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="future")

    #: 계획한 기간. 시작 전에도 적을 수 있다 — 그래야 달력에 올린다.
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: **실제로** 시작·끝난 시각. 계획과 다를 수 있고, 번다운은 이쪽을 쓴다.
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(state.in_(SPRINT_STATES), name="sprint_state"),
        UniqueConstraint("project_id", "name", name="uq_sprint_project_id_name"),
        # **활성은 하나뿐.** 애플리케이션에서만 막으면 두 요청이 동시에 시작할
        # 때 둘 다 통과한다.
        Index(
            "uq_sprint_one_active",
            "project_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("ix_sprint_project_id", "project_id"),
    )


class RecurringIssue(Entity):
    """주기적으로 같은 이슈를 만드는 스케줄 (A27, M5).

    ## 왜 별도 표인가

    이슈의 사본이 아니라 **틀**이다. 만들어진 이슈를 고쳐도 다음 이슈는 틀에서
    나오고, 틀을 고쳐도 이미 만들어진 이슈는 그대로다. 그 둘이 같은 행에
    있으면 "다음 것부터 바꾸고 싶다" 를 표현할 수 없다.

    ## 다음 시각을 행이 들고 있다

    크론 표현식을 저장하고 매번 계산하지 않는다 — 계산이 틀리면 **아무 일도
    일어나지 않고**, 아무 일도 일어나지 않는 것은 화면에 안 보인다. 다음
    시각을 값으로 들고 있으면 화면이 "다음: 9월 15일 09:00" 을 보여 줄 수
    있고, 지나갔는데 안 돌았다는 것도 보인다.

    전진은 **이슈를 만드는 것과 같은 트랜잭션**에서 한다. 나누면 워커가 그
    사이에 죽었을 때 같은 이슈가 두 번 만들어진다.

    ## 왜 끈 이유를 적는가

    스케줄은 조용히 멈추면 안 된다. 만든 사람의 계정이 정지되면 그 이름으로
    계속 이슈가 만들어지는 것도 옳지 않아서 끄는데, 이유가 없으면 사람은
    "왜 안 돌지" 를 로그에서 찾아야 한다 (`email_channel.last_error` 와 같은
    자리다).
    """

    __tablename__ = "recurring_issue"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    #: 스케줄의 이름. 만들어지는 이슈의 요약과 다르다 — 목록에서 구분하는 이름이다.
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # ── 만들 이슈의 틀 ────────────────────────────────────────
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("issue_type.id", ondelete="SET NULL"), nullable=True
    )
    assignee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    labels: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: 만들 때 기한을 며칠 뒤로 둘 것인가. 없으면 기한 없는 이슈다.
    #:
    #: 절대 날짜를 둘 수 없는 이유: 반복이니까. "9월 30일" 은 한 번만 맞다.
    due_in_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── 언제 도는가 ──────────────────────────────────────────
    cadence: Mapped[str] = mapped_column(String(20), nullable=False)
    hour: Mapped[int] = mapped_column(Integer, nullable=False)
    minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 0=월 … 6=일. 매주일 때만 쓴다.
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 1~31. 매월일 때만 쓴다. 짧은 달에서는 당긴다(`recurrence.next_after`).
    day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: **스케줄의 시간대다.** 보는 사람의 것이 아니다.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    # ── 상태 ────────────────────────────────────────────────
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 마지막으로 만든 이슈. 화면에서 "이게 그것" 으로 갈 수 있어야 한다.
    last_issue_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("issue.id", ondelete="SET NULL"), nullable=True
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: 스스로 껐으면 그 이유. 사람이 끈 것과 구분된다(사람이 끄면 비어 있다).
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_recurring_issue_project_id_name"),
        # 워커가 "지금 지난 것" 만 훑는다. 꺼진 것은 애초에 안 본다.
        Index(
            "ix_recurring_issue_due",
            "next_run_at",
            postgresql_where=text("is_enabled"),
        ),
    )


class SprintSnapshot(Base):
    """그날 남아 있던 양 (M5).

    **되짚어 계산하지 않는다.** 지금 상태로 과거를 그리면, 어제 추가된 이슈가
    스프린트 첫날부터 있었던 것이 되고 번다운은 실제보다 예쁘게 나온다 —
    범위가 늘어난 사실이 그림에서 사라진다. 그래서 그날 값을 그날 적는다.

    오늘 줄은 스윕이 계속 덮어쓴다(살아 있다). 날짜가 바뀌면 그 줄은 그대로
    굳고, 새 줄이 생긴다.

    날짜는 **UTC 기준**이다. 프로젝트에 타임존이 없어서인데, 한국 팀이 보면
    하루가 오전 9시에 넘어간다 — 프로젝트 타임존이 생기면 그때 옮긴다.
    """

    __tablename__ = "sprint_snapshot"

    sprint_id: Mapped[UUID] = mapped_column(
        ForeignKey("sprint.id", ondelete="CASCADE"), primary_key=True
    )
    on_date: Mapped[date] = mapped_column(Date, primary_key=True)
    #: 아직 done 이 아닌 이슈 수.
    remaining_issues: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 그 이슈들의 추정 시간 합(분). 추정이 없는 이슈는 0 으로 센다.
    remaining_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 그날 스프린트에 들어 있던 전체. **범위가 늘어난 것이 여기서 보인다.**
    total_issues: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

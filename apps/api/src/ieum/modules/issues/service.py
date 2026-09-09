"""issues 비즈니스 로직.

권한 검사·도메인 규칙·트랜잭션 경계·이벤트 발행이 여기 있다.
다른 모듈은 contracts 로만 부른다 (절대규칙 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    OptimisticLockError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.logging import get_logger
from ieum.core.markdown import extract_mentions, to_plaintext
from ieum.core.markdown import normalize as normalize_markdown
from ieum.core.outbox import publish
from ieum.core.pagination import Page, PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity import contracts as identity
from ieum.modules.issues import events as issue_events
from ieum.modules.issues import gates
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.fields import validate_value
from ieum.modules.issues.models import (
    FieldDefinition,
    Issue,
    IssueComment,
    IssueLink,
    IssueType,
    Version,
    WorkflowState,
)
from ieum.modules.issues.repository import (
    CommentRepository,
    FieldDefinitionRepository,
    HistoryRepository,
    IssueFieldValueRepository,
    IssueLabelRepository,
    IssueLinkRepository,
    IssueRepository,
    IssueTypeRepository,
    SecurityLevelRepository,
    VersionRepository,
    WorkflowRepository,
)
from ieum.modules.issues.rollup import has_children, recompute_ancestors
from ieum.modules.issues.workflow import (
    TransitionContext,
    TransitionSpec,
    apply_post_functions,
    evaluate_conditions,
)
from ieum.modules.org import contracts as org
from ieum.modules.search import contracts as search

log = get_logger(__name__)

MAX_LABELS = 30
MAX_SUBTASK_DEPTH = 3

#: 사용자가 직접 고칠 수 있는 필드. 여기 없는 필드는 전용 경로로만 바뀐다
#: (state_id 는 전이로, key_seq 는 채번으로, version 은 저장 시 자동으로).
EDITABLE_FIELDS = frozenset(
    {
        "summary",
        "description",
        "assignee_id",
        "priority",
        "parent_id",
        "category_id",
        "fix_version_id",
        "start_date",
        "due_date",
        "estimate_minutes",
        "progress",
    }
)


#: PATCH 로 들어오는 값의 타입. JSON 에는 UUID 도 date 도 없으므로 여기서
#: 바꿔 준다. 문자열을 그대로 setattr 하면 (a) UUID 컬럼에 str 이 들어가
#: 값이 안 바뀌어도 "바뀐 것"으로 기록되고, (b) 잘못된 값이 드라이버
#: 오류로 500 이 된다.
_UUID_CHANGES = frozenset({"assignee_id", "parent_id", "category_id", "fix_version_id"})
_DATE_CHANGES = frozenset({"start_date", "due_date"})
_INT_CHANGES = frozenset({"priority", "estimate_minutes", "progress"})
_TEXT_CHANGES = frozenset({"summary", "description"})
#: null 로 비울 수 있는 필드. 나머지에 null 이 오면 거절한다.
_NULLABLE_CHANGES = EDITABLE_FIELDS - {"summary", "priority", "progress"}


def _bad_change(field: str, expected: str) -> ValidationError:
    return ValidationError(
        f"'{field}' 값이 {expected} 이(가) 아니다.",
        code="issues.invalid_field_value",
        details={"field": field},
    )


def _coerce_change(field: str, value: Any) -> Any:
    """PATCH 값 하나를 컬럼 타입으로 바꾼다. 못 바꾸면 422 로 거절한다."""
    if value is None:
        if field not in _NULLABLE_CHANGES:
            raise _bad_change(field, "비울 수 없는 값")
        return None
    if field in _UUID_CHANGES:
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except ValueError as exc:
            raise _bad_change(field, "UUID") from exc
    if field in _DATE_CHANGES:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise _bad_change(field, "YYYY-MM-DD 날짜") from exc
    if field in _INT_CHANGES:
        # bool 은 int 의 서브클래스다. True 가 우선순위 1 이 되면 안 된다.
        if isinstance(value, bool) or not isinstance(value, int):
            raise _bad_change(field, "정수")
        return value
    if field in _TEXT_CHANGES:
        if not isinstance(value, str):
            raise _bad_change(field, "문자열")
        # 설명은 마크다운 정본이다. 저장 전에 반드시 정규화를 지난다
        # (wiki-markdown.md 7절) — 안 그러면 에디터 왕복마다 diff 가 오염된다.
        if field == "description":
            return _normalized_body(value)
    return value


def _normalized_body(text: str | None) -> str | None:
    """마크다운 본문을 저장 형태로. None 과 빈 문자열은 그대로 둔다."""
    if text is None:
        return None
    return normalize_markdown(text) or None


@dataclass(slots=True)
class NewIssue:
    project_id: UUID
    summary: str
    type_id: UUID | None = None
    description: str | None = None
    assignee_id: UUID | None = None
    priority: int = 3
    parent_id: UUID | None = None
    due_date: date | None = None
    labels: list[str] = field(default_factory=list)
    custom_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IssueView:
    """이슈 하나를 표시하는 데 필요한 것. 라우터가 그대로 직렬화한다."""

    issue: Issue
    key: str
    state_name: str
    state_category: str
    type_name: str
    labels: list[str]
    custom_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RelatedIssue:
    link_id: UUID
    kind: str
    #: True 면 이 이슈가 관계의 출발점이다 ("blocks" vs "blocked by").
    outward: bool
    issue: Issue


@dataclass(frozen=True, slots=True)
class IssueRelations:
    parent: Issue | None
    children: list[Issue]
    links: list[RelatedIssue]


@dataclass(frozen=True, slots=True)
class IssueSummaryView:
    """목록 한 줄. 상세보다 가볍지만 화면이 필요한 건 다 들어 있다.

    key 와 상태 이름을 여기서 채운다. 클라이언트가 프로젝트·상태 목록을
    따로 받아 이어 붙이면, 목록에 없는 프로젝트의 이슈는 영영 키가 안 뜬다.
    """

    issue: Issue
    key: str
    state_name: str
    state_category: str


@dataclass(frozen=True, slots=True)
class AvailableTransition:
    id: UUID
    name: str
    to_state_id: UUID
    to_state_name: str
    #: 통과하지 못한 조건. 비어 있으면 지금 실행할 수 있다.
    blocked_by: list[str]


class SecurityLevelGuard:
    """이슈 보안 레벨. 스코프 권한을 통과한 뒤 한 번 더 거르는 관문이다.

    core.PermissionService 에 등록되어 subject=issue 로 검사할 때 호출된다.
    기동 시 한 번 등록되므로 상태를 갖지 않는다 — 세션은 호출마다 받는다.
    """

    async def allows(
        self, session: AsyncSession, actor: Actor, permission: str, subject: Any
    ) -> bool:
        if not isinstance(subject, Issue) or subject.security_level_id is None:
            return True
        level = await SecurityLevelRepository(session).get(subject.security_level_id)
        if level is None:
            # 레벨이 삭제됐으면 제한도 사라진 것으로 본다. 반대로 하면
            # 아무도 못 보는 이슈가 영구히 남는다.
            return True
        allowed_ids = {
            UUID(g["id"])
            for g in level.grantees
            if g.get("kind") in {"user", "group"} and g.get("id")
        }
        return bool(actor.principal_ids & allowed_ids)


async def resolve_mentions(
    session: AsyncSession,
    permissions: PermissionService,
    issue: Issue,
    text: str | None,
    *,
    require_internal: bool = False,
) -> list[UUID]:
    """본문의 멘션 중 **이 이슈를 볼 수 있는 사람만** 남긴다.

    권한 검사를 여기서 하는 이유는 notify 가 이슈 ACL 을 못 보기 때문이다.
    거르지 않으면 아무나 멘션해서 비공개 이슈의 제목을 알림으로 흘릴 수 있다.
    내부 노트는 내부 노트를 볼 수 있는 사람에게만 간다.
    """
    if not text:
        return []
    scope = Scope.project(issue.project_id)
    allowed: list[UUID] = []
    for user_id in extract_mentions(text):
        mentioned = await identity.load_actor(session, user_id)
        if mentioned is None or not mentioned.is_active:
            continue
        if not await permissions.has(
            session, mentioned, perms.ISSUE_VIEW, scope=scope, subject=issue
        ):
            continue
        if require_internal and not await permissions.has(
            session, mentioned, perms.COMMENT_VIEW_INTERNAL, scope=scope, subject=issue
        ):
            continue
        allowed.append(user_id)
    return allowed


class IssueService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._issues = IssueRepository(session)
        self._types = IssueTypeRepository(session)
        self._workflows = WorkflowRepository(session)
        self._labels = IssueLabelRepository(session)
        self._values = IssueFieldValueRepository(session)
        self._definitions = FieldDefinitionRepository(session)
        self._history = HistoryRepository(session)
        self._links = IssueLinkRepository(session)
        self._versions = VersionRepository(session)

    # ── 조회 ────────────────────────────────────────────────────

    async def get(self, actor: Actor, issue_id: UUID) -> IssueView:
        issue = await self._require_issue(issue_id)
        await self._perms.require(
            self._s,
            actor,
            perms.ISSUE_VIEW,
            scope=Scope.project(issue.project_id),
            subject=issue,
        )
        return await self.to_view(issue)

    async def get_by_key(self, actor: Actor, key: str) -> IssueView:
        """`PROJ-123` 형태의 표시용 키로 조회한다."""
        project_key, _, raw_seq = key.rpartition("-")
        if not project_key or not raw_seq.isdigit():
            raise ValidationError(
                "이슈 키 형식이 아니다. 예: PROJ-123",
                code="issues.invalid_key",
                details={"key": key},
            )
        project = await org.get_project_by_key(self._s, project_key)
        if project is None:
            raise NotFoundError("프로젝트를 찾을 수 없다.")
        issue = await self._issues.get_by_key_seq(project.id, int(raw_seq))
        if issue is None:
            raise NotFoundError("이슈를 찾을 수 없다.")
        await self._perms.require(
            self._s,
            actor,
            perms.ISSUE_VIEW,
            scope=Scope.project(issue.project_id),
            subject=issue,
        )
        return await self.to_view(issue)

    async def list_for(
        self,
        actor: Actor,
        request: PageRequest,
        *,
        project_id: UUID | None = None,
        include_archived: bool = False,
    ) -> Page[Issue]:
        acl = await self._perms.acl_for(self._s, actor, perms.ISSUE_VIEW)
        return await self._issues.list_page(
            request, acl=acl, project_id=project_id, include_archived=include_archived
        )

    async def list_types(self, actor: Actor, project_id: UUID) -> list[IssueType]:
        """이 프로젝트에서 쓸 수 있는 이슈 유형. 생성 폼이 이걸로 채운다."""
        await self._perms.require(self._s, actor, perms.ISSUE_VIEW, scope=Scope.project(project_id))
        return await self._types.available_for(project_id)

    async def list_field_definitions(
        self, actor: Actor, project_id: UUID, issue_type_id: UUID
    ) -> list[FieldDefinition]:
        """이 프로젝트·유형에 뜨는 커스텀 필드 정의."""
        await self._perms.require(self._s, actor, perms.ISSUE_VIEW, scope=Scope.project(project_id))
        return await self._definitions.applicable_to(
            project_id=project_id, issue_type_id=issue_type_id
        )

    async def list_versions(self, actor: Actor, project_id: UUID) -> list[Version]:
        """version 종류 커스텀 필드의 선택지."""
        await self._perms.require(self._s, actor, perms.ISSUE_VIEW, scope=Scope.project(project_id))
        return await self._versions.list_for(project_id)

    async def list_workflow_states(self, actor: Actor, project_id: UUID) -> list[WorkflowState]:
        """이 프로젝트에서 등장할 수 있는 상태. 보드 컬럼·필터 칩이 쓴다."""
        await self._perms.require(self._s, actor, perms.ISSUE_VIEW, scope=Scope.project(project_id))
        types = await self._types.available_for(project_id)
        seen: dict[UUID, WorkflowState] = {}
        for workflow_id in dict.fromkeys(t.workflow_id for t in types):
            for state in await self._workflows.states_of(workflow_id):
                seen.setdefault(state.id, state)
        return sorted(seen.values(), key=lambda s: (s.position, s.name))

    async def to_summaries(self, issues: list[Issue]) -> list[IssueSummaryView]:
        """목록 행을 만든다. 프로젝트·상태를 각각 **한 번씩만** 조회한다."""
        if not issues:
            return []
        projects = await org.get_projects(self._s, [i.project_id for i in issues])
        states = await self._workflows.states_by_ids([i.state_id for i in issues])
        rows: list[IssueSummaryView] = []
        for issue in issues:
            project = projects.get(issue.project_id)
            state = states.get(issue.state_id)
            rows.append(
                IssueSummaryView(
                    issue=issue,
                    key=f"{project.key}-{issue.key_seq}" if project else str(issue.key_seq),
                    state_name=state.name if state else "",
                    state_category=state.category if state else "",
                )
            )
        return rows

    # ── 생성 ────────────────────────────────────────────────────

    async def create(self, actor: Actor, payload: NewIssue) -> IssueView:
        await self._perms.require(
            self._s, actor, perms.ISSUE_CREATE, scope=Scope.project(payload.project_id)
        )
        return await self.create_authorized(actor, payload)

    async def create_authorized(
        self, actor: Actor, payload: NewIssue, *, anonymous: bool = False
    ) -> IssueView:
        """`issue.create` 를 **보지 않고** 만든다. 호출자가 이미 판단했을 때만.

        포털 제출이 여기로 온다. 고객 계정은 프로젝트 역할이 없으므로
        `issue.create` 를 절대 통과하지 못하고, 통과하도록 역할을 주면 그
        고객은 포털이 열어 둔 요청 유형 밖의 이슈도 만들 수 있게 된다 —
        권한을 넓혀서 기능을 여는 것은 거꾸로다.

        그래서 권한의 근거를 옮긴다: "이 포털이 이 요청 유형을 열어 두었다"
        가 근거이고, 그 판단은 `desk` 가 한다. 여기서는 **만드는 일**만 한다.

        `_` 로 시작하지 않는 것은 다른 모듈이 `contracts` 를 통해 쓰기
        때문이다. 라우터에서 직접 부르지 않는다 — 부르면 권한 없는 생성
        경로가 API 표면에 그대로 뚫린다.

        `anonymous` 는 게스트 요청용이다. 신고자를 NULL 로 둔다 — 계정이
        없는 사람을 `reporter_id` 에 넣을 수 없고(FK), 아무 계정에 붙이면
        증명 없이 남의 이름으로 티켓을 만드는 일이 된다.
        """
        project = await org.get_project(self._s, payload.project_id)
        if project is None:
            raise NotFoundError("프로젝트를 찾을 수 없다.")
        if project.is_archived:
            raise ConflictError(
                "아카이브된 프로젝트에는 이슈를 만들 수 없다.",
                code="issues.project_archived",
            )

        issue_type = await self._resolve_type(payload.project_id, payload.type_id)
        initial = await self._workflows.initial_state(issue_type.workflow_id)
        if initial is None:
            raise ConflictError(
                "워크플로우에 시작 상태가 없다.", code="issues.workflow_has_no_initial_state"
            )

        summary = self._validate_summary(payload.summary)
        self._validate_priority(payload.priority)
        labels = self._validate_labels(payload.labels)

        if payload.parent_id is not None:
            await self._validate_parent(payload.project_id, payload.parent_id)
        if payload.assignee_id is not None:
            await self._perms.require(
                self._s,
                actor,
                perms.ISSUE_ASSIGN,
                scope=Scope.project(payload.project_id),
            )

        validated = await self._validate_custom_fields(
            project_id=payload.project_id,
            issue_type_id=issue_type.id,
            values=payload.custom_fields,
            require_all=True,
        )

        # 채번은 UPDATE ... RETURNING 으로 원자적으로 한다 (data-model).
        key_seq = await org.next_issue_number(self._s, payload.project_id)

        issue = Issue(
            project_id=payload.project_id,
            key_seq=key_seq,
            type_id=issue_type.id,
            state_id=initial.id,
            summary=summary,
            description=_normalized_body(payload.description),
            reporter_id=None if anonymous else actor.user_id,
            assignee_id=payload.assignee_id,
            priority=payload.priority,
            parent_id=payload.parent_id,
            due_date=payload.due_date,
        )
        self._issues.add(issue)
        await self._s.flush()

        if labels:
            await self._labels.replace(issue.id, labels)
        for key, value in validated.items():
            await self._values.set(issue.id, key, value)
        # autoflush 를 꺼 뒀으므로 아래 to_view 가 방금 쓴 값을 보려면 직접 밀어야 한다.
        await self._s.flush()

        issue_key = f"{project.key}-{key_seq}"
        publish(
            self._s,
            issue_events.IssueCreated(
                aggregate_id=issue.id,
                project_id=issue.project_id,
                issue_key=issue_key,
                summary=summary,
                actor_id=actor.user_id,
                assignee_id=issue.assignee_id,
                reporter_id=issue.reporter_id,
                mentioned_ids=await resolve_mentions(
                    self._s, self._perms, issue, issue.description
                ),
            ),
        )
        if issue.parent_id is not None:
            await recompute_ancestors(self._s, issue.id)
        await self._reindex(issue, labels=labels)
        log.info("issue.created", issue_key=issue_key, actor=str(actor.user_id))
        return await self.to_view(issue)

    # ── 수정 ────────────────────────────────────────────────────

    async def update(
        self,
        actor: Actor,
        issue_id: UUID,
        changes: dict[str, Any],
        *,
        expected_version: int | None = None,
        labels: list[str] | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> IssueView:
        issue = await self._require_issue(issue_id)
        await self._require_edit(actor, issue)
        self._check_version(issue, expected_version)

        unknown = sorted(set(changes) - EDITABLE_FIELDS)
        if unknown:
            raise ValidationError(
                "수정할 수 없는 필드다.",
                code="issues.field_not_editable",
                details={"fields": unknown},
            )

        changes = {name: _coerce_change(name, value) for name, value in changes.items()}

        # 자식이 있으면 진행률은 계산값이다. 손으로 고쳐 봐야 다음 롤업에
        # 덮어써지므로, 조용히 되돌리는 대신 지금 거절한다.
        if "progress" in changes and await has_children(self._s, issue.id):
            raise ValidationError(
                "하위 이슈가 있는 이슈의 진행률은 직접 바꿀 수 없다.",
                code="issues.progress_is_derived",
            )

        if "assignee_id" in changes and changes["assignee_id"] != issue.assignee_id:
            await self._perms.require(
                self._s, actor, perms.ISSUE_ASSIGN, scope=Scope.project(issue.project_id)
            )
        if "summary" in changes:
            changes["summary"] = self._validate_summary(changes["summary"])
        if "priority" in changes:
            self._validate_priority(changes["priority"])
        if "progress" in changes:
            self._validate_progress(changes["progress"])
        if changes.get("parent_id") is not None:
            await self._validate_parent(issue.project_id, changes["parent_id"], child_id=issue.id)

        diff: list[dict[str, Any]] = []
        for name, new_value in changes.items():
            old_value = getattr(issue, name)
            if old_value == new_value:
                continue
            setattr(issue, name, new_value)
            diff.append({"field": name, "from": _jsonable(old_value), "to": _jsonable(new_value)})

        if labels is not None:
            before = await self._labels.for_issue(issue.id)
            after = self._validate_labels(labels)
            if sorted(before) != sorted(after):
                await self._labels.replace(issue.id, after)
                diff.append({"field": "labels", "from": before, "to": after})

        if custom_fields:
            validated = await self._validate_custom_fields(
                project_id=issue.project_id,
                issue_type_id=issue.type_id,
                values=custom_fields,
                require_all=False,
            )
            current = await self._values.for_issue(issue.id)
            for key, value in validated.items():
                if current.get(key) == value:
                    continue
                await self._values.set(issue.id, key, value)
                diff.append({"field": f"cf.{key}", "from": current.get(key), "to": value})

        if diff:
            issue.version += 1
            self._history.record(issue_id=issue.id, actor_id=actor.user_id, changes=diff)
            publish(
                self._s,
                issue_events.IssueUpdated(
                    aggregate_id=issue.id,
                    project_id=issue.project_id,
                    issue_key=await self._key_of(issue),
                    summary=issue.summary,
                    actor_id=actor.user_id,
                    assignee_id=issue.assignee_id,
                    reporter_id=issue.reporter_id,
                    changed_fields=[c["field"] for c in diff],
                    # 설명이 바뀐 경우에만 본다. 우선순위만 고쳐도 멘션 알림이
                    # 다시 나가면 사람들이 알림을 끈다.
                    mentioned_ids=(
                        await resolve_mentions(self._s, self._perms, issue, issue.description)
                        if any(c["field"] == "description" for c in diff)
                        else []
                    ),
                ),
            )
            await self._s.flush()

        # 진행률·추정·부모가 바뀌면 조상 계산이 달라진다.
        if any(c["field"] in {"progress", "estimate_minutes", "parent_id"} for c in diff):
            await recompute_ancestors(self._s, issue.id)
        await self._reindex(issue)
        return await self.to_view(issue)

    async def archive(self, actor: Actor, issue_id: UUID) -> Issue:
        issue = await self._require_issue(issue_id)
        await self._perms.require(
            self._s,
            actor,
            perms.ISSUE_DELETE,
            scope=Scope.project(issue.project_id),
            subject=issue,
        )
        if issue.is_archived:
            return issue

        children = await self._issues.children_of(issue.id)
        if children:
            raise ConflictError(
                "하위 이슈를 먼저 정리해야 한다.",
                code="issues.has_active_children",
                details={"count": len(children)},
            )

        issue.archived_at = utcnow()
        issue.version += 1
        await self._s.flush()
        # 휴지통으로 간 이슈는 검색에서 빠진다. 남겨 두면 열 수 없는 결과가
        # 목록에 뜬다.
        await self._reindex(issue)
        # 아카이브된 자식은 롤업에서 빠진다. 취소한 일 때문에 부모가 영원히
        # 100% 가 안 되면 숫자를 아무도 안 믿는다.
        await recompute_ancestors(self._s, issue.id)
        publish(
            self._s,
            issue_events.IssueArchived(
                aggregate_id=issue.id,
                project_id=issue.project_id,
                issue_key=await self._key_of(issue),
                summary=issue.summary,
                actor_id=actor.user_id,
                assignee_id=issue.assignee_id,
                reporter_id=issue.reporter_id,
            ),
        )
        return issue

    # ── 전이 ────────────────────────────────────────────────────

    async def available_transitions(
        self, actor: Actor, issue_id: UUID
    ) -> list[AvailableTransition]:
        """지금 이 이슈에서 가능한 전이. 막힌 이유까지 함께 준다.

        UI 가 버튼을 비활성화하고 왜 안 되는지 보여줄 수 있어야 한다.
        """
        issue = await self._require_issue(issue_id)
        await self._perms.require(
            self._s,
            actor,
            perms.ISSUE_VIEW,
            scope=Scope.project(issue.project_id),
            subject=issue,
        )
        issue_type = await self._types.get(issue.type_id)
        assert issue_type is not None
        rows = await self._workflows.transitions_from(issue_type.workflow_id, issue.state_id)

        result: list[AvailableTransition] = []
        for row in rows:
            target = await self._workflows.state(row.to_state_id)
            if target is None:
                continue
            ctx = await self._transition_context(actor, issue, target, inputs={})
            spec = _spec_of(row)
            result.append(
                AvailableTransition(
                    id=row.id,
                    name=row.name,
                    to_state_id=target.id,
                    to_state_name=target.name,
                    blocked_by=evaluate_conditions(spec, ctx),
                )
            )
        return result

    async def transition(
        self,
        actor: Actor,
        issue_id: UUID,
        transition_id: UUID,
        *,
        inputs: dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> IssueView:
        """상태 전이 (overview.md 요청 처리 흐름).

        순서가 중요하다: 권한 → 워크플로우 규칙 → 적용 → 이력 → 이벤트.
        규칙 검증 전에 적용하면 롤백해도 후처리 부수효과가 남는다.
        """
        issue = await self._require_issue(issue_id)
        await self._perms.require(
            self._s,
            actor,
            perms.ISSUE_TRANSITION,
            scope=Scope.project(issue.project_id),
            subject=issue,
        )
        self._check_version(issue, expected_version)

        row = await self._workflows.transition(transition_id)
        issue_type = await self._types.get(issue.type_id)
        assert issue_type is not None
        if row is None or row.workflow_id != issue_type.workflow_id:
            raise NotFoundError("전이를 찾을 수 없다.")
        if row.from_state_id is not None and row.from_state_id != issue.state_id:
            raise ConflictError(
                "현재 상태에서 실행할 수 없는 전이다.",
                code="issues.transition_not_available",
            )

        target = await self._workflows.state(row.to_state_id)
        if target is None:
            raise ConflictError("전이 대상 상태가 없다.", code="issues.invalid_transition")

        current = await self._workflows.state(issue.state_id)

        # **다른 모듈이 막을 수 있는 자리** (C12 승인). 조건 평가보다 먼저
        # 본다: 관문은 기본 거절이고, 조건은 관리자가 켜야 걸린다 —
        # 순서를 뒤집으면 조건을 통과한 전이가 관문에서 막히는 것을
        # "조건을 만족하지 않는다" 로 잘못 말하게 된다. 막은 쪽이 자기
        # 문구와 코드를 들고 예외를 던진다 (`gates.py`).
        await gates.check(
            self._s,
            actor,
            gates.Gate(
                issue_id=issue.id,
                project_id=issue.project_id,
                from_category=current.category if current else None,
                to_category=target.category,
            ),
        )

        ctx = await self._transition_context(actor, issue, target, inputs=inputs or {})
        spec = _spec_of(row)

        blocked = evaluate_conditions(spec, ctx)
        if blocked:
            raise PermissionDeniedError(
                "전이 조건을 만족하지 않는다.",
                code="issues.transition_blocked",
                details={"conditions": blocked},
            )

        diff: list[dict[str, Any]] = [
            {
                "field": "status",
                "from": current.name if current else None,
                "to": target.name,
            }
        ]
        issue.state_id = target.id

        for change in apply_post_functions(spec, ctx):
            if not hasattr(issue, change.field):
                raise ValidationError(
                    f"후처리가 존재하지 않는 필드를 건드린다: {change.field}",
                    code="issues.invalid_workflow_rule",
                )
            old = getattr(issue, change.field)
            if old == change.value:
                continue
            setattr(issue, change.field, change.value)
            diff.append(
                {
                    "field": change.field,
                    "from": _jsonable(old),
                    "to": _jsonable(change.value),
                }
            )

        issue.version += 1
        self._history.record(issue_id=issue.id, actor_id=actor.user_id, changes=diff)
        publish(
            self._s,
            issue_events.IssueTransitioned(
                aggregate_id=issue.id,
                project_id=issue.project_id,
                issue_key=await self._key_of(issue),
                summary=issue.summary,
                actor_id=actor.user_id,
                from_state=current.name if current else None,
                to_state=target.name,
                to_state_category=target.category,
                assignee_id=issue.assignee_id,
                reporter_id=issue.reporter_id,
            ),
        )
        await self._s.flush()
        # 전이 후처리가 progress 를 바꾼다(Resolve → 100). 부모도 따라 움직여야 한다.
        await recompute_ancestors(self._s, issue.id)
        return await self.to_view(issue)

    # ── 관계 ────────────────────────────────────────────────────

    async def relations(self, actor: Actor, issue_id: UUID) -> IssueRelations:
        """부모·자식·링크를 한 번에. 상세 화면의 관계 패널이 쓴다."""
        issue = await self._require_issue(issue_id)
        await self._perms.require(
            self._s,
            actor,
            perms.ISSUE_VIEW,
            scope=Scope.project(issue.project_id),
            subject=issue,
        )
        parent = None if issue.parent_id is None else await self._issues.get(issue.parent_id)
        children = await self._issues.children_of(issue.id)
        links = await self._links.for_issue(issue.id)

        # 링크가 가리키는 상대 이슈를 한 번에 읽는다. 링크마다 조회하면
        # 관계가 열 개인 이슈에서 왕복이 열 번 난다.
        other_ids = {
            (link.to_issue_id if link.from_issue_id == issue.id else link.from_issue_id)
            for link in links
        }
        others = {row.id: row for row in await self._issues.get_many(sorted(other_ids))}

        related: list[RelatedIssue] = []
        for link in links:
            outward = link.from_issue_id == issue.id
            other = others.get(link.to_issue_id if outward else link.from_issue_id)
            if other is None:
                continue
            related.append(
                RelatedIssue(
                    link_id=link.id,
                    kind=link.kind,
                    outward=outward,
                    issue=other,
                )
            )

        # 볼 수 없는 이슈는 관계에서도 감춘다. 제목만 보여도 비공개 이슈의
        # 존재와 내용이 새어 나간다.
        acl_children = [c for c in children if await self._can_view(actor, c)]
        acl_related = [r for r in related if await self._can_view(actor, r.issue)]
        visible_parent = (
            parent if parent is not None and await self._can_view(actor, parent) else None
        )
        return IssueRelations(
            parent=visible_parent,
            children=acl_children,
            links=acl_related,
        )

    async def unlink(self, actor: Actor, link_id: UUID) -> None:
        link = await self._links.get(link_id)
        if link is None:
            raise NotFoundError("관계를 찾을 수 없다.")
        issue = await self._require_issue(link.from_issue_id)
        await self._perms.require(
            self._s, actor, perms.ISSUE_LINK, scope=Scope.project(issue.project_id), subject=issue
        )
        await self._s.delete(link)

    async def _can_view(self, actor: Actor, issue: Issue) -> bool:
        return await self._perms.has(
            self._s,
            actor,
            perms.ISSUE_VIEW,
            scope=Scope.project(issue.project_id),
            subject=issue,
        )

    async def link(self, actor: Actor, from_id: UUID, to_id: UUID, kind: str) -> IssueLink:
        if from_id == to_id:
            raise ValidationError("자기 자신과는 연결할 수 없다.", code="issues.self_link")
        source = await self._require_issue(from_id)
        target = await self._require_issue(to_id)
        for issue in (source, target):
            await self._perms.require(
                self._s,
                actor,
                perms.ISSUE_LINK,
                scope=Scope.project(issue.project_id),
                subject=issue,
            )
        if await self._links.exists(from_id, to_id, kind):
            raise ConflictError("이미 있는 관계다.", code="issues.link_exists")
        return self._links.add(IssueLink(from_issue_id=from_id, to_issue_id=to_id, kind=kind))

    # ── 내부 ────────────────────────────────────────────────────

    async def _reindex(self, issue: Issue, *, labels: list[str] | None = None) -> None:
        """검색 색인을 원본과 같은 트랜잭션에서 맞춘다.

        읽기 경로(`to_view`)에 넣지 않는다 — 목록을 그릴 때마다 색인에
        쓰게 된다.
        """
        project = await org.get_project(self._s, issue.project_id)
        await index_issue(
            self._s,
            issue,
            project_key=project.key if project else "",
            labels=labels if labels is not None else await self._labels.for_issue(issue.id),
        )

    async def _require_issue(self, issue_id: UUID) -> Issue:
        issue = await self._issues.get(issue_id)
        if issue is None:
            raise NotFoundError("이슈를 찾을 수 없다.")
        return issue

    async def _require_edit(self, actor: Actor, issue: Issue) -> None:
        """모든 이슈 수정 권한이 없으면 '자신이 보고한 이슈' 권한을 본다."""
        scope = Scope.project(issue.project_id)
        if await self._perms.has(self._s, actor, perms.ISSUE_EDIT, scope=scope, subject=issue):
            return
        if issue.reporter_id == actor.user_id and await self._perms.has(
            self._s, actor, perms.ISSUE_EDIT_OWN, scope=scope, subject=issue
        ):
            return
        raise PermissionDeniedError(
            "이슈를 수정할 권한이 없다.", details={"permission": perms.ISSUE_EDIT}
        )

    def _check_version(self, issue: Issue, expected: int | None) -> None:
        """If-Match 불일치는 409 다 (conventions.md API 규약)."""
        if expected is not None and expected != issue.version:
            raise OptimisticLockError(
                "다른 사용자가 먼저 수정했다.",
                details={"expected": expected, "current": issue.version},
            )

    async def _resolve_type(self, project_id: UUID, type_id: UUID | None) -> IssueType:
        if type_id is None:
            found = await self._types.default_for(project_id)
            if found is None:
                raise ConflictError("사용할 수 있는 이슈 유형이 없다.", code="issues.no_issue_type")
            return found
        issue_type = await self._types.get(type_id)
        if issue_type is None:
            raise NotFoundError("이슈 유형을 찾을 수 없다.")
        if issue_type.project_id is not None and issue_type.project_id != project_id:
            raise ValidationError(
                "이 프로젝트에서 쓸 수 없는 이슈 유형이다.",
                code="issues.issue_type_not_available",
            )
        return issue_type

    def _validate_summary(self, summary: str) -> str:
        text = summary.strip()
        if not text:
            raise ValidationError("제목은 비울 수 없다.", code="issues.summary_required")
        if len(text) > 500:
            raise ValidationError(
                "제목은 500자 이하여야 한다.",
                code="issues.summary_too_long",
                details={"length": len(text)},
            )
        return text

    def _validate_priority(self, priority: int) -> None:
        if not 1 <= priority <= 5:
            raise ValidationError(
                "우선순위는 1~5 여야 한다.",
                code="issues.invalid_priority",
                details={"value": priority},
            )

    def _validate_progress(self, progress: int) -> None:
        if not 0 <= progress <= 100:
            raise ValidationError(
                "진행률은 0~100 이어야 한다.",
                code="issues.invalid_progress",
                details={"value": progress},
            )

    def _validate_labels(self, labels: list[str]) -> list[str]:
        cleaned = [label.strip() for label in labels if label.strip()]
        if len(cleaned) > MAX_LABELS:
            raise ValidationError(
                f"라벨은 {MAX_LABELS}개 이하여야 한다.",
                code="issues.too_many_labels",
                details={"max": MAX_LABELS},
            )
        too_long = [label for label in cleaned if len(label) > 100]
        if too_long:
            raise ValidationError(
                "라벨이 너무 길다.", code="issues.label_too_long", details={"labels": too_long}
            )
        return sorted(set(cleaned))

    async def _validate_parent(
        self, project_id: UUID, parent_id: UUID, *, child_id: UUID | None = None
    ) -> None:
        parent = await self._issues.get(parent_id)
        if parent is None:
            raise NotFoundError("상위 이슈를 찾을 수 없다.")
        if parent.project_id != project_id:
            raise ValidationError(
                "다른 프로젝트의 이슈를 상위로 둘 수 없다.",
                code="issues.parent_in_other_project",
            )
        if parent.is_archived:
            raise ConflictError(
                "아카이브된 이슈를 상위로 둘 수 없다.", code="issues.parent_archived"
            )
        if child_id is not None:
            # 순환을 막는다. A→B→A 가 되면 롤업 계산이 무한 루프에 빠진다.
            ancestors = await self._issues.ancestor_ids(parent_id)
            if child_id in ancestors:
                raise ValidationError("부모-자식 관계가 순환한다.", code="issues.parent_cycle")
        depth = len(await self._issues.ancestor_ids(parent_id))
        if depth >= MAX_SUBTASK_DEPTH:
            raise ValidationError(
                f"하위 이슈는 {MAX_SUBTASK_DEPTH}단계까지다.",
                code="issues.subtask_too_deep",
                details={"max_depth": MAX_SUBTASK_DEPTH},
            )

    async def _validate_custom_fields(
        self,
        *,
        project_id: UUID,
        issue_type_id: UUID,
        values: dict[str, Any],
        require_all: bool,
    ) -> dict[str, Any]:
        definitions = await self._definitions.applicable_to(
            project_id=project_id, issue_type_id=issue_type_id
        )
        by_key = {d.key: d for d in definitions}

        unknown = sorted(set(values) - set(by_key))
        if unknown:
            raise ValidationError(
                "이 이슈 유형에 없는 커스텀 필드다.",
                code="issues.unknown_custom_field",
                details={"fields": unknown, "available": sorted(by_key)},
            )

        validated: dict[str, Any] = {}
        for key, raw in values.items():
            definition = by_key[key]
            try:
                validated[key] = validate_value(definition.kind, raw, definition.config)
            except ValidationError as exc:
                # 어느 필드에서 났는지 알려주지 않으면 폼에서 표시할 수 없다.
                exc.details["field"] = key
                raise

        await self._check_field_references(by_key, validated, project_id=project_id)

        if require_all:
            missing = sorted(
                d.key for d in definitions if d.is_required and validated.get(d.key) is None
            )
            if missing:
                raise ValidationError(
                    "필수 커스텀 필드가 비었다.",
                    code="issues.required_custom_field_missing",
                    details={"fields": missing},
                )
        return validated

    async def _check_field_references(
        self,
        by_key: dict[str, FieldDefinition],
        validated: dict[str, Any],
        *,
        project_id: UUID,
    ) -> None:
        """user·version 값이 실존하는지 본다.

        밸리데이터는 UUID 모양만 본다 (DB 를 모른다). 여기서 확인하지 않으면
        아무 UUID 나 저장되고, 나중에 화면이 이름을 못 찾아 날 UUID 를 띄운다.
        종류별로 **한 번씩만** 조회한다 — 필드 개수만큼 왕복하지 않는다.
        """
        wanted: dict[str, list[tuple[str, UUID]]] = {"user": [], "version": []}
        for key, value in validated.items():
            kind = by_key[key].kind
            if value is None or kind not in wanted:
                continue
            wanted[kind].append((key, UUID(str(value))))

        if wanted["user"]:
            found = await identity.get_users(self._s, [uid for _, uid in wanted["user"]])
            self._fail_missing_refs(wanted["user"], set(found), "사용자를 찾을 수 없다.")

        if wanted["version"]:
            versions = await self._versions.list_for(project_id)
            # 다른 프로젝트의 버전은 없는 것과 같다. 목록에 안 뜨는 값을
            # API 로 밀어 넣어 프로젝트 경계를 넘지 못하게 한다.
            self._fail_missing_refs(
                wanted["version"], {v.id for v in versions}, "버전을 찾을 수 없다."
            )

    @staticmethod
    def _fail_missing_refs(pairs: list[tuple[str, UUID]], known: set[UUID], message: str) -> None:
        missing = sorted({key for key, ref in pairs if ref not in known})
        if missing:
            raise ValidationError(
                message,
                code="issues.invalid_field_reference",
                details={"fields": missing},
            )

    async def _transition_context(
        self,
        actor: Actor,
        issue: Issue,
        target: WorkflowState,
        *,
        inputs: dict[str, Any],
    ) -> TransitionContext:
        current = await self._workflows.state(issue.state_id)
        granted = await self._perms.resolver.permissions_in_scope(
            self._s, actor, Scope.project(issue.project_id)
        )
        return TransitionContext(
            actor_id=actor.user_id,
            actor_permissions=granted,
            issue_id=issue.id,
            project_id=issue.project_id,
            assignee_id=issue.assignee_id,
            reporter_id=issue.reporter_id,
            from_state=current.name if current else None,
            to_state=target.name,
            to_state_category=target.category,
            inputs=inputs,
        )

    async def _key_of(self, issue: Issue) -> str:
        project = await org.get_project(self._s, issue.project_id)
        return f"{project.key}-{issue.key_seq}" if project else str(issue.key_seq)

    async def to_view(self, issue: Issue) -> IssueView:
        state = await self._workflows.state(issue.state_id)
        issue_type = await self._types.get(issue.type_id)
        return IssueView(
            issue=issue,
            key=await self._key_of(issue),
            state_name=state.name if state else "",
            state_category=state.category if state else "",
            type_name=issue_type.name if issue_type else "",
            labels=await self._labels.for_issue(issue.id),
            custom_fields=await self._values.for_issue(issue.id),
        )


async def index_issue(
    session: AsyncSession, issue: Issue, *, project_key: str, labels: list[str] | None = None
) -> None:
    """이슈 하나를 검색 색인에 반영한다. 원본과 **같은 트랜잭션**이다.

    워커로 미루면 방금 만든 이슈가 검색에 안 나온다. 색인이 실패해도 본업이
    실패해서는 안 되지만, 여기서는 같은 트랜잭션이라 함께 롤백되는 편이
    낫다 — 색인만 남아 유령 결과가 되는 것보다 낫다.

    보안 레벨이 걸린 이슈는 볼 수 있는 주체를 함께 넣는다. 스코프만으로
    거르면 검색이 보안 레벨을 우회하는 통로가 된다.
    """
    if issue.is_archived:
        await search.remove_document(session, kind=search.ISSUE, entity_id=issue.id)
        return

    restricted: list[UUID] | None = None
    if issue.security_level_id is not None:
        level = await SecurityLevelRepository(session).get(issue.security_level_id)
        if level is not None:
            restricted = [
                UUID(g["id"])
                for g in level.grantees
                if g.get("kind") in {"user", "group"} and g.get("id")
            ]

    body_parts = [issue.description or ""]
    if labels:
        body_parts.append(" ".join(labels))
    await search.index_document(
        session,
        kind=search.ISSUE,
        entity_id=issue.id,
        scope_kind="project",
        scope_id=issue.project_id,
        ref=f"{project_key}-{issue.key_seq}",
        title=issue.summary,
        body=to_plaintext("\n\n".join(p for p in body_parts if p)),
        restricted_to=restricted,
        updated_at=issue.updated_at,
    )


class CommentService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._comments = CommentRepository(session)
        self._issues = IssueRepository(session)

    async def add(
        self, actor: Actor, issue_id: UUID, body: str, *, is_internal: bool = False
    ) -> IssueComment:
        issue = await self._require_issue(issue_id)
        scope = Scope.project(issue.project_id)
        await self._perms.require(self._s, actor, perms.COMMENT_ADD, scope=scope, subject=issue)
        if is_internal:
            # 내부 노트를 볼 수 없는 사람이 쓸 수는 더더욱 없다.
            await self._perms.require(
                self._s, actor, perms.COMMENT_VIEW_INTERNAL, scope=scope, subject=issue
            )

        text = normalize_markdown(body)
        if not text:
            raise ValidationError("내용을 비울 수 없다.", code="issues.comment_empty")

        comment = self._comments.add(
            IssueComment(
                issue_id=issue_id,
                author_id=actor.user_id,
                body=text,
                is_internal=is_internal,
            )
        )
        await self._s.flush()

        project = await org.get_project(self._s, issue.project_id)
        publish(
            self._s,
            issue_events.IssueCommented(
                aggregate_id=issue.id,
                project_id=issue.project_id,
                issue_key=f"{project.key}-{issue.key_seq}" if project else "",
                summary=issue.summary,
                comment_id=comment.id,
                actor_id=actor.user_id,
                is_internal=is_internal,
                assignee_id=issue.assignee_id,
                reporter_id=issue.reporter_id,
                mentioned_ids=await resolve_mentions(
                    self._s, self._perms, issue, text, require_internal=is_internal
                ),
            ),
        )
        return comment

    async def list_for(self, actor: Actor, issue_id: UUID) -> list[IssueComment]:
        issue = await self._require_issue(issue_id)
        scope = Scope.project(issue.project_id)
        await self._perms.require(self._s, actor, perms.ISSUE_VIEW, scope=scope, subject=issue)
        # 내부 노트 포함 여부는 SQL 단계에서 결정한다. 가져와서 거르면
        # 실수 한 번에 고객에게 노출된다.
        include_internal = await self._perms.has(
            self._s, actor, perms.COMMENT_VIEW_INTERNAL, scope=scope, subject=issue
        )
        return await self._comments.list_for_issue(issue_id, include_internal=include_internal)

    async def edit(self, actor: Actor, comment_id: UUID, body: str) -> IssueComment:
        comment = await self._comments.get(comment_id)
        if comment is None or comment.is_archived:
            raise NotFoundError("코멘트를 찾을 수 없다.")
        issue = await self._require_issue(comment.issue_id)
        scope = Scope.project(issue.project_id)

        if not await self._perms.has(
            self._s, actor, perms.COMMENT_EDIT_ANY, scope=scope, subject=issue
        ):
            if comment.author_id != actor.user_id:
                raise PermissionDeniedError(
                    "타인의 코멘트를 수정할 권한이 없다.",
                    details={"permission": perms.COMMENT_EDIT_ANY},
                )
            await self._perms.require(
                self._s, actor, perms.COMMENT_EDIT_OWN, scope=scope, subject=issue
            )

        text = normalize_markdown(body)
        if not text:
            raise ValidationError("내용을 비울 수 없다.", code="issues.comment_empty")
        comment.body = text
        comment.edited_at = utcnow()
        return comment

    async def _require_issue(self, issue_id: UUID) -> Issue:
        issue = await self._issues.get(issue_id)
        if issue is None:
            raise NotFoundError("이슈를 찾을 수 없다.")
        return issue


def _spec_of(row: Any) -> TransitionSpec:
    return TransitionSpec(
        id=row.id,
        name=row.name,
        from_state_id=row.from_state_id,
        to_state_id=row.to_state_id,
        conditions=list(row.conditions or []),
        post_functions=list(row.post_functions or []),
    )


def _jsonable(value: Any) -> Any:
    """이력 JSONB 에 담을 수 있는 형태로."""
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value

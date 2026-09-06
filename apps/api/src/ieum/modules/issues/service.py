"""issues 비즈니스 로직.

권한 검사·도메인 규칙·트랜잭션 경계·이벤트 발행이 여기 있다.
다른 모듈은 contracts 로만 부른다 (절대규칙 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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
from ieum.core.outbox import publish
from ieum.core.pagination import Page, PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.issues import events as issue_events
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.fields import validate_value
from ieum.modules.issues.models import (
    Issue,
    IssueComment,
    IssueLink,
    IssueType,
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
    WorkflowRepository,
)
from ieum.modules.issues.workflow import (
    TransitionContext,
    TransitionSpec,
    apply_post_functions,
    evaluate_conditions,
)
from ieum.modules.org import contracts as org

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
        return await self._to_view(issue)

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
        return await self._to_view(issue)

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

    # ── 생성 ────────────────────────────────────────────────────

    async def create(self, actor: Actor, payload: NewIssue) -> IssueView:
        await self._perms.require(
            self._s, actor, perms.ISSUE_CREATE, scope=Scope.project(payload.project_id)
        )
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
            description=payload.description,
            reporter_id=actor.user_id,
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
        # autoflush 를 꺼 뒀으므로 아래 _to_view 가 방금 쓴 값을 보려면 직접 밀어야 한다.
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
            ),
        )
        log.info("issue.created", issue_key=issue_key, actor=str(actor.user_id))
        return await self._to_view(issue)

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
                ),
            )
            await self._s.flush()
        return await self._to_view(issue)

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
        return await self._to_view(issue)

    # ── 관계 ────────────────────────────────────────────────────

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

    async def _to_view(self, issue: Issue) -> IssueView:
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

        text = body.strip()
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

        text = body.strip()
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

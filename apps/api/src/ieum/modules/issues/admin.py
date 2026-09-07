"""워크플로우 조회와 커스텀 필드 정의 관리 (관리 콘솔).

`service.py` 가 이미 크다. 이 모듈은 관심사가 다르다 — 이슈를 다루는 것이
아니라 이슈가 따르는 **정의**를 다룬다. `bulk.py`·`worklog.py` 와 같은 자리다.

워크플로우는 **읽기만** 한다. 상태를 지우거나 초기 상태를 옮기는 일은 이미
그 상태에 있는 이슈를 어디로 보낼지 정해야 하고, 전이 규칙과 "워크플로우당
초기 상태 하나" 제약을 함께 지켜야 한다. 절반만 만든 편집은 없는 편집보다
나쁘다 — 눌러서 깨지는 것보다 못 누르는 것이 낫다.

필드 정의는 만들고 고치고 지운다. 여기가 `python -m ieum.cli seed-fields` 를
대신하는 자리다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ConflictError, NotFoundError, ValidationError
from ieum.core.permissions import PermissionService, Scope
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.models import (
    FIELD_KINDS,
    FieldDefinition,
    IssueType,
    Workflow,
    WorkflowState,
    WorkflowTransition,
)
from ieum.modules.issues.repository import FieldDefinitionRepository, WorkflowRepository

#: 필드 키. IQL 이 식별자로 받아 쓰고(`cf_severity = "high"`), DB CHECK 가
#: 소문자를 요구한다. 사람이 고칠 수 없는 값이라 만들 때 한 번만 검사한다.
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

#: 선택지를 요구하는 종류. 빈 목록이면 위젯이 빈 드롭다운을 그리고, 그 필드는
#: 영원히 채울 수 없다.
NEEDS_OPTIONS = ("select", "multi_select")


@dataclass(frozen=True, slots=True)
class WorkflowView:
    """목록 한 줄. 고칠 때 무엇이 영향을 받는지까지 보여 준다."""

    workflow: Workflow
    states: int
    transitions: int
    #: 이 워크플로우를 쓰는 이슈 유형 이름.
    used_by: list[str]


@dataclass(frozen=True, slots=True)
class WorkflowDetail:
    workflow: Workflow
    states: list[WorkflowState]
    transitions: list[WorkflowTransition]
    types: list[IssueType]


@dataclass(frozen=True, slots=True)
class FieldView:
    definition: FieldDefinition
    #: 이 필드에 값을 넣은 이슈 수. 지우기 전에 알아야 한다.
    values: int


class WorkflowCatalogService:
    """워크플로우 조회. **편집은 없다** (모듈 도크스트링 참고)."""

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._workflows = WorkflowRepository(session)

    async def list_all(self, actor: Actor) -> list[WorkflowView]:
        await self._require(actor)
        workflows = await self._workflows.all_workflows()
        states = await self._workflows.state_counts()
        transitions = await self._workflows.transition_counts()
        return [
            WorkflowView(
                workflow=workflow,
                states=states.get(workflow.id, 0),
                transitions=transitions.get(workflow.id, 0),
                used_by=[t.name for t in await self._workflows.types_using(workflow.id)],
            )
            for workflow in workflows
        ]

    async def detail(self, actor: Actor, workflow_id: UUID) -> WorkflowDetail:
        await self._require(actor)
        workflow = await self._workflows.get(workflow_id)
        if workflow is None:
            raise NotFoundError("워크플로우를 찾을 수 없다.")
        return WorkflowDetail(
            workflow=workflow,
            states=await self._workflows.states_of(workflow_id),
            transitions=await self._workflows.transitions_of(workflow_id),
            types=await self._workflows.types_using(workflow_id),
        )

    async def _require(self, actor: Actor) -> None:
        # 정의를 바꾸면 프로젝트 전체 동작이 바뀐다. 읽기도 같은 권한으로
        # 묶는다 — 편집이 붙을 자리가 여기이고, 화면은 하나다.
        await self._perms.require(self._s, actor, perms.WORKFLOW_MANAGE, scope=Scope.global_())


class FieldDefinitionService:
    """커스텀 필드 정의. 값은 `issue_field_value`(JSONB) 에 있다 (D-18)."""

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._definitions = FieldDefinitionRepository(session)

    async def list_all(self, actor: Actor) -> list[FieldView]:
        await self._require(actor)
        counts = await self._definitions.value_counts()
        return [
            FieldView(definition=row, values=counts.get(row.key, 0))
            for row in await self._definitions.all_definitions()
        ]

    async def create(
        self,
        actor: Actor,
        *,
        key: str,
        name: str,
        kind: str,
        description: str | None = None,
        config: dict[str, Any] | None = None,
        is_required: bool = False,
        position: int = 0,
        project_id: UUID | None = None,
        issue_type_id: UUID | None = None,
    ) -> FieldDefinition:
        await self._require(actor)
        normalized = key.strip().lower()
        self._validate_key(normalized)
        self._validate_kind(kind)
        settings = dict(config or {})
        self._validate_config(kind, settings)

        if await self._definitions.get_by_key(normalized) is not None:
            # 키에 유일 제약이 걸려 있다. 먼저 묻지 않으면 두 번째 등록이
            # 500 으로 떨어진다.
            raise ConflictError(f"이미 쓰는 필드 키다: {normalized}", code="issues.field_key_taken")

        definition = self._definitions.add(
            FieldDefinition(
                key=normalized,
                name=name.strip(),
                kind=kind,
                description=description,
                config=settings,
                is_required=is_required,
                position=position,
                # NULL 이면 제한 없음이다. 프로젝트·유형을 좁히는 편집기는
                # 아직 화면에 없지만, API 로는 받아 둔다 — 모델이 이미
                # 지원하고, 임포터가 쓸 자리다.
                project_id=project_id,
                issue_type_id=issue_type_id,
            )
        )
        await self._s.flush()
        return definition

    async def update(
        self,
        actor: Actor,
        definition_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        config: dict[str, Any] | None = None,
        is_required: bool | None = None,
        position: int | None = None,
    ) -> FieldDefinition:
        """이름·설명·설정·필수 여부·순서를 고친다.

        **키와 종류는 못 고친다.** 키는 IQL 이 식별자로 쓰고 값의 주소이기도
        하다 — 바꾸면 이미 저장된 값이 통째로 고아가 된다. 종류는 값의 해석을
        정한다: `text` → `number` 로 바꾸면 JSONB 에 남은 문자열이 다음 편집에서
        검증을 통과하지 못하고, IQL 비교도 어긋난다. 새로 만드는 편이 정직하다.
        """
        await self._require(actor)
        definition = await self._require_definition(definition_id)

        if config is not None:
            settings = dict(config)
            self._validate_config(definition.kind, settings)
            # JSONB 는 통째로 갈아 끼워야 변경으로 잡힌다. 키만 바꾸면
            # SQLAlchemy 가 같은 객체로 보고 UPDATE 를 내지 않는다.
            definition.config = settings
        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                raise ValidationError("필드 이름이 필요하다.", code="issues.field_name_required")
            definition.name = cleaned
        if description is not None:
            definition.description = description or None
        if is_required is not None:
            definition.is_required = is_required
        if position is not None:
            definition.position = position

        await self._s.flush()
        return definition

    async def delete(self, actor: Actor, definition_id: UUID) -> int:
        """정의를 지운다. **값도 함께 지운다.** 지운 값의 개수를 돌려준다.

        남기면 되살아난다: `issue_field_value.field_key` 에는 FK 가 없어서
        정의만 지우면 값은 보이지 않는 채로 남고, 나중에 같은 키로 정의를 다시
        만들면 그 값이 다시 나타난다 — 종류가 달라졌으면 위젯이 엉뚱한 것을
        그린다. 그룹을 지울 때 역할 할당을 함께 지우는 것과 같은 판단이다.
        """
        await self._require(actor)
        definition = await self._require_definition(definition_id)
        return await self._definitions.delete_definition(definition.id, definition.key)

    async def _require_definition(self, definition_id: UUID) -> FieldDefinition:
        definition = await self._definitions.get(definition_id)
        if definition is None:
            raise NotFoundError("필드 정의를 찾을 수 없다.")
        return definition

    @staticmethod
    def _validate_key(key: str) -> None:
        if not KEY_PATTERN.match(key):
            raise ValidationError(
                "필드 키는 소문자로 시작하는 2~64자 소문자·숫자·밑줄이어야 한다.",
                code="issues.invalid_field_key",
                details={"key": key},
            )

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if kind not in FIELD_KINDS:
            raise ValidationError(
                f"지원하지 않는 필드 종류다: {kind}",
                code="issues.unknown_field_kind",
                details={"kinds": list(FIELD_KINDS)},
            )

    @staticmethod
    def _validate_config(kind: str, config: dict[str, Any]) -> None:
        """종류별 설정을 본다.

        선택지가 없는 `select` 는 **채울 수 없는 필드**다. 저장을 받아 주면
        화면은 빈 드롭다운을 그리고, 필수로 걸어 두었으면 그 프로젝트의 이슈를
        아무도 저장할 수 없게 된다.
        """
        if kind in NEEDS_OPTIONS:
            options = config.get("options")
            if not isinstance(options, list) or not options:
                raise ValidationError(
                    "선택지가 하나 이상 필요하다.",
                    code="issues.field_options_required",
                    details={"kind": kind},
                )
            if len(options) != len({str(o) for o in options}):
                raise ValidationError(
                    "선택지가 중복됐다.",
                    code="issues.field_options_duplicated",
                    details={"kind": kind},
                )

    async def _require(self, actor: Actor) -> None:
        await self._perms.require(self._s, actor, perms.FIELD_MANAGE, scope=Scope.global_())


__all__ = [
    "FieldDefinitionService",
    "FieldView",
    "WorkflowCatalogService",
    "WorkflowDetail",
    "WorkflowView",
]

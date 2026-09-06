"""IQL 검색과 저장 필터.

실행 경로는 하나뿐이다: 파싱 → 검증 → 컴파일 → ACL AND 결합 → 실행.
권한 필터를 건너뛰는 경로를 만들지 않는다 (query-language.md 3절).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from ieum.core.pagination import Page, PageRequest
from ieum.core.permissions import PermissionService
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.iql import errors as iql_errors
from ieum.modules.issues.iql.compiler import (
    MAX_RESULTS,
    compile_query,
    project_keys_in,
)
from ieum.modules.issues.iql.parser import parse
from ieum.modules.issues.iql.registry import (
    FIELDS,
    FUNCTIONS,
    FunctionContext,
    known_field_names,
)
from ieum.modules.issues.models import Issue, SavedFilter
from ieum.modules.org import contracts as org


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    #: 유효하지 않을 때만 채워진다. offset/length 로 에디터가 밑줄을 긋는다.
    error: dict[str, Any] | None = None
    #: 질의가 참조하는 필드. UI 가 컬럼을 자동 선택할 때 쓴다.
    fields: list[str] | None = None


class SearchService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    def _context(self, actor: Actor) -> FunctionContext:
        return FunctionContext(actor_id=actor.user_id, timezone=actor.timezone)

    async def _resolve_projects(self, query: Any) -> dict[str, UUID]:
        """질의 안의 프로젝트 키를 id 로 바꾼다.

        컴파일러가 org 테이블을 조인하지 않아도 되게 해서 모듈 경계를
        지킨다(절대규칙 1). 덤으로 issue 의 project 인덱스를 그대로 탄다.
        """
        resolved: dict[str, UUID] = {}
        for key in project_keys_in(query):
            found = await org.get_project_by_key(self._s, key)
            if found is not None:
                resolved[key] = found.id
        return resolved

    async def validate(self, actor: Actor, iql: str) -> ValidationResult:
        """문법·필드 검증. 실행하지 않는다.

        컴파일까지 돌려야 필드·연산자·함수 오류를 잡을 수 있다. 파싱만으로는
        `assigne = x` 같은 오타를 통과시킨다.
        """
        from ieum.core.permissions import Acl

        try:
            query = parse(iql)
            compile_query(
                query,
                # 검증에만 쓰는 ACL. 실제 실행은 search() 가 진짜 ACL 로 한다.
                acl=Acl(permission=perms.ISSUE_VIEW, is_global=True),
                ctx=self._context(actor),
                project_ids=await self._resolve_projects(query),
            )
        except iql_errors.IQLError as exc:
            return ValidationResult(
                valid=False,
                error={"code": exc.code, "message": exc.message, **exc.details},
            )
        return ValidationResult(valid=True, fields=sorted(_referenced_fields(query)))

    async def search(self, actor: Actor, iql: str, request: PageRequest) -> Page[Issue]:
        acl = await self._perms.acl_for(self._s, actor, perms.ISSUE_VIEW)
        query = parse(iql)
        compiled = compile_query(
            query,
            acl=acl,
            ctx=self._context(actor),
            project_ids=await self._resolve_projects(query),
        )

        stmt: Select[Any] = compiled.apply(select(Issue))
        payload = request.cursor_payload
        if payload:
            # 정렬 키가 자유롭기 때문에 커서는 id 기준으로만 잡는다.
            # 정렬 결과 안에서의 정확한 이어보기는 M5 의 과제다.
            stmt = stmt.where(Issue.id > UUID(payload["id"]))
        stmt = stmt.limit(min(request.fetch_limit, MAX_RESULTS))

        rows = list((await self._s.execute(stmt)).scalars().all())
        return Page.from_rows(rows, request, lambda i: {"id": str(i.id)})


class SavedFilterService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def create(
        self,
        actor: Actor,
        *,
        name: str,
        iql: str,
        description: str | None = None,
        is_shared: bool = False,
    ) -> SavedFilter:
        # 저장 전에 파싱한다. 깨진 필터를 저장하면 나중에 실행할 때 터진다.
        parse(iql)

        existing = (
            await self._s.execute(
                select(SavedFilter)
                .where(SavedFilter.owner_id == actor.user_id)
                .where(SavedFilter.name == name)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("같은 이름의 필터가 이미 있다.", code="issues.filter_name_taken")

        row = SavedFilter(
            owner_id=actor.user_id,
            name=name,
            description=description,
            iql=iql,
            is_shared=is_shared,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def get(self, actor: Actor, filter_id: UUID) -> SavedFilter:
        row = await self._s.get(SavedFilter, filter_id)
        if row is None:
            raise NotFoundError("필터를 찾을 수 없다.")
        if row.owner_id != actor.user_id and not row.is_shared:
            # 존재 자체를 숨긴다. 남의 필터 이름을 열거할 수 있으면 안 된다.
            raise NotFoundError("필터를 찾을 수 없다.")
        return row

    async def list_for(self, actor: Actor) -> list[SavedFilter]:
        stmt = (
            select(SavedFilter)
            .where((SavedFilter.owner_id == actor.user_id) | SavedFilter.is_shared.is_(True))
            .order_by(SavedFilter.name)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def update(
        self,
        actor: Actor,
        filter_id: UUID,
        *,
        name: str | None = None,
        iql: str | None = None,
        description: str | None = None,
        is_shared: bool | None = None,
    ) -> SavedFilter:
        """저장 필터 수정. 소유자만.

        지우고 다시 만들게 하면 공유 링크(필터 id)가 끊긴다.
        """
        row = await self.get(actor, filter_id)
        if row.owner_id != actor.user_id:
            raise PermissionDeniedError("남의 필터는 고칠 수 없다.")

        if iql is not None:
            # 저장 전에 파싱한다. 깨진 필터를 저장하면 실행할 때 터진다.
            parse(iql)
            row.iql = iql

        if name is not None and name != row.name:
            taken = (
                await self._s.execute(
                    select(SavedFilter)
                    .where(SavedFilter.owner_id == actor.user_id)
                    .where(SavedFilter.name == name)
                    .where(SavedFilter.id != filter_id)
                )
            ).scalar_one_or_none()
            if taken is not None:
                raise ConflictError(
                    "같은 이름의 필터가 이미 있다.", code="issues.filter_name_taken"
                )
            row.name = name

        if description is not None:
            row.description = description
        if is_shared is not None:
            row.is_shared = is_shared

        await self._s.flush()
        return row

    async def delete(self, actor: Actor, filter_id: UUID) -> None:
        row = await self.get(actor, filter_id)
        if row.owner_id != actor.user_id:
            raise PermissionDeniedError("남의 필터는 지울 수 없다.")
        await self._s.delete(row)

    async def run(self, actor: Actor, filter_id: UUID, request: PageRequest) -> Page[Issue]:
        """저장 필터 실행. **실행자 권한으로** 돈다.

        소유자 권한을 승계하면 공유가 곧 권한 상승이 된다
        (query-language.md 6절).
        """
        row = await self.get(actor, filter_id)
        return await SearchService(self._s, self._perms).search(actor, row.iql, request)


def _referenced_fields(query: Any) -> set[str]:
    """질의가 건드리는 필드 이름. 자동완성·컬럼 선택에 쓴다."""
    from ieum.modules.issues.iql.ast import (
        And,
        Comparison,
        EmptinessCheck,
        Not,
        Or,
    )

    found: set[str] = set()

    def walk(node: Any) -> None:
        match node:
            case And(operands) | Or(operands):
                for child in operands:
                    walk(child)
            case Not(operand):
                walk(operand)
            case Comparison() | EmptinessCheck():
                found.add(node.field.label())

    if query.where is not None:
        walk(query.where)
    for key in query.order_by:
        found.add(key.field.label())
    return found


def field_catalog() -> list[dict[str, Any]]:
    """UI 가 필터 칩과 자동완성을 그릴 때 쓰는 필드 목록."""
    seen: dict[str, dict[str, Any]] = {}
    for spec in FIELDS.values():
        if spec.name in seen:
            continue
        seen[spec.name] = {
            "name": spec.name,
            "type": spec.type.value,
            "description": spec.description,
            "operators": spec.allowed_operators(),
            "sortable": spec.sortable,
            "nullable": spec.nullable,
        }
    return [seen[name] for name in sorted(seen)]


def function_catalog() -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for spec in FUNCTIONS.values():
        seen[spec.name] = {
            "name": spec.name,
            "returns": spec.returns.value,
            "description": spec.description,
            "min_args": spec.arity[0],
            "max_args": spec.arity[1],
        }
    return [seen[name] for name in sorted(seen)]


__all__ = [
    "SavedFilterService",
    "SearchService",
    "ValidationResult",
    "field_catalog",
    "function_catalog",
    "known_field_names",
]

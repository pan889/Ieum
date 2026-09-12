"""IQL 검색과 저장 필터.

실행 경로는 하나뿐이다: 파싱 → 검증 → 컴파일 → ACL AND 결합 → 실행.
권한 필터를 건너뛰는 경로를 만들지 않는다 (query-language.md 3절).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.pagination import Page, PageRequest
from ieum.core.permissions import PermissionService
from ieum.modules.identity import contracts as identity
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
    FieldType,
    FunctionContext,
    known_field_names,
)
from ieum.modules.issues.iql.suggest import (
    Context,
    Suggestion,
    SuggestKind,
    analyze,
    quote_value,
    rank,
    static_candidates,
)
from ieum.modules.issues.models import (
    FieldDefinition,
    Issue,
    IssueLabel,
    IssueType,
    SavedFilter,
    WorkflowState,
)
from ieum.modules.org import contracts as org


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    #: 유효하지 않을 때만 채워진다. offset/length 로 에디터가 밑줄을 긋는다.
    error: dict[str, Any] | None = None
    #: 질의가 참조하는 필드. UI 가 컬럼을 자동 선택할 때 쓴다.
    fields: list[str] | None = None


@dataclass(frozen=True, slots=True)
class SuggestResult:
    """갈아 끼울 범위와 후보. 범위는 커서보다 뒤로 갈 수 있다 (닫는 따옴표)."""

    start: int
    length: int
    items: list[Suggestion]


#: 상태 분류는 워크플로우가 아니라 코드가 정한다 (data-model).
_STATUS_CATEGORIES = ("todo", "in_progress", "done")
#: 우선순위는 1~5 고정이다. 테이블이 없으므로 여기서 낸다.
_PRIORITIES = ("1", "2", "3", "4", "5")


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

    async def search(
        self,
        actor: Actor,
        iql: str,
        request: PageRequest,
        *,
        extra_where: ColumnElement[bool] | None = None,
    ) -> Page[Issue]:
        """IQL 실행. **언제나 실행자의 `issue.view` ACL 을 탄다.**

        `extra_where` 는 부르는 쪽이 얹는 조건이다. `desk` 의 큐가 "티켓인
        이슈만" 을 여기로 넘긴다 — `issue` 를 아는 것은 이 모듈이고 `desk` 는
        자기 테이블(`ticket_ext`)만 아는데, 둘을 한 질의로 묶어야 하기
        때문이다. 조건을 여기서 얹으면 페이지네이션이 맞는다: 밖에서 결과를
        걸러내면 50개를 읽어 3개가 남고, 다음 페이지가 어디인지 알 수 없다.
        `desk` 가 `issue` 를 import 하지 않고 `issues` 가 `desk` 를 import
        하지 않으므로 화살표는 그대로다(ADR-0010).
        """
        acl = await self._perms.acl_for(self._s, actor, perms.ISSUE_VIEW)
        query = parse(iql)
        compiled = compile_query(
            query,
            acl=acl,
            ctx=self._context(actor),
            project_ids=await self._resolve_projects(query),
        )

        stmt: Select[Any] = compiled.apply(select(Issue))
        if extra_where is not None:
            stmt = stmt.where(extra_where)
        payload = request.cursor_payload or {}
        offset = 0
        if compiled.order_by:
            # **정렬이 붙으면 자리 수로 센다.** id 커서(`WHERE id > X`)는 정렬이
            # id 순일 때만 맞다. `ORDER BY priority` 같은 것이 붙으면 1페이지
            # 마지막 행의 id 는 정렬 안에서 아무 자리도 아니어서, 다음 페이지가
            # 아직 안 보여 준 행 중 id 가 그보다 작은 것을 **통째로 버리고**
            # 이미 보여 준 행 중 id 가 큰 것을 다시 싣는다. 큐를 끝까지 넘겨도
            # 자기 티켓의 절반을 영영 못 보는 상태였다.
            #
            # 정렬 키를 전부 커서에 담는 방법(keyset)이 더 정확하지만, 키마다
            # 방향과 NULL 순서가 다르고 값을 실어 나르며 형을 되살려야 해서
            # 조용히 틀릴 자리가 많다. 자리 수는 정렬이 전순서(마지막이 id)라
            # 같은 스냅샷에서 결정적이다. 대신 **페이지를 넘기는 사이에 앞쪽
            # 행이 늘거나 줄면 한 건을 두 번 보거나 건너뛸 수 있다** — 행을
            # 절반씩 잃는 것과는 비교가 안 된다.
            offset = int(payload.get("offset", 0))
            stmt = stmt.offset(offset)
        elif "id" in payload:
            # 정렬이 id 하나뿐이면 id 커서가 정확하고 깊은 페이지에서도 빠르다.
            stmt = stmt.where(Issue.id > UUID(payload["id"]))
        stmt = stmt.limit(min(request.fetch_limit, MAX_RESULTS))

        rows = list((await self._s.execute(stmt)).scalars().all())
        if compiled.order_by:
            next_offset = offset + request.limit
            return Page.from_rows(rows, request, lambda _row: {"offset": next_offset})
        return Page.from_rows(rows, request, lambda i: {"id": str(i.id)})

    async def scan(
        self,
        actor: Actor,
        iql: str,
        *,
        limit: int,
        extra_where: ColumnElement[bool] | None = None,
    ) -> tuple[list[Issue], bool]:
        """한 번에 긁는다. `(행, 잘렸나)` 를 준다.

        `search` 와 달리 커서를 쓰지 않는다. 달력·간트처럼 **창 안의 것을
        전부** 원하는 화면은 순서도 이어보기도 필요 없다 — 페이지를 돌려 가며
        모으면 그 사이에 들어온 변경 때문에 한 건을 두 번 싣거나 놓친다.

        대신 상한이 있고, **넘었다는 사실을 숨기지 않는다** — 부르는 쪽이
        화면에 적을 수 있어야 한다. 잘린 달력을 다 그린 달력으로 읽으면 없는
        여유를 있다고 계획한다.
        """
        if limit < 1 or limit > MAX_RESULTS:
            raise ValidationError(
                f"limit 은 1 이상 {MAX_RESULTS} 이하여야 한다.",
                code="common.invalid_limit",
                details={"max": MAX_RESULTS},
            )
        acl = await self._perms.acl_for(self._s, actor, perms.ISSUE_VIEW)
        query = parse(iql)
        compiled = compile_query(
            query,
            acl=acl,
            ctx=self._context(actor),
            project_ids=await self._resolve_projects(query),
        )
        stmt: Select[Any] = compiled.apply(select(Issue))
        if extra_where is not None:
            stmt = stmt.where(extra_where)
        # 하나 더 읽어서 "더 있다" 를 안다. 세는 질의를 따로 내면 두 번 훑는다.
        rows = list((await self._s.execute(stmt.limit(limit + 1))).scalars().all())
        return rows[:limit], len(rows) > limit

    # ── 자동완성 ────────────────────────────────────────────────

    async def suggest(self, actor: Actor, iql: str, offset: int, limit: int = 20) -> SuggestResult:
        """커서 자리에서 올 수 있는 것을 제안한다.

        자리 판단은 문법이 하고(`iql.suggest`), 값은 여기서 채운다 — 프로젝트
        키도 사용자도 권한을 타기 때문이다. 목록은 검사하지 않고 필터링한다.
        """
        ctx = analyze(iql, offset)
        if ctx is None:
            return SuggestResult(start=min(max(offset, 0), len(iql)), length=0, items=[])

        candidates = static_candidates(ctx)
        candidates.extend(await self._value_candidates(actor, ctx, limit))
        candidates.extend(await self._custom_field_candidates(ctx))
        return SuggestResult(start=ctx.start, length=ctx.length, items=rank(ctx, candidates, limit))

    def _value(self, ctx: Context, value: str, detail: str = "") -> Suggestion:
        return Suggestion(
            label=value,
            insert=f"{quote_value(ctx, value)} ",
            kind=SuggestKind.VALUE,
            detail=detail,
        )

    async def _value_candidates(self, actor: Actor, ctx: Context, limit: int) -> list[Suggestion]:
        # `x IN` 다음은 괄호 자리다. 값을 끼우면 목록이 아니라 단일 값이 된다.
        if not ctx.wants_value or ctx.after_in:
            return []
        if ctx.custom_key is not None:
            return await self._custom_value_candidates(ctx)
        spec = ctx.spec
        if spec is None:
            return []

        match spec.name:
            case "statuscategory":
                return [self._value(ctx, name) for name in _STATUS_CATEGORIES]
            case "priority":
                # 숫자 리터럴이라 따옴표를 씌우지 않는다.
                return [
                    Suggestion(label=n, insert=f"{n} ", kind=SuggestKind.VALUE) for n in _PRIORITIES
                ]
            case "project":
                acl = await self._perms.acl_for(self._s, actor, perms.ISSUE_VIEW)
                rows = await org.search_projects(
                    self._s, acl=acl, query=ctx.prefix or None, limit=limit
                )
                return [self._value(ctx, row.key, row.name) for row in rows]
            case "status":
                return await self._distinct(ctx, WorkflowState.name, limit)
            case "type":
                acl = await self._perms.acl_for(self._s, actor, perms.ISSUE_VIEW)
                if acl.is_empty:
                    return []
                # 유형 이름은 그냥 이름이 아니다 ("Security Incident"). 프로젝트
                # 전용 유형은 그 프로젝트를 볼 수 있는 사람에게만 보인다.
                scope = (
                    None
                    if acl.is_global
                    else or_(
                        IssueType.project_id.is_(None),
                        IssueType.project_id.in_(acl.project_ids),
                    )
                )
                return await self._distinct(ctx, IssueType.name, limit, where=scope)
            case "labels":
                return await self._label_candidates(actor, ctx, limit)
            case _:
                pass

        if spec.type is FieldType.USER:
            users = await identity.search_users(self._s, query=ctx.prefix or None, limit=limit)
            # 값은 UUID 로 컴파일된다. 보이는 건 이름, 들어가는 건 ID 다.
            return [
                Suggestion(
                    label=user.display_name,
                    insert=f"{quote_value(ctx, str(user.id))} ",
                    kind=SuggestKind.VALUE,
                    detail=user.email,
                )
                for user in users
            ]
        return []

    async def _distinct(
        self,
        ctx: Context,
        column: Any,
        limit: int,
        *,
        where: ColumnElement[bool] | None = None,
    ) -> list[Suggestion]:
        """설정 테이블에서 이름을 뽑는다. 같은 이름이 여러 프로젝트에 있어도 한 번만."""
        stmt = select(column).distinct().order_by(column).limit(limit)
        if where is not None:
            stmt = stmt.where(where)
        if ctx.prefix:
            stmt = stmt.where(func.lower(column).like(f"%{ctx.prefix.lower()}%"))
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._value(ctx, str(row)) for row in rows]

    async def _label_candidates(self, actor: Actor, ctx: Context, limit: int) -> list[Suggestion]:
        acl = await self._perms.acl_for(self._s, actor, perms.ISSUE_VIEW)
        if acl.is_empty:
            return []
        stmt = (
            select(IssueLabel.label)
            .join(Issue, Issue.id == IssueLabel.issue_id)
            .distinct()
            .order_by(IssueLabel.label)
            .limit(limit)
        )
        if not acl.is_global:
            stmt = stmt.where(Issue.project_id.in_(acl.project_ids))
        if ctx.prefix:
            stmt = stmt.where(func.lower(IssueLabel.label).like(f"%{ctx.prefix.lower()}%"))
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._value(ctx, row) for row in rows]

    async def _custom_field_candidates(self, ctx: Context) -> list[Suggestion]:
        """`cf["key"]`. 필드 자리에서만 낸다."""
        if "CF" not in ctx.accepts:
            return []
        stmt = (
            select(FieldDefinition.key, FieldDefinition.name)
            .order_by(FieldDefinition.key)
            .limit(50)
        )
        rows = (await self._s.execute(stmt)).all()
        return [
            Suggestion(
                label=f'cf["{row.key}"]',
                insert=f'cf["{row.key}"] ',
                kind=SuggestKind.FIELD,
                detail=row.name,
                # 기본 필드가 먼저다. 커스텀 필드가 알파벳 순으로 앞에
                # 끼어들면 `created` 를 찾으러 스크롤해야 한다.
                weight=1,
            )
            for row in rows
        ]

    async def _custom_value_candidates(self, ctx: Context) -> list[Suggestion]:
        """select 커스텀 필드는 고를 값이 정해져 있다."""
        stmt = select(FieldDefinition.kind, FieldDefinition.config).where(
            FieldDefinition.key == ctx.custom_key
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None or row.kind not in {"select", "multiselect"}:
            return []
        options = row.config.get("options", [])
        return [self._value(ctx, option) for option in options if isinstance(option, str)]


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
    "SuggestResult",
    "ValidationResult",
    "field_catalog",
    "function_catalog",
    "known_field_names",
]

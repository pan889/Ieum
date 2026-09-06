"""칸반 보드.

컬럼은 IQL 로 정의한다 (D-44). 보드가 별도 필터 포맷을 만들면 워크플로우가
바뀔 때 두 곳을 고쳐야 하고, 저장 필터·매크로와 표현력이 갈라진다.

드래그 전이는 **기존 워크플로우 검증을 그대로 탄다**. 보드에서만 통하는
전이 경로를 만들면 조건과 후처리가 우회된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ConflictError, NotFoundError, ValidationError
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.iql.parser import parse
from ieum.modules.issues.models import Board, Issue
from ieum.modules.issues.search import SearchService
from ieum.modules.issues.service import IssueService, IssueView
from ieum.modules.org import contracts as org

MAX_COLUMNS = 12
#: 컬럼 하나에 이만큼만 싣는다. 보드는 훑어보는 화면이지 전체 목록이 아니다.
COLUMN_PAGE_SIZE = 50

#: IQL 에 프로젝트 키를 그대로 끼워 넣기 전에 확인한다. org 의 검증을
#: 신뢰해서 생략하면, 키 형식이 언젠가 느슨해질 때 조용히 질의 주입이 된다.
_SAFE_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")


@dataclass(frozen=True, slots=True)
class BoardColumn:
    name: str
    iql: str
    wip_limit: int | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any], index: int) -> BoardColumn:
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValidationError(
                f"{index + 1}번째 컬럼에 이름이 없다.",
                code="issues.invalid_board_column",
                details={"index": index},
            )
        iql = str(raw.get("iql", "")).strip()
        limit = raw.get("wip_limit")
        # bool 은 int 의 서브클래스다. True 를 WIP 1 로 받아들이면 안 된다.
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
            raise ValidationError(
                f"'{name}' 의 WIP 제한은 정수여야 한다.",
                code="issues.invalid_board_column",
                details={"column": name},
            )
        if limit is not None and not 1 <= limit <= COLUMN_PAGE_SIZE:
            raise ValidationError(
                f"'{name}' 의 WIP 제한은 1 이상 {COLUMN_PAGE_SIZE} 이하여야 한다.",
                code="issues.invalid_board_column",
                details={"column": name, "max": COLUMN_PAGE_SIZE},
            )
        return cls(name=name, iql=iql, wip_limit=limit)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "iql": self.iql, "wip_limit": self.wip_limit}


@dataclass(slots=True)
class ColumnResult:
    name: str
    iql: str
    wip_limit: int | None
    issues: list[IssueView] = field(default_factory=list)
    #: 이 컬럼에 실제로 실린 개수. `truncated` 면 이보다 많다.
    loaded: int = 0
    #: 페이지 크기에서 잘렸는지. UI 는 "50+" 로 표시한다.
    truncated: bool = False
    over_wip: bool = False


class BoardService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def create(
        self,
        actor: Actor,
        *,
        project_id: UUID,
        name: str,
        columns: list[dict[str, Any]],
        swimlane_by: str | None = None,
        base_iql: str | None = None,
    ) -> Board:
        await self._perms.require(
            self._s, actor, perms.BOARD_MANAGE, scope=Scope.project(project_id)
        )
        parsed = self._validate_columns(columns)
        if base_iql:
            parse(base_iql)
        await self._require_unique_name(project_id, name)

        board = Board(
            project_id=project_id,
            name=name,
            columns=[c.as_dict() for c in parsed],
            swimlane_by=swimlane_by,
            base_iql=base_iql,
        )
        self._s.add(board)
        await self._s.flush()
        return board

    async def update(
        self,
        actor: Actor,
        board_id: UUID,
        *,
        name: str | None = None,
        columns: list[dict[str, Any]] | None = None,
        swimlane_by: str | None = None,
        base_iql: str | None = None,
        clear_swimlane: bool = False,
        clear_base_iql: bool = False,
    ) -> Board:
        board = await self._get_row(board_id)
        await self._perms.require(
            self._s, actor, perms.BOARD_MANAGE, scope=Scope.project(board.project_id)
        )
        if name is not None and name != board.name:
            await self._require_unique_name(board.project_id, name, exclude=board.id)
            board.name = name
        if columns is not None:
            board.columns = [c.as_dict() for c in self._validate_columns(columns)]
        if clear_base_iql:
            board.base_iql = None
        elif base_iql is not None:
            parse(base_iql)
            board.base_iql = base_iql
        if clear_swimlane:
            board.swimlane_by = None
        elif swimlane_by is not None:
            board.swimlane_by = swimlane_by
        await self._s.flush()
        return board

    async def delete(self, actor: Actor, board_id: UUID) -> None:
        board = await self._get_row(board_id)
        await self._perms.require(
            self._s, actor, perms.BOARD_MANAGE, scope=Scope.project(board.project_id)
        )
        await self._s.delete(board)

    async def get(self, actor: Actor, board_id: UUID) -> Board:
        board = await self._get_row(board_id)
        await self._perms.require(
            self._s, actor, perms.ISSUE_VIEW, scope=Scope.project(board.project_id)
        )
        return board

    async def list_for_project(self, actor: Actor, project_id: UUID) -> list[Board]:
        await self._perms.require(self._s, actor, perms.ISSUE_VIEW, scope=Scope.project(project_id))
        stmt = (
            select(Board).where(Board.project_id == project_id).order_by(Board.position, Board.name)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def load(self, actor: Actor, board_id: UUID) -> list[ColumnResult]:
        """컬럼마다 IQL 을 돌려 이슈를 담는다.

        컬럼 수만큼 질의가 나간다. 컬럼은 12개로 제한되고 각 질의가 인덱스를
        타므로 감당할 수 있다 — 한 번에 다 긁어와 메모리에서 나누면 페이지네이션과
        WIP 계산이 어긋난다.
        """
        board = await self.get(actor, board_id)
        scope_iql = await self._project_scope(board.project_id)
        search = SearchService(self._s, self._perms)
        issues = IssueService(self._s, self._perms)

        results: list[ColumnResult] = []
        for index, raw in enumerate(board.columns):
            column = BoardColumn.parse(raw, index)
            page = await search.search(
                actor,
                self._combine(scope_iql, board, column),
                PageRequest(limit=COLUMN_PAGE_SIZE),
            )
            views = [await issues.to_view(issue) for issue in page.items]
            truncated = page.next_cursor is not None
            results.append(
                ColumnResult(
                    name=column.name,
                    iql=column.iql,
                    wip_limit=column.wip_limit,
                    issues=views,
                    loaded=len(views),
                    truncated=truncated,
                    over_wip=column.wip_limit is not None
                    and (len(views) > column.wip_limit or truncated),
                )
            )
        return results

    async def move(
        self,
        actor: Actor,
        board_id: UUID,
        issue_id: UUID,
        transition_id: UUID,
        *,
        expected_version: int | None = None,
    ) -> IssueView:
        """드래그 전이.

        전용 경로를 만들지 않고 IssueService.transition 을 그대로 부른다.
        보드에서만 통하는 전이가 있으면 조건과 후처리가 우회된다.
        """
        board = await self.get(actor, board_id)
        issue = await self._s.get(Issue, issue_id)
        if issue is None:
            raise NotFoundError("이슈를 찾을 수 없다.")
        if issue.project_id != board.project_id:
            raise ValidationError("이 보드의 이슈가 아니다.", code="issues.issue_not_on_board")
        return await IssueService(self._s, self._perms).transition(
            actor, issue_id, transition_id, expected_version=expected_version
        )

    # ── 내부 ────────────────────────────────────────────────────────

    async def _get_row(self, board_id: UUID) -> Board:
        board = await self._s.get(Board, board_id)
        if board is None:
            raise NotFoundError("보드를 찾을 수 없다.")
        return board

    async def _require_unique_name(
        self, project_id: UUID, name: str, *, exclude: UUID | None = None
    ) -> None:
        stmt = select(Board.id).where(Board.project_id == project_id).where(Board.name == name)
        if exclude is not None:
            stmt = stmt.where(Board.id != exclude)
        if (await self._s.execute(stmt)).first() is not None:
            raise ConflictError("같은 이름의 보드가 이미 있다.", code="issues.board_name_taken")

    async def _project_scope(self, project_id: UUID) -> str:
        """보드는 언제나 자기 프로젝트 안에서만 본다.

        컬럼 IQL 이 비어 있을 때 프로젝트 조건까지 없으면 보이는 이슈를
        전부 긁어온다 — 권한은 막아도 남의 프로젝트 이슈가 보드에 올라온다.
        """
        project = await org.get_project(self._s, project_id)
        if project is None:
            raise NotFoundError("프로젝트를 찾을 수 없다.")
        if not _SAFE_PROJECT_KEY.match(project.key):
            raise ValidationError(
                "프로젝트 키 형식이 보드에서 지원되지 않는다.",
                code="issues.board_unsupported_project_key",
            )
        # 아카이브된 이슈는 보드에 올리지 않는다. 컬럼 IQL 이 뭐라 하든
        # 보드는 "지금 흐르고 있는 일" 을 보는 화면이다.
        return f'project = "{project.key}" AND archived = false'

    def _combine(self, scope_iql: str, board: Board, column: BoardColumn) -> str:
        """프로젝트 범위 + 보드 범위 + 컬럼 조건을 AND 로 묶는다."""
        parts = [scope_iql]
        if board.base_iql:
            parts.append(f"({board.base_iql})")
        if column.iql:
            parts.append(f"({column.iql})")
        return " AND ".join(parts)

    def _validate_columns(self, columns: list[dict[str, Any]]) -> list[BoardColumn]:
        if not columns:
            raise ValidationError("컬럼을 하나 이상 정의해야 한다.", code="issues.board_no_columns")
        if len(columns) > MAX_COLUMNS:
            raise ValidationError(
                f"컬럼은 {MAX_COLUMNS}개까지다.",
                code="issues.board_too_many_columns",
                details={"max": MAX_COLUMNS},
            )
        parsed = [BoardColumn.parse(raw, i) for i, raw in enumerate(columns)]
        # 저장 시점에 IQL 을 검증한다. 깨진 컬럼은 보드를 열 때 터진다.
        for column in parsed:
            if column.iql:
                parse(column.iql)
        names = [c.name for c in parsed]
        if len(set(names)) != len(names):
            raise ValidationError("컬럼 이름이 겹친다.", code="issues.board_duplicate_column")
        return parsed


#: 기본 보드 컬럼. 프로젝트를 만들 때 이 모양으로 하나 만들어 준다.
DEFAULT_COLUMNS: list[dict[str, Any]] = [
    {"name": "To Do", "iql": "statusCategory = todo", "wip_limit": None},
    {"name": "In Progress", "iql": "statusCategory = in_progress", "wip_limit": 5},
    {"name": "Done", "iql": "statusCategory = done", "wip_limit": None},
]

__all__ = [
    "COLUMN_PAGE_SIZE",
    "DEFAULT_COLUMNS",
    "MAX_COLUMNS",
    "BoardColumn",
    "BoardService",
    "ColumnResult",
]

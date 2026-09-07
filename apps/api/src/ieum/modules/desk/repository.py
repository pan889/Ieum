"""desk 데이터 접근. 쿼리만 한다."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.pagination import Page as PageResult
from ieum.core.pagination import PageRequest
from ieum.modules.desk.models import (
    CannedResponse,
    CustomerMembership,
    CustomerOrganization,
    Portal,
    Queue,
    RequestType,
    TicketExt,
)


def normalize_slug(raw: str) -> str:
    """포털 슬러그. 고객이 URL 에서 읽는 값이므로 소문자로 굳힌다."""
    return raw.strip().lower()


def normalize_domain(raw: str) -> str:
    """이메일 도메인. `@` 를 붙여 적는 사람이 있으므로 떼어 낸다."""
    return raw.strip().lower().lstrip("@")


class PortalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, portal_id: UUID) -> Portal | None:
        return await self._s.get(Portal, portal_id)

    async def get_by_slug(self, slug: str) -> Portal | None:
        stmt = select(Portal).where(Portal.slug == normalize_slug(slug))
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def slug_exists(self, slug: str, *, exclude_id: UUID | None = None) -> bool:
        stmt = select(func.count()).select_from(Portal).where(Portal.slug == normalize_slug(slug))
        if exclude_id is not None:
            stmt = stmt.where(Portal.id != exclude_id)
        return bool((await self._s.execute(stmt)).scalar_one())

    def add(self, portal: Portal) -> Portal:
        self._s.add(portal)
        return portal

    async def list_for_projects(
        self, project_ids: Sequence[UUID], *, include_archived: bool = False
    ) -> list[Portal]:
        if not project_ids:
            return []
        stmt: Select[tuple[Portal]] = select(Portal).where(Portal.project_id.in_(project_ids))
        if not include_archived:
            stmt = stmt.where(Portal.archived_at.is_(None))
        stmt = stmt.order_by(Portal.name)
        return list((await self._s.execute(stmt)).scalars().all())

    async def list_for_projects_all(self) -> list[Portal]:
        """접히지 않은 포털 전부. 로그인한 고객에게 창구를 고르게 할 때 쓴다."""
        stmt = select(Portal).where(Portal.archived_at.is_(None)).order_by(Portal.name, Portal.id)
        return list((await self._s.execute(stmt)).scalars().all())


class RequestTypeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, request_type_id: UUID) -> RequestType | None:
        return await self._s.get(RequestType, request_type_id)

    async def list_for_portal(
        self, portal_id: UUID, *, enabled_only: bool = False
    ) -> list[RequestType]:
        stmt: Select[tuple[RequestType]] = select(RequestType).where(
            RequestType.portal_id == portal_id, RequestType.archived_at.is_(None)
        )
        if enabled_only:
            stmt = stmt.where(RequestType.is_enabled.is_(True))
        stmt = stmt.order_by(RequestType.position, RequestType.name)
        return list((await self._s.execute(stmt)).scalars().all())

    async def name_exists(
        self, portal_id: UUID, name: str, *, exclude_id: UUID | None = None
    ) -> bool:
        stmt = (
            select(func.count())
            .select_from(RequestType)
            .where(RequestType.portal_id == portal_id, RequestType.name == name)
        )
        if exclude_id is not None:
            stmt = stmt.where(RequestType.id != exclude_id)
        return bool((await self._s.execute(stmt)).scalar_one())

    async def ticket_count(self, request_type_id: UUID) -> int:
        """이 유형으로 들어온 티켓 수. 지우기 전에 사람에게 보여 준다."""
        stmt = (
            select(func.count())
            .select_from(TicketExt)
            .where(TicketExt.request_type_id == request_type_id)
        )
        return int((await self._s.execute(stmt)).scalar_one())

    def add(self, request_type: RequestType) -> RequestType:
        self._s.add(request_type)
        return request_type


class CustomerOrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, organization_id: UUID) -> CustomerOrganization | None:
        return await self._s.get(CustomerOrganization, organization_id)

    async def name_exists(self, name: str, *, exclude_id: UUID | None = None) -> bool:
        stmt = (
            select(func.count())
            .select_from(CustomerOrganization)
            .where(CustomerOrganization.name == name)
        )
        if exclude_id is not None:
            stmt = stmt.where(CustomerOrganization.id != exclude_id)
        return bool((await self._s.execute(stmt)).scalar_one())

    async def find_by_domain(self, domain: str) -> CustomerOrganization | None:
        """이 도메인을 가진 조직. 가입 시 기본 소속을 정하는 데만 쓴다.

        `domains` 는 배열이므로 `ANY` 로 본다. 여러 조직이 같은 도메인을
        주장하면 이름 순 첫 번째다 — 그런 설정 자체가 잘못이지만, 여기서
        예외를 던지면 가입이 막힌다.
        """
        needle = normalize_domain(domain)
        if not needle:
            return None
        stmt = (
            select(CustomerOrganization)
            # `contains` 는 `domains @> ARRAY[needle]` 로 내려가 GIN 인덱스를
            # 탄다. `any(...)` 은 컬럼 표현식을 요구해 타입이 맞지 않는다.
            .where(CustomerOrganization.domains.contains([needle]))
            .where(CustomerOrganization.archived_at.is_(None))
            .order_by(CustomerOrganization.name)
            .limit(1)
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, organization: CustomerOrganization) -> CustomerOrganization:
        self._s.add(organization)
        return organization

    async def list_page(
        self, request: PageRequest, *, query: str | None = None, include_archived: bool = False
    ) -> PageResult[CustomerOrganization]:
        stmt: Select[tuple[CustomerOrganization]] = select(CustomerOrganization)
        if not include_archived:
            stmt = stmt.where(CustomerOrganization.archived_at.is_(None))
        if query:
            stmt = stmt.where(CustomerOrganization.name.ilike(f"%{query.strip()}%"))
        payload = request.cursor_payload
        if payload:
            # 이름 정렬이므로 커서도 (이름, id) 다. 이름은 유일하지만 동명
            # 조직이 생길 여지를 남기지 않으려면 id 가 함께 있어야 한다.
            stmt = stmt.where(
                tuple_(CustomerOrganization.name, CustomerOrganization.id)
                > (payload["name"], UUID(payload["id"]))
            )
        stmt = stmt.order_by(CustomerOrganization.name, CustomerOrganization.id)
        rows = list((await self._s.execute(stmt.limit(request.fetch_limit))).scalars().all())
        return PageResult.from_rows(rows, request, lambda o: {"name": o.name, "id": str(o.id)})

    async def member_counts(self, organization_ids: Sequence[UUID]) -> dict[UUID, int]:
        """조직별 소속 고객 수. 목록이 행마다 세지 않게 한다."""
        if not organization_ids:
            return {}
        stmt = (
            select(CustomerMembership.organization_id, func.count())
            .where(CustomerMembership.organization_id.in_(organization_ids))
            .group_by(CustomerMembership.organization_id)
        )
        rows = (await self._s.execute(stmt)).all()
        return {row[0]: int(row[1]) for row in rows}


class CustomerMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: UUID) -> CustomerMembership | None:
        return await self._s.get(CustomerMembership, user_id)

    async def organization_of(self, user_id: UUID) -> UUID | None:
        row = await self.get(user_id)
        return row.organization_id if row else None

    async def user_ids_in(self, organization_id: UUID) -> list[UUID]:
        stmt = select(CustomerMembership.user_id).where(
            CustomerMembership.organization_id == organization_id
        )
        return [row[0] for row in (await self._s.execute(stmt)).all()]

    async def set(self, *, user_id: UUID, organization_id: UUID) -> None:
        """한 사람은 한 조직. 있으면 갈아 끼운다."""
        existing = await self.get(user_id)
        if existing is None:
            self._s.add(CustomerMembership(user_id=user_id, organization_id=organization_id))
            return
        existing.organization_id = organization_id

    async def clear(self, user_id: UUID) -> bool:
        existing = await self.get(user_id)
        if existing is None:
            return False
        await self._s.delete(existing)
        return True


class TicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, issue_id: UUID) -> TicketExt | None:
        return await self._s.get(TicketExt, issue_id)

    def add(self, ticket: TicketExt) -> TicketExt:
        self._s.add(ticket)
        return ticket

    async def visible_to_customer(
        self,
        request: PageRequest,
        *,
        user_id: UUID,
        organization_id: UUID | None,
        portal_id: UUID | None = None,
    ) -> PageResult[TicketExt]:
        """이 고객이 볼 수 있는 티켓. 자기 것 + 자기 조직 것 (auth.md 5절).

        조직이 없으면 자기 것만이다. `organization_id IS NULL` 을 조건에
        넣지 않는 것이 중요하다 — 넣으면 조직 없는 고객이 **조직 미지정
        티켓 전부**를 보게 된다.
        """
        stmt: Select[tuple[TicketExt]] = select(TicketExt)
        if organization_id is None:
            stmt = stmt.where(TicketExt.reporter_customer_id == user_id)
        else:
            stmt = stmt.where(
                (TicketExt.reporter_customer_id == user_id)
                | (TicketExt.organization_id == organization_id)
            )
        if portal_id is not None:
            # 포털이 지정되면 그 포털의 요청 유형으로 들어온 것만.
            stmt = stmt.where(
                TicketExt.request_type_id.in_(
                    select(RequestType.id).where(RequestType.portal_id == portal_id)
                )
            )
        # `issue_id` 는 UUIDv7 이므로 id 역순이 곧 최신순이다.
        payload = request.cursor_payload
        if payload:
            stmt = stmt.where(TicketExt.issue_id < UUID(payload["id"]))
        stmt = stmt.order_by(TicketExt.issue_id.desc()).limit(request.fetch_limit)
        rows = list((await self._s.execute(stmt)).scalars().all())
        return PageResult.from_rows(rows, request, lambda t: {"id": str(t.issue_id)})


class QueueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, queue_id: UUID) -> Queue | None:
        return await self._s.get(Queue, queue_id)

    def add(self, queue: Queue) -> Queue:
        self._s.add(queue)
        return queue

    async def list_for_project(self, project_id: UUID) -> list[Queue]:
        """사이드바 순서대로. 보관된 것은 뺀다.

        `position` 이 같으면 이름으로 가른다 — 안 그러면 새로고침마다 순서가
        바뀌고, 사람은 큐가 사라졌다고 생각한다.
        """
        stmt: Select[tuple[Queue]] = (
            select(Queue)
            .where(Queue.project_id == project_id, Queue.archived_at.is_(None))
            .order_by(Queue.position, Queue.name)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def name_taken(self, project_id: UUID, name: str, *, exclude: UUID | None = None) -> bool:
        """이름 중복. 보관된 것까지 본다 — 유니크 제약이 `archived_at` 을
        모르기 때문이다. 여기서 안 보면 저장이 500 으로 터진다."""
        stmt = select(func.count()).where(Queue.project_id == project_id, Queue.name == name)
        if exclude is not None:
            stmt = stmt.where(Queue.id != exclude)
        return bool((await self._s.execute(stmt)).scalar_one())


class CannedResponseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, response_id: UUID) -> CannedResponse | None:
        return await self._s.get(CannedResponse, response_id)

    def add(self, response: CannedResponse) -> CannedResponse:
        self._s.add(response)
        return response

    async def list_for_project(self, project_id: UUID) -> list[CannedResponse]:
        stmt: Select[tuple[CannedResponse]] = (
            select(CannedResponse)
            .where(CannedResponse.project_id == project_id, CannedResponse.archived_at.is_(None))
            .order_by(CannedResponse.name)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def name_taken(self, project_id: UUID, name: str, *, exclude: UUID | None = None) -> bool:
        stmt = select(func.count()).where(
            CannedResponse.project_id == project_id, CannedResponse.name == name
        )
        if exclude is not None:
            stmt = stmt.where(CannedResponse.id != exclude)
        return bool((await self._s.execute(stmt)).scalar_one())

    async def shortcut_taken(
        self, project_id: UUID, shortcut: str, *, exclude: UUID | None = None
    ) -> bool:
        stmt = select(func.count()).where(
            CannedResponse.project_id == project_id, CannedResponse.shortcut == shortcut
        )
        if exclude is not None:
            stmt = stmt.where(CannedResponse.id != exclude)
        return bool((await self._s.execute(stmt)).scalar_one())

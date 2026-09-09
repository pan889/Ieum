"""자산·구성 항목과 티켓의 연결 (C15).

## 무엇을 담고 무엇을 안 담는가

담는 것: 종류·이름·자산번호·상태·담당자·소속 조직·위치·메모. 그리고 **티켓과의
연결**. 로드맵이 약속한 것은 그 연결이고, 이 파일의 절반이 그것이다.

안 담는 것: 자유 속성. JSONB 하나를 두고 아무 키나 넣게 하면 두 사람이 같은
것을 다르게 적고(`serial` / `sn` / `시리얼`), 화면은 그것을 그릴 수 없다.
커스텀 필드 기계를 여기까지 늘리는 것도 하지 않았다 — 그건 이슈에 붙어 있고,
자산으로 넓히면 정의가 두 벌이 된다. 실제 요청이 올 때 정의를 갖춘 채로 더한다.

## 목록은 언제나 검색이다

자산은 수천 개가 된다. 이 저장소는 "목록을 통째로 받아 드롭다운에 넣는" 실수를
세 번 했고(ux-principles 4절), 그때마다 **마지막에 만든 것이 고를 수 없는**
상태가 됐다. 그래서 여기서 내주는 것은 커서 페이지뿐이고, 상한 없는 전체
목록을 주는 함수는 두지 않는다.

## 자산은 지워지지 않는다

지난 티켓이 그 자산을 가리키고 있고, 그 이력이 이 기능의 값이다. 장비가 나가면
`retired` 다. 링크가 하나도 없는 자산만 지울 수 있다 — 잘못 만든 행을 치우는
길은 있어야 하지만, 이력을 지우는 길은 없어야 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, func, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ConflictError, NotFoundError, ValidationError
from ieum.core.logging import get_logger
from ieum.core.pagination import Page as PageResult
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.modules.desk import permissions as perms
from ieum.modules.desk.models import ASSET_STATUSES, Asset, AssetLink, AssetType
from ieum.modules.identity import contracts as identity
from ieum.modules.issues import contracts as issues

log = get_logger(__name__)

MAX_NAME = 200
MAX_TAG = 64
MAX_LOCATION = 200
MAX_NOTE = 4000

#: 자산번호에 받아들일 글자. 사람이 손으로 치고 바코드에서 읽는 값이라
#: 좁게 잡는다 — 공백과 한글이 섞이면 같은 번호가 두 자산이 된다.
_TAG = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{0,63}$")


def clean_tag(raw: str | None) -> str | None:
    """자산번호를 저장할 모양으로. 비면 `None`.

    **대문자로 맞춘다.** `A-1024` 와 `a-1024` 가 두 자산이 되면 재고를 못
    믿는다 (프로젝트 키와 같은 판단).
    """
    value = (raw or "").strip().upper()
    if not value:
        return None
    if not _TAG.match(value):
        raise ValidationError(
            "자산번호는 영문 대문자·숫자와 `.  _  /  -` 만 쓴다.",
            code="desk.invalid_asset_tag",
            details={"value": value[:MAX_TAG]},
        )
    return value


def clean_status(raw: str | None) -> str:
    if raw is None:
        return "in_use"
    if raw not in ASSET_STATUSES:
        raise ValidationError(
            "그런 자산 상태는 없다.",
            code="desk.invalid_asset_status",
            details={"allowed": ", ".join(ASSET_STATUSES)},
        )
    return raw


@dataclass(frozen=True, slots=True)
class NewAsset:
    type_id: UUID
    name: str
    tag: str | None = None
    status: str = "in_use"
    owner_id: UUID | None = None
    organization_id: UUID | None = None
    location: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class AssetView:
    """화면이 그릴 자산 한 줄. 이름은 함께 담는다 — 목록이 행마다 조회하지
    않게."""

    asset: Asset
    type_name: str
    owner_name: str | None
    organization_name: str | None
    #: 이 자산에 걸린 티켓 수. **0 이 아닌 것이 눈에 띄어야** 한다: 자꾸
    #: 고장나는 장비를 찾는 것이 이 기능을 쓰는 이유 중 하나다.
    ticket_count: int


@dataclass(frozen=True, slots=True)
class LinkedAsset:
    """티켓에 붙은 자산 하나."""

    asset: Asset
    type_name: str
    organization_name: str | None


@dataclass(frozen=True, slots=True)
class OrgChoice:
    """자산에 붙일 고객 조직 후보. 이름과 id 뿐이다 — 고르는 데 그 이상은
    필요 없고, 그 이상을 내주면 `desk.customer.manage` 없이 조직 관리 화면의
    내용을 읽는 길이 된다."""

    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class OrgChoices:
    """후보와 **잘렸는지**. 잘린 것을 조용히 두면 사람은 "이게 전부" 로
    읽는다 — 이 저장소가 세 번 겪은 실수고, ux-principles 는 "안전장치는
    조용하면 안 된다" 로 적어 두었다."""

    items: list[OrgChoice]
    has_more: bool


@dataclass(frozen=True, slots=True)
class AssetTicket:
    """자산에 걸린 티켓 하나. 이력 화면이 쓴다."""

    issue_id: UUID
    key: str
    summary: str
    state_name: str
    state_category: str


class AssetTypeService:
    """자산 종류. 관리자가 짓는 이름이다."""

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def list_all(self, actor: Actor, *, include_archived: bool = False) -> list[AssetType]:
        await self._perms.require(self._s, actor, perms.ASSET_VIEW, scope=Scope.global_())
        stmt = select(AssetType).order_by(AssetType.position, AssetType.name)
        if not include_archived:
            stmt = stmt.where(AssetType.archived_at.is_(None))
        return list((await self._s.execute(stmt)).scalars().all())

    async def create(
        self, actor: Actor, *, name: str, icon: str | None = None, position: int = 0
    ) -> AssetType:
        await self._perms.require(self._s, actor, perms.ASSET_MANAGE, scope=Scope.global_())
        clean = name.strip()
        if not clean:
            raise ValidationError("종류 이름이 필요하다.", code="desk.asset_type_name_required")
        row = AssetType(name=clean[:120], icon=(icon or "").strip() or None, position=position)
        self._s.add(row)
        try:
            await self._s.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "같은 이름의 종류가 있다.", code="desk.asset_type_name_taken"
            ) from exc
        return row

    async def set_archived(self, actor: Actor, type_id: UUID, *, archived: bool) -> AssetType:
        """종류를 접는다. **지우지 않는다** — 그 종류의 자산이 남아 있고,
        지우면 그 자산들이 가리킬 곳을 잃는다(FK 가 RESTRICT 다)."""
        await self._perms.require(self._s, actor, perms.ASSET_MANAGE, scope=Scope.global_())
        row = await self._s.get(AssetType, type_id)
        if row is None:
            raise NotFoundError("자산 종류를 찾을 수 없다.")
        from ieum.core.time import utcnow

        row.archived_at = utcnow() if archived else None
        await self._s.flush()
        return row


class AssetService:
    """자산을 등록하고 찾고 티켓에 잇는다."""

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    # ── 목록 ────────────────────────────────────────────────────

    async def search(
        self,
        actor: Actor,
        request: PageRequest,
        *,
        query: str | None = None,
        type_id: UUID | None = None,
        status: str | None = None,
        organization_id: UUID | None = None,
        include_retired: bool = False,
    ) -> PageResult[AssetView]:
        """이름·자산번호·위치로 찾는다. **커서 페이지다** (파일 머리 참조).

        `include_retired` 가 기본으로 꺼져 있는 이유: 티켓에 이을 자산을 고를
        때 나간 장비가 후보에 섞이면 잘못 고른다. 재고 화면은 켜서 본다.
        """
        await self._perms.require(self._s, actor, perms.ASSET_VIEW, scope=Scope.global_())
        stmt: Select[tuple[Asset]] = select(Asset)
        stmt = self._narrow(
            stmt,
            query=query,
            type_id=type_id,
            status=status,
            organization_id=organization_id,
            include_retired=include_retired,
        )
        # 이름 순이다. id 순(= 만든 순)으로 두면 재고 목록을 눈으로 훑을 수
        # 없다. 이름이 겹칠 수 있으므로 id 를 함께 실어 커서를 안정시킨다.
        payload = request.cursor_payload
        if payload:
            stmt = stmt.where(tuple_(Asset.name, Asset.id) > (payload["name"], UUID(payload["id"])))
        stmt = stmt.order_by(Asset.name, Asset.id).limit(request.fetch_limit)
        rows = list((await self._s.execute(stmt)).scalars().all())
        page = PageResult.from_rows(
            rows, request, lambda row: {"name": row.name, "id": str(row.id)}
        )
        return PageResult(
            items=await self._views(page.items),
            next_cursor=page.next_cursor,
        )

    async def organizations(
        self, actor: Actor, *, query: str | None = None, limit: int = 10
    ) -> OrgChoices:
        """자산에 붙일 고객 조직 후보를 찾는다. **검색이다.**

        따로 있는 이유는 권한이다. 조직 관리 목록(`desk.customer.manage`)은
        **step-up 을 요구한다** — 소속을 바꾸면 그 고객이 보는 티켓의 범위가
        바뀌니까. 자산 등록은 일부러 step-up 이 아니다(`ASSET_MANAGE` 주석):
        장비를 하루에 스무 개 넣는 사람에게 2FA 를 스무 번 물으면 그 사람은
        스프레드시트로 돌아간다. 그 목록을 자산 화면에서 그대로 쓰면 화면을
        여는 것만으로 403 이 나고, 조직 칸은 죽은 칸이 된다.

        그래서 여기서 내주는 것은 **이름과 id 뿐**이다. 관리 목록이 함께
        주는 소속 인원수·도메인·메모는 넘기지 않는다.

        드롭다운이 아니라 검색인 이유: 고객 조직은 수백 개가 된다. 앞의 몇
        개만 담은 드롭다운은 "이게 전부" 로 읽히고, 나머지는 고를 길이 없다.
        그래서 **잘렸는지를 함께 돌려준다** — 화면이 그것을 말해야 한다.
        """
        await self._perms.require(self._s, actor, perms.ASSET_MANAGE, scope=Scope.global_())
        from ieum.modules.desk.models import CustomerOrganization

        stmt = select(CustomerOrganization.id, CustomerOrganization.name).where(
            CustomerOrganization.archived_at.is_(None)
        )
        clean = (query or "").strip()
        if clean:
            stmt = stmt.where(CustomerOrganization.name.ilike(f"%{clean}%"))
        # 하나 더 읽어 잘렸는지 안다. `PageRequest.fetch_limit` 과 같은 수다.
        stmt = stmt.order_by(CustomerOrganization.name, CustomerOrganization.id).limit(limit + 1)
        found = list((await self._s.execute(stmt)).all())
        return OrgChoices(
            items=[OrgChoice(id=row_id, name=name) for row_id, name in found[:limit]],
            has_more=len(found) > limit,
        )

    async def get(self, actor: Actor, asset_id: UUID) -> AssetView:
        await self._perms.require(self._s, actor, perms.ASSET_VIEW, scope=Scope.global_())
        row = await self._require(asset_id)
        (view,) = await self._views([row])
        return view

    async def tickets_of(self, actor: Actor, asset_id: UUID) -> list[AssetTicket]:
        """이 자산에 걸린 티켓. 최신순.

        **이슈 권한을 여기서 보지 않는다.** 대신 내주는 것을 좁힌다: 키·제목·
        상태뿐이다. 자산 화면은 전역 권한(`desk.asset.view`)으로 들어오는데,
        그 사람이 모든 프로젝트의 이슈를 볼 수 있다고 볼 근거는 없다 — 그래서
        제목 이상은 주지 않고, 눌러 들어간 이슈 화면이 자기 문을 지킨다.
        """
        await self._perms.require(self._s, actor, perms.ASSET_VIEW, scope=Scope.global_())
        await self._require(asset_id)
        rows = list(
            (
                await self._s.execute(
                    select(AssetLink.issue_id)
                    .where(AssetLink.asset_id == asset_id)
                    .order_by(AssetLink.linked_at.desc())
                )
            )
            .scalars()
            .all()
        )
        found = await issues.get_tickets(self._s, rows)
        out: list[AssetTicket] = []
        for issue_id in rows:
            ticket = found.get(issue_id)
            if ticket is None:
                continue
            out.append(
                AssetTicket(
                    issue_id=issue_id,
                    key=ticket.key,
                    summary=ticket.summary,
                    state_name=ticket.state_name,
                    state_category=ticket.state_category,
                )
            )
        return out

    # ── 쓰기 ────────────────────────────────────────────────────

    async def create(self, actor: Actor, payload: NewAsset) -> AssetView:
        await self._perms.require(self._s, actor, perms.ASSET_MANAGE, scope=Scope.global_())
        name = payload.name.strip()
        if not name:
            raise ValidationError("자산 이름이 필요하다.", code="desk.asset_name_required")
        kind = await self._s.get(AssetType, payload.type_id)
        if kind is None or kind.archived_at is not None:
            # 접힌 종류로는 새로 만들지 않는다. 접은 것은 "이제 안 쓴다" 는
            # 뜻이고, 그 뒤로 그 종류가 늘어나면 접은 의미가 없다.
            raise ValidationError("쓸 수 없는 자산 종류다.", code="desk.asset_type_not_available")
        row = Asset(
            type_id=payload.type_id,
            name=name[:MAX_NAME],
            tag=clean_tag(payload.tag),
            status=clean_status(payload.status),
            owner_id=await self._validated_owner(payload.owner_id),
            organization_id=payload.organization_id,
            location=(payload.location or "").strip()[:MAX_LOCATION] or None,
            note=(payload.note or "").strip()[:MAX_NOTE] or None,
        )
        self._s.add(row)
        await self._flush_unique()
        log.info("desk.asset_created", asset=str(row.id), actor=str(actor.user_id))
        (view,) = await self._views([row])
        return view

    async def update(
        self,
        actor: Actor,
        asset_id: UUID,
        *,
        name: str | None = None,
        tag: str | None = None,
        clear_tag: bool = False,
        status: str | None = None,
        owner_id: UUID | None = None,
        clear_owner: bool = False,
        organization_id: UUID | None = None,
        clear_organization: bool = False,
        location: str | None = None,
        note: str | None = None,
    ) -> AssetView:
        """부분 수정.

        비우는 것을 `clear_*` 로 따로 받는다. 부분 수정에서 `None` 은 언제나
        "안 건드린다" 이고, 그 둘을 한 필드로 표현하면 폼이 값을 안 보내는
        것만으로 담당자가 지워진다 (정형 응답·지식베이스와 같은 판단).
        """
        await self._perms.require(self._s, actor, perms.ASSET_MANAGE, scope=Scope.global_())
        row = await self._require(asset_id)
        if name is not None:
            clean = name.strip()
            if not clean:
                raise ValidationError("자산 이름이 필요하다.", code="desk.asset_name_required")
            row.name = clean[:MAX_NAME]
        if clear_tag:
            row.tag = None
        elif tag is not None:
            row.tag = clean_tag(tag)
        if status is not None:
            row.status = clean_status(status)
        if clear_owner:
            row.owner_id = None
        elif owner_id is not None:
            row.owner_id = await self._validated_owner(owner_id)
        if clear_organization:
            row.organization_id = None
        elif organization_id is not None:
            row.organization_id = organization_id
        if location is not None:
            row.location = location.strip()[:MAX_LOCATION] or None
        if note is not None:
            row.note = note.strip()[:MAX_NOTE] or None
        await self._flush_unique()
        (view,) = await self._views([row])
        return view

    async def delete(self, actor: Actor, asset_id: UUID) -> None:
        """지운다. **링크가 하나라도 있으면 거절한다.**

        잘못 만든 행을 치우는 길은 있어야 하지만, 이력을 지우는 길은 없어야
        한다. 장비가 나간 것이라면 `retired` 다.
        """
        await self._perms.require(self._s, actor, perms.ASSET_MANAGE, scope=Scope.global_())
        row = await self._require(asset_id)
        linked = await self._s.scalar(
            select(func.count()).select_from(AssetLink).where(AssetLink.asset_id == asset_id)
        )
        if linked:
            raise ConflictError(
                "티켓에 이어진 자산은 지울 수 없다. 대신 '사용 종료' 로 둔다.",
                code="desk.asset_in_use",
                details={"tickets": str(linked)},
            )
        await self._s.delete(row)
        await self._s.flush()

    # ── 티켓과의 연결 ───────────────────────────────────────────

    async def for_issue(self, actor: Actor, issue_id: UUID) -> list[LinkedAsset]:
        """이 티켓에 붙은 자산.

        **이슈를 볼 수 있어야 본다.** 자산 권한이 아니라 이슈 권한이다 —
        여기서 답하는 질문은 "이 티켓이 무엇에 대한 것인가" 이고, 그건 티켓의
        내용이다.
        """
        ref = await issues.get_issue(self._s, issue_id)
        if ref is None:
            raise NotFoundError("티켓을 찾을 수 없다.")
        await self._require_issue_view(actor, issue_id, ref.project_id)
        rows = list(
            (
                await self._s.execute(
                    select(Asset)
                    .join(AssetLink, AssetLink.asset_id == Asset.id)
                    .where(AssetLink.issue_id == issue_id)
                    .order_by(Asset.name, Asset.id)
                )
            )
            .scalars()
            .all()
        )
        types = await self._type_names(rows)
        orgs = await self._org_names(rows)
        return [
            LinkedAsset(
                asset=row,
                type_name=types.get(row.type_id, ""),
                organization_name=(orgs.get(row.organization_id) if row.organization_id else None),
            )
            for row in rows
        ]

    async def link(self, actor: Actor, issue_id: UUID, asset_id: UUID) -> LinkedAsset:
        """티켓에 자산을 잇는다.

        **두 문을 다 지난다**: 티켓을 고칠 수 있어야 하고(이 링크는 티켓의
        내용이다), 자산을 볼 수 있어야 한다(못 보는 것을 이을 수는 없다).
        """
        ref = await issues.get_issue(self._s, issue_id)
        if ref is None:
            raise NotFoundError("티켓을 찾을 수 없다.")
        await self._perms.require(
            self._s,
            actor,
            "issue.edit",
            scope=Scope.project(ref.project_id),
            subject=await self._s.get(issues.issue_model(), issue_id),
        )
        await self._perms.require(self._s, actor, perms.ASSET_VIEW, scope=Scope.global_())
        asset = await self._require(asset_id)
        self._s.add(AssetLink(asset_id=asset.id, issue_id=issue_id, linked_by=actor.user_id))
        try:
            await self._s.flush()
        except IntegrityError as exc:
            raise ConflictError("이미 이어져 있다.", code="desk.asset_already_linked") from exc
        types = await self._type_names([asset])
        orgs = await self._org_names([asset])
        log.info("desk.asset_linked", asset=str(asset.id), issue=str(issue_id))
        return LinkedAsset(
            asset=asset,
            type_name=types.get(asset.type_id, ""),
            organization_name=(orgs.get(asset.organization_id) if asset.organization_id else None),
        )

    async def unlink(self, actor: Actor, issue_id: UUID, asset_id: UUID) -> bool:
        """연결을 끊는다. 없었으면 `False`.

        자산 권한을 요구하지 않는다. 잘못 이어진 것을 떼는 일은 티켓을 고치는
        일이고, 자산 목록을 볼 수 없게 된 사람이 자기 티켓의 잘못된 링크를
        못 떼면 그 티켓은 틀린 채로 남는다.
        """
        ref = await issues.get_issue(self._s, issue_id)
        if ref is None:
            raise NotFoundError("티켓을 찾을 수 없다.")
        await self._perms.require(
            self._s,
            actor,
            "issue.edit",
            scope=Scope.project(ref.project_id),
            subject=await self._s.get(issues.issue_model(), issue_id),
        )
        row = await self._s.get(AssetLink, {"asset_id": asset_id, "issue_id": issue_id})
        if row is None:
            return False
        await self._s.delete(row)
        await self._s.flush()
        return True

    # ── 내부 ────────────────────────────────────────────────────

    def _narrow(
        self,
        stmt: Select[tuple[Asset]],
        *,
        query: str | None,
        type_id: UUID | None,
        status: str | None,
        organization_id: UUID | None,
        include_retired: bool,
    ) -> Select[tuple[Asset]]:
        text = (query or "").strip()
        if text:
            like = f"%{text}%"
            # 이름·자산번호·위치를 함께 본다. 자산번호는 대문자로 저장하므로
            # 사람이 소문자로 쳐도 찾히게 올려 준다.
            stmt = stmt.where(
                or_(
                    Asset.name.ilike(like),
                    Asset.tag.ilike(f"%{text.upper()}%"),
                    Asset.location.ilike(like),
                )
            )
        if type_id is not None:
            stmt = stmt.where(Asset.type_id == type_id)
        if status is not None:
            stmt = stmt.where(Asset.status == clean_status(status))
        if organization_id is not None:
            stmt = stmt.where(Asset.organization_id == organization_id)
        if not include_retired and status is None:
            # 상태를 지목했으면 그 말을 따른다 — "나간 장비만 보여 달라" 도
            # 정당한 요청이고, 그때 이 조건이 그것을 지운다.
            stmt = stmt.where(Asset.status != "retired")
        return stmt

    async def _require(self, asset_id: UUID) -> Asset:
        row = await self._s.get(Asset, asset_id)
        if row is None:
            raise NotFoundError("자산을 찾을 수 없다.")
        return row

    async def _require_issue_view(self, actor: Actor, issue_id: UUID, project_id: UUID) -> None:
        await self._perms.require(
            self._s,
            actor,
            "issue.view",
            scope=Scope.project(project_id),
            subject=await self._s.get(issues.issue_model(), issue_id),
        )

    async def _validated_owner(self, user_id: UUID | None) -> UUID | None:
        if user_id is None:
            return None
        found = await identity.get_user(self._s, user_id)
        if found is None:
            raise ValidationError("없는 사용자다.", code="desk.asset_owner_not_found")
        return user_id

    async def _flush_unique(self) -> None:
        try:
            await self._s.flush()
        except IntegrityError as exc:
            raise ConflictError("그 자산번호는 이미 쓰인다.", code="desk.asset_tag_taken") from exc

    async def _views(self, rows: list[Asset]) -> list[AssetView]:
        if not rows:
            return []
        types = await self._type_names(rows)
        orgs = await self._org_names(rows)
        owners = await identity.get_users(self._s, [row.owner_id for row in rows if row.owner_id])
        counts = await self._ticket_counts([row.id for row in rows])
        return [
            AssetView(
                asset=row,
                type_name=types.get(row.type_id, ""),
                owner_name=(
                    owners[row.owner_id].display_name
                    if row.owner_id and row.owner_id in owners
                    else None
                ),
                organization_name=(orgs.get(row.organization_id) if row.organization_id else None),
                ticket_count=counts.get(row.id, 0),
            )
            for row in rows
        ]

    async def _type_names(self, rows: list[Asset]) -> dict[UUID, str]:
        ids = list({row.type_id for row in rows})
        if not ids:
            return {}
        found = await self._s.execute(
            select(AssetType.id, AssetType.name).where(AssetType.id.in_(ids))
        )
        return dict(found.all())  # type: ignore[arg-type]

    async def _org_names(self, rows: list[Asset]) -> dict[UUID, str]:
        from ieum.modules.desk.models import CustomerOrganization

        ids = list({row.organization_id for row in rows if row.organization_id})
        if not ids:
            return {}
        found = await self._s.execute(
            select(CustomerOrganization.id, CustomerOrganization.name).where(
                CustomerOrganization.id.in_(ids)
            )
        )
        return dict(found.all())  # type: ignore[arg-type]

    async def _ticket_counts(self, asset_ids: list[UUID]) -> dict[UUID, int]:
        """자산별 티켓 수를 **한 번에** 센다. 행마다 세면 목록이 N+1 이 된다."""
        if not asset_ids:
            return {}
        found = await self._s.execute(
            select(AssetLink.asset_id, func.count())
            .where(AssetLink.asset_id.in_(asset_ids))
            .group_by(AssetLink.asset_id)
        )
        return {row_id: int(count) for row_id, count in found.all()}


def statuses() -> tuple[str, ...]:
    """화면이 고를 수 있는 상태. 서버가 아는 것과 화면이 그리는 것이 어긋나지
    않게 한 곳에서 읽는다."""
    return ASSET_STATUSES


__all__ = [
    "ASSET_STATUSES",
    "AssetService",
    "AssetTicket",
    "AssetType",
    "AssetTypeService",
    "AssetView",
    "LinkedAsset",
    "NewAsset",
    "OrgChoice",
    "OrgChoices",
    "clean_status",
    "clean_tag",
    "statuses",
]

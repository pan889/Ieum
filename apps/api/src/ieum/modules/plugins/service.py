"""앱 등록과 자리 지정, 그리고 앱이 써 둔 글 (M6 "플러그인 훅").

## 이 모듈이 나누는 두 가지 권한

- **자리는 관리자의 것이다.** 어느 자리에 무엇을 놓을지는 `plugins.app.manage`
  가 정한다. 앱이 스스로 자리를 넓히거나 옮길 수 없다 — 옮길 수 있으면 앱
  하나가 전이 버튼 옆으로 옮겨 앉아 우리가 쓴 글처럼 보일 수 있다.
- **안은 앱의 것이다.** 패널 본문은 앱이 자기 토큰으로 쓴다. 관리자는
  그 글을 대신 쓰지 않는다 — 그러면 앱이 아니라 그냥 메모다.

## 서버 이벤트를 여기서 배달하지 않는다

notify 의 웹훅이 서명·재시도·자동 중단·전송 로그를 이미 들고 있다(M2).
앱은 웹훅 하나를 가리키고, 켜고 끄고 지우는 것을 `notify.contracts` 로
넘긴다. 같은 것을 두 번 만들면 "몇 번 실패했나" 를 세는 규칙이 두 곳에 생긴다.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.context import Actor
from ieum.core.crypto import hash_token
from ieum.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.logging import get_logger
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.issues import contracts as issues
from ieum.modules.notify import contracts as notify
from ieum.modules.plugins import permissions as perms
from ieum.modules.plugins import slots
from ieum.modules.plugins.models import App, AppPanel, AppSlot

log = get_logger(__name__)

#: 패널 본문 상한. 이슈 상세의 한 칸이고, 문서를 담는 자리가 아니다.
MAX_BODY_CHARS = 4000
#: 앱 하나가 차지할 수 있는 자리 수. 상한이 없으면 목록 화면이 무너진다.
MAX_SLOTS_PER_APP = 12


@dataclass(frozen=True, slots=True)
class NewSlot:
    """자리 하나를 놓는 요청."""

    slot: str
    kind: str
    label: str
    url_template: str | None = None
    position: int = 0


@dataclass(frozen=True, slots=True)
class SlotView:
    row: AppSlot
    app_name: str
    app_slug: str


@dataclass(frozen=True, slots=True)
class AppView:
    """앱 하나와 그 상태. 목록 화면이 읽는 모양이다."""

    app: App
    slots: list[AppSlot]
    #: 웹훅 상태. 이벤트를 안 받는 앱은 `None` 이다.
    stream: notify.WebhookHealth | None


@dataclass(frozen=True, slots=True)
class Contribution:
    """화면 한 자리에 실릴 것 하나. **여기까지 오면 다 검사된 값이다.**"""

    app_slug: str
    app_name: str
    slot: str
    kind: str
    label: str
    #: 링크면 채워진 주소, 패널이면 `None`.
    url: str | None
    #: 패널이면 마크다운 본문, 링크면 `None`.
    body: str | None


def _new_token() -> tuple[str, str]:
    """원문과 앞머리. 원문은 부르는 쪽이 한 번 보여 주고 버린다."""
    prefix = AppTokenService.PREFIX
    raw = f"{prefix}{secrets.token_urlsafe(32)}"
    return raw, raw[: len(prefix) + 6]


class AppService:
    """앱을 등록하고 자리를 정한다. 전부 `plugins.app.manage` 다."""

    def __init__(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        self._s = session
        self._perms = permissions
        self._settings = settings

    # ── 목록 ────────────────────────────────────────────────────

    async def list_all(self, actor: Actor) -> list[AppView]:
        """등록된 앱 전부. **상한을 두지 않는 유일한 목록이다.**

        앱은 사람이 하나씩 등록하는 것이고 수십 개가 되지 않는다. 커서를
        붙이면 관리 화면이 "다음 쪽" 을 그려야 하는데, 그 쪽에 아무것도 없는
        화면을 만드는 값을 하지 않는다.
        """
        await self._perms.require(self._s, actor, perms.APP_VIEW, scope=Scope.global_())
        rows = list((await self._s.execute(select(App).order_by(App.name, App.id))).scalars().all())
        placements = await self._slots_of([row.id for row in rows])
        views: list[AppView] = []
        for row in rows:
            stream = (
                await notify.event_stream_health(self._s, row.webhook_id)
                if row.webhook_id is not None
                else None
            )
            views.append(AppView(app=row, slots=placements.get(row.id, []), stream=stream))
        return views

    async def get(self, actor: Actor, app_id: UUID) -> AppView:
        await self._perms.require(self._s, actor, perms.APP_VIEW, scope=Scope.global_())
        row = await self._require(app_id)
        stream = (
            await notify.event_stream_health(self._s, row.webhook_id)
            if row.webhook_id is not None
            else None
        )
        return AppView(
            app=row, slots=(await self._slots_of([row.id])).get(row.id, []), stream=stream
        )

    # ── 등록 ────────────────────────────────────────────────────

    async def register(
        self,
        actor: Actor,
        *,
        name: str,
        slug: str,
        description: str | None = None,
        homepage_url: str | None = None,
        events: list[str] | None = None,
        event_url: str | None = None,
    ) -> tuple[App, str]:
        """앱을 등록하고 **토큰을 한 번** 돌려준다.

        이벤트를 받으려면 주소와 이벤트 목록을 함께 준다. 그러면 웹훅이
        하나 생기고, 그 시크릿은 앱 토큰과 **다른 값**이다: 하나가 새면
        나머지 하나로 다른 일을 할 수 없어야 한다.
        """
        await self._perms.require(self._s, actor, perms.APP_MANAGE, scope=Scope.global_())
        clean_name = name.strip()
        if not clean_name:
            raise ValidationError("앱 이름이 필요하다.", code="plugins.name_required")
        clean_slug = slots.clean_slug(slug)
        if homepage_url is not None and homepage_url.strip():
            homepage = slots.clean_url_template(slots.slot("settings.link"), homepage_url)
        else:
            homepage = None

        raw, prefix = _new_token()
        row = App(
            name=clean_name,
            slug=clean_slug,
            description=(description or "").strip() or None,
            homepage_url=homepage,
            token_hash=hash_token(raw),
            token_prefix=prefix,
            created_by=actor.user_id,
        )
        self._s.add(row)
        await self._flush_unique(clean_slug)

        if events:
            if not event_url:
                raise ValidationError(
                    "이벤트를 받을 주소가 필요하다.", code="plugins.event_url_required"
                )
            row.webhook_id = await notify.open_event_stream(
                self._s,
                self._perms,
                self._settings,
                actor,
                name=f"app:{clean_slug}",
                url=event_url,
                events=events,
                secret=secrets.token_urlsafe(32),
            )
            await self._s.flush()

        log.info("app.registered", app=str(row.id), slug=clean_slug)
        return row, raw

    async def update(
        self,
        actor: Actor,
        app_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        events: list[str] | None = None,
        event_url: str | None = None,
    ) -> App:
        """`None` 은 "안 건드린다" 다 (CLAUDE.md 부분 수정 규칙)."""
        await self._perms.require(self._s, actor, perms.APP_MANAGE, scope=Scope.global_())
        row = await self._require(app_id)
        if name is not None:
            clean = name.strip()
            if not clean:
                raise ValidationError("앱 이름이 필요하다.", code="plugins.name_required")
            row.name = clean
        if description is not None:
            row.description = description.strip() or None
        if events is not None or event_url is not None:
            if row.webhook_id is None:
                if not event_url or not events:
                    raise ValidationError(
                        "이벤트를 받을 주소와 목록이 함께 필요하다.",
                        code="plugins.event_url_required",
                    )
                row.webhook_id = await notify.open_event_stream(
                    self._s,
                    self._perms,
                    self._settings,
                    actor,
                    name=f"app:{row.slug}",
                    url=event_url,
                    events=events,
                    secret=secrets.token_urlsafe(32),
                )
            else:
                await notify.retune_event_stream(
                    self._s,
                    self._perms,
                    self._settings,
                    actor,
                    row.webhook_id,
                    url=event_url,
                    events=events,
                )
        await self._s.flush()
        return row

    async def set_enabled(self, actor: Actor, app_id: UUID, *, enabled: bool) -> App:
        """끄면 **자리도 이벤트도 함께 멈춘다.**

        자리만 감추고 이벤트는 계속 보내면 끈 것이 아니다 — 끈 앱이 이슈
        제목을 계속 받아 가는 상태가 조용히 남는다.
        """
        await self._perms.require(self._s, actor, perms.APP_MANAGE, scope=Scope.global_())
        row = await self._require(app_id)
        row.enabled = enabled
        if row.webhook_id is not None:
            await notify.set_event_stream_enabled(
                self._s, self._perms, self._settings, actor, row.webhook_id, enabled=enabled
            )
        await self._s.flush()
        return row

    async def rotate_token(self, actor: Actor, app_id: UUID) -> str:
        """토큰을 새로 낸다. 옛 토큰은 그 즉시 죽는다."""
        await self._perms.require(self._s, actor, perms.APP_MANAGE, scope=Scope.global_())
        row = await self._require(app_id)
        raw, prefix = _new_token()
        row.token_hash = hash_token(raw)
        row.token_prefix = prefix
        await self._s.flush()
        log.info("app.token_rotated", app=str(row.id))
        return raw

    async def remove(self, actor: Actor, app_id: UUID) -> None:
        """앱을 지운다. **자리와 글과 웹훅이 함께 사라진다.**

        자리를 남기면 화면에 주인 없는 칸이 남고, 웹훅을 남기면 지운 앱의
        주소로 이벤트가 계속 나간다 — 지웠다고 믿는 쪽이 틀리게 된다.
        """
        await self._perms.require(self._s, actor, perms.APP_MANAGE, scope=Scope.global_())
        row = await self._require(app_id)
        webhook_id = row.webhook_id
        await self._s.delete(row)
        await self._s.flush()
        if webhook_id is not None:
            await notify.close_event_stream(self._s, self._perms, self._settings, actor, webhook_id)
            # **여기서 흘려보낸다.** 트랜잭션 경계는 라우터지만 "지웠다" 가
            # 이 함수의 약속이다 — 커밋까지 미루면 세션이 아직 그 웹훅을 들고
            # 있어서, 같은 트랜잭션에서 다시 묻는 쪽은 살아 있는 것으로 읽는다.
            await self._s.flush()
        log.info("app.removed", app=str(app_id))

    # ── 자리 ────────────────────────────────────────────────────

    async def place(self, actor: Actor, app_id: UUID, payload: NewSlot) -> AppSlot:
        """앱에게 자리 하나를 준다. 검사는 전부 `slots.py` 에 있다."""
        await self._perms.require(self._s, actor, perms.APP_MANAGE, scope=Scope.global_())
        app = await self._require(app_id)

        target = slots.slot(payload.slot)
        kind = slots.check_kind(target, payload.kind)
        label = slots.clean_label(payload.label)
        url = (
            slots.clean_url_template(target, payload.url_template or "") if kind == "link" else None
        )
        if kind == "panel" and payload.url_template:
            raise ValidationError(
                "패널 자리에는 링크 주소를 두지 않는다.", code="plugins.panel_has_url"
            )

        taken = await self._s.execute(
            select(AppSlot.id).where(AppSlot.app_id == app.id).limit(MAX_SLOTS_PER_APP)
        )
        if len(list(taken)) >= MAX_SLOTS_PER_APP:
            raise ConflictError(
                "이 앱의 자리가 너무 많다.",
                code="plugins.too_many_slots",
                details={"max": MAX_SLOTS_PER_APP},
            )

        row = AppSlot(
            app_id=app.id,
            slot=target.name,
            kind=kind,
            label=label,
            url_template=url,
            position=payload.position,
        )
        self._s.add(row)
        try:
            await self._s.flush()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ConflictError(
                "그 자리에 같은 이름이 이미 있다.", code="plugins.slot_label_taken"
            ) from exc
        return row

    async def unplace(self, actor: Actor, app_id: UUID, slot_id: UUID) -> None:
        await self._perms.require(self._s, actor, perms.APP_MANAGE, scope=Scope.global_())
        row = await self._s.get(AppSlot, slot_id)
        # 남의 앱의 자리는 존재 자체를 숨긴다.
        if row is None or row.app_id != app_id:
            raise NotFoundError("그 자리를 찾을 수 없다.")
        await self._s.delete(row)
        await self._s.flush()

    # ── 화면이 읽는 것 ──────────────────────────────────────────

    async def contributions_for_issue(self, actor: Actor, issue_id: UUID) -> list[Contribution]:
        """이 이슈에서 앱들이 놓은 것.

        **이슈를 볼 수 있는 사람만 본다.** 패널 본문에는 앱이 그 이슈에 대해
        쓴 글이 들어 있고, 그것은 이슈 내용의 일부다.
        """
        ref = await issues.get_issue(self._s, issue_id)
        if ref is None:
            raise NotFoundError("이슈를 찾을 수 없다.")
        # 이슈 하나의 문을 그 이슈의 프로젝트 스코프로 지킨다. `subject` 를
        # 함께 넘겨야 보안 등급 같은 개체 단위 가드가 걸린다 (desk 와 같다).
        await self._perms.require(
            self._s,
            actor,
            "issue.view",
            scope=Scope.project(ref.project_id),
            subject=await self._s.get(issues.issue_model(), issue_id),
        )

        ticket = (await issues.get_tickets(self._s, [issue_id])).get(issue_id)
        stmt = (
            select(AppSlot, App, AppPanel.body)
            .join(App, App.id == AppSlot.app_id)
            .outerjoin(
                AppPanel,
                (AppPanel.slot_id == AppSlot.id) & (AppPanel.issue_id == issue_id),
            )
            .where(App.enabled)
            .where(AppSlot.slot.in_(("issue.panel", "issue.link")))
            .order_by(AppSlot.position, App.name, AppSlot.label)
        )
        key = ticket.key if ticket is not None else ""
        values = {
            "issue_key": key,
            "issue_id": str(issue_id),
            # 이슈 키의 앞부분이 프로젝트 키다. 따로 물으면 질의가 하나 늘고,
            # 두 값이 어긋날 자리가 생긴다.
            "project_key": key.rpartition("-")[0],
        }
        found: list[Contribution] = []
        for placement, app, body in (await self._s.execute(stmt)).all():
            # 아직 아무것도 안 쓴 패널은 자리를 차지하지 않는다. 빈 칸을
            # 그리면 사람은 그것을 고장으로 읽는다.
            if placement.kind == "panel" and not body:
                continue
            found.append(
                Contribution(
                    app_slug=app.slug,
                    app_name=app.name,
                    slot=placement.slot,
                    kind=placement.kind,
                    label=placement.label,
                    url=(
                        slots.fill(placement.url_template or "", values)
                        if placement.kind == "link"
                        else None
                    ),
                    body=body if placement.kind == "panel" else None,
                )
            )
        return found

    async def links_for(self, actor: Actor, slot_name: str) -> list[Contribution]:
        """이슈에 매이지 않은 자리(설정 목록 등)의 링크.

        권한은 `APP_VIEW` 다 — 로그인한 사람이 자기 설정 화면에서 볼 목록이다.
        """
        await self._perms.require(self._s, actor, perms.APP_VIEW, scope=Scope.global_())
        target = slots.slot(slot_name)
        stmt: Select[tuple[AppSlot, App]] = (
            select(AppSlot, App)
            .join(App, App.id == AppSlot.app_id)
            .where(App.enabled)
            .where(AppSlot.slot == target.name)
            .order_by(AppSlot.position, App.name, AppSlot.label)
        )
        return [
            Contribution(
                app_slug=app.slug,
                app_name=app.name,
                slot=placement.slot,
                kind=placement.kind,
                label=placement.label,
                url=placement.url_template,
                body=None,
            )
            for placement, app in (await self._s.execute(stmt)).all()
        ]

    # ── 안쪽 ────────────────────────────────────────────────────

    async def _require(self, app_id: UUID) -> App:
        row = await self._s.get(App, app_id)
        if row is None:
            raise NotFoundError("앱을 찾을 수 없다.")
        return row

    async def _slots_of(self, app_ids: list[UUID]) -> dict[UUID, list[AppSlot]]:
        """앱별 자리를 **한 번에** 읽는다. 줄마다 읽으면 목록이 N+1 이 된다."""
        if not app_ids:
            return {}
        rows = (
            await self._s.execute(
                select(AppSlot)
                .where(AppSlot.app_id.in_(app_ids))
                .order_by(AppSlot.position, AppSlot.label)
            )
        ).scalars()
        found: dict[UUID, list[AppSlot]] = {}
        for row in rows:
            found.setdefault(row.app_id, []).append(row)
        return found

    async def _flush_unique(self, slug: str) -> None:
        try:
            await self._s.flush()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ConflictError(
                "그 짧은 이름은 이미 쓰인다.",
                code="plugins.slug_taken",
                details={"slug": slug},
            ) from exc


class AppTokenService:
    """앱이 자기 토큰으로 하는 일. **사람의 권한과 섞이지 않는다.**

    앱 토큰은 액터를 만들지 않는다 — 앱은 사람이 아니고, 사람의 권한 표에
    앉히면 "이 앱이 이슈를 지울 수 있나" 같은 질문이 생긴다. 앱이 할 수 있는
    일은 이 클래스에 있는 것 **뿐**이다: 자기 자리에 자기 글을 쓴다.
    """

    #: 토큰 앞머리. 화면이나 로그에서 이것이 보이면 앱 토큰인 것을 안다
    #: (identity 의 PAT 과 같은 방식이다).
    PREFIX = "ieum_app_"

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def authenticate(self, raw: str) -> App:
        row = (
            await self._s.execute(select(App).where(App.token_hash == hash_token(raw)))
        ).scalar_one_or_none()
        if row is None:
            raise AuthenticationError("앱 토큰이 유효하지 않다.", code="auth.invalid_token")
        if not row.enabled:
            # 끈 앱은 글도 못 쓴다. 끄고도 쓸 수 있으면 끈 것이 아니다.
            raise AuthenticationError("이 앱은 꺼져 있다.", code="plugins.app_disabled")
        row.last_seen_at = utcnow()
        return row

    async def write_panel(self, app: App, *, issue_key: str, label: str, body: str) -> AppPanel:
        """앱이 이슈 하나의 자기 패널에 글을 쓴다.

        이슈를 **키로** 받는 이유: 앱이 아는 것은 커밋 제목에 실려 온
        `PROJ-123` 이고 우리 UUID 가 아니다 (A22 와 같은 판단).

        `label` 로 자기 자리를 짚는다. 관리자가 자리를 안 줬으면 쓸 곳이
        없다 — **앱이 자리를 만들지 못한다.**
        """
        text = body.strip()
        if not text:
            raise ValidationError("본문이 비어 있다.", code="plugins.body_required")
        if len(text) > MAX_BODY_CHARS:
            raise ValidationError(
                "본문이 너무 길다.",
                code="plugins.body_too_long",
                details={"max": MAX_BODY_CHARS},
            )

        ref = await issues.get_issue_by_key(self._s, issue_key)
        if ref is None:
            raise NotFoundError("그 이슈를 찾을 수 없다.")

        placement = (
            await self._s.execute(
                select(AppSlot)
                .where(AppSlot.app_id == app.id)
                .where(AppSlot.slot == "issue.panel")
                .where(AppSlot.label == label.strip())
            )
        ).scalar_one_or_none()
        if placement is None:
            raise PermissionDeniedError(
                "그 이름의 패널 자리를 받지 못했다.",
                code="plugins.no_such_placement",
            )

        row = await self._s.get(AppPanel, {"slot_id": placement.id, "issue_id": ref.id})
        if row is None:
            row = AppPanel(slot_id=placement.id, issue_id=ref.id, body=text)
            self._s.add(row)
        else:
            # 덮어쓴다. 이 칸은 지금 상태를 말하는 자리고 이력이 아니다.
            row.body = text
            row.updated_at = utcnow()
        await self._s.flush()
        return row

    async def clear_panel(self, app: App, *, issue_key: str, label: str) -> bool:
        """앱이 자기 글을 거둔다. 빈 글을 쓰는 것과 지우는 것은 다르다."""
        ref = await issues.get_issue_by_key(self._s, issue_key)
        if ref is None:
            raise NotFoundError("그 이슈를 찾을 수 없다.")
        placement = (
            await self._s.execute(
                select(AppSlot)
                .where(AppSlot.app_id == app.id)
                .where(AppSlot.slot == "issue.panel")
                .where(AppSlot.label == label.strip())
            )
        ).scalar_one_or_none()
        if placement is None:
            raise PermissionDeniedError(
                "그 이름의 패널 자리를 받지 못했다.",
                code="plugins.no_such_placement",
            )
        row = await self._s.get(AppPanel, {"slot_id": placement.id, "issue_id": ref.id})
        if row is None:
            return False
        await self._s.delete(row)
        await self._s.flush()
        return True


__all__ = [
    "MAX_BODY_CHARS",
    "MAX_SLOTS_PER_APP",
    "AppService",
    "AppTokenService",
    "AppView",
    "Contribution",
    "NewSlot",
    "SlotView",
]

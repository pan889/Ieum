"""wiki 서비스. 스페이스·문서 트리·버전.

본문 정본은 마크다운 텍스트다 (ADR-0008). 저장 전에 항상 정규화한다 —
정규화가 없으면 에디터 왕복마다 diff 가 오염돼 버전 비교가 쓸모없어진다
(wiki-markdown.md 7절).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    OptimisticLockError,
    ValidationError,
)
from ieum.core.logging import get_logger
from ieum.core.markdown import MAX_LENGTH as MAX_BODY_LENGTH
from ieum.core.markdown import normalize as normalize_markdown
from ieum.core.pagination import Page as PageResult
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.wiki import permissions as perms
from ieum.modules.wiki.models import (
    MAX_DEPTH,
    SPACE_KINDS,
    Page,
    PageRestriction,
    PageVersion,
    Space,
)
from ieum.modules.wiki.portable import (
    ArchiveEntry,
    parse_document,
    read_archive,
    render_document,
    write_archive,
)
from ieum.modules.wiki.repository import (
    PageLabelRepository,
    PageRepository,
    PageRestrictionRepository,
    PageVersionRepository,
    SpaceRepository,
)
from ieum.modules.wiki.slug import join_path, slugify, unique_slug

log = get_logger(__name__)

_SPACE_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
MAX_LABELS = 30


@dataclass(frozen=True, slots=True)
class NewPage:
    space_id: UUID
    title: str
    parent_id: UUID | None = None
    body: str = ""
    front_matter: dict[str, Any] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    #: True 면 바로 게시한다. 기본은 초안 — 쓰다 만 문서가 트리에 뜨면 곤란하다.
    publish: bool = False


@dataclass(frozen=True, slots=True)
class PageView:
    page: Page
    #: 게시된 본문. 초안만 있으면 None.
    current: PageVersion | None
    labels: list[str]
    space_key: str

    @property
    def body(self) -> str:
        return self.current.body if self.current else ""


class PageRestrictionGuard:
    """문서 열람 제한. 스코프 권한을 통과한 뒤 한 번 더 거른다.

    이슈 보안 레벨과 같은 자리다 (auth.md 5절). 제한은 **상속된다** —
    상위 문서를 못 보면 그 아래도 못 본다. 안 그러면 링크를 아는 사람이
    제한된 가지의 안쪽을 그대로 열 수 있다.
    """

    async def allows(
        self, session: AsyncSession, actor: Actor, permission: str, subject: Any
    ) -> bool:
        if not isinstance(subject, Page):
            return True
        mode = "edit" if permission in _EDIT_PERMISSIONS else "view"
        return await _passes_restrictions(session, actor, subject, mode)


#: 이 권한들은 편집 제한을 본다. 나머지는 열람 제한만 본다.
_EDIT_PERMISSIONS = frozenset(
    {perms.PAGE_EDIT, perms.PAGE_DELETE, perms.PAGE_MOVE, perms.PAGE_RESTRICT}
)


async def _passes_restrictions(session: AsyncSession, actor: Actor, page: Page, mode: str) -> bool:
    """자기 자신과 모든 조상의 제한을 본다.

    조상 경로는 `path` 에 이미 들어 있으므로 사슬을 질의 한 번으로 가져온다.
    스페이스 전체를 읽지 않는다 — 문서가 많은 스페이스에서 권한 검사마다
    전부 긁으면 곧 못 쓰게 된다.

    편집 제한은 열람을 **함의한다**: 못 보는 문서를 고칠 수는 없다. 그래서
    edit 을 물으면 두 모드를 한 번에 본다(질의는 여전히 한 벌이다).
    """
    ancestors = _ancestor_paths(page.path)
    chain = [page, *await PageRepository(session).by_paths(page.space_id, ancestors)]
    restrictions = await PageRestrictionRepository(session).for_pages([p.id for p in chain])
    if not restrictions:
        return True

    modes = ("view", "edit") if mode == "edit" else ("view",)
    for node in chain:
        for rule_mode in modes:
            rules = [r for r in restrictions.get(node.id, []) if r.mode == rule_mode]
            # 규칙이 없으면 제한이 없는 것이다. 있으면 그중 하나에 걸려야 한다.
            if rules and not any(r.principal_id in actor.principal_ids for r in rules):
                return False
    return True


def _ancestor_paths(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


class SpaceService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._spaces = SpaceRepository(session)
        self._pages = PageRepository(session)

    async def create(
        self,
        actor: Actor,
        *,
        key: str,
        name: str,
        description: str | None = None,
        kind: str = "team",
    ) -> Space:
        await self._perms.require(self._s, actor, perms.SPACE_CREATE, scope=Scope.global_())
        normalized = key.strip().upper()
        if not _SPACE_KEY.match(normalized):
            raise ValidationError(
                "스페이스 키는 영문 대문자로 시작하는 2~16자여야 한다.",
                code="wiki.invalid_space_key",
                details={"key": key},
            )
        if kind not in SPACE_KINDS:
            raise ValidationError(
                "알 수 없는 스페이스 종류다.",
                code="wiki.invalid_space_kind",
                details={"kind": kind, "known": list(SPACE_KINDS)},
            )
        if await self._spaces.key_exists(normalized):
            raise ConflictError("이미 쓰이는 스페이스 키다.", code="wiki.space_key_taken")

        space = self._spaces.add(
            Space(key=normalized, name=name.strip(), description=description, kind=kind)
        )
        await self._s.flush()
        log.info("wiki.space_created", actor=str(actor.user_id), key=space.key)
        return space

    async def get(self, actor: Actor, space_id: UUID) -> Space:
        space = await self._require_space(space_id)
        await self._perms.require(self._s, actor, perms.PAGE_VIEW, scope=Scope.space(space.id))
        return space

    async def get_by_key(self, actor: Actor, key: str) -> Space:
        space = await self._spaces.get_by_key(key)
        if space is None:
            raise NotFoundError("스페이스를 찾을 수 없다.")
        await self._perms.require(self._s, actor, perms.PAGE_VIEW, scope=Scope.space(space.id))
        return space

    async def list_for(
        self,
        actor: Actor,
        request: PageRequest,
        *,
        include_archived: bool = False,
        query: str | None = None,
    ) -> PageResult[Space]:
        acl = await self._perms.acl_for(self._s, actor, perms.PAGE_VIEW)
        return await self._spaces.list_page(
            request, acl=acl, include_archived=include_archived, query=query
        )

    async def update(
        self,
        actor: Actor,
        space_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        home_page_id: UUID | None = None,
        clear_home_page: bool = False,
    ) -> Space:
        space = await self._require_space(space_id)
        await self._perms.require(self._s, actor, perms.SPACE_ADMIN, scope=Scope.space(space.id))
        if name is not None:
            space.name = name.strip()
        if description is not None:
            space.description = description
        if clear_home_page:
            space.home_page_id = None
        elif home_page_id is not None:
            page = await self._pages.get(home_page_id)
            if page is None or page.space_id != space.id:
                raise ValidationError(
                    "이 스페이스의 문서가 아니다.", code="wiki.home_page_not_in_space"
                )
            space.home_page_id = home_page_id
        await self._s.flush()
        return space

    async def archive(self, actor: Actor, space_id: UUID) -> Space:
        space = await self._require_space(space_id)
        await self._perms.require(self._s, actor, perms.SPACE_ADMIN, scope=Scope.space(space.id))
        if space.archived_at is None:
            space.archived_at = utcnow()
            await self._s.flush()
        return space

    async def _require_space(self, space_id: UUID) -> Space:
        space = await self._spaces.get(space_id)
        if space is None:
            raise NotFoundError("스페이스를 찾을 수 없다.")
        return space


class PageService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._spaces = SpaceRepository(session)
        self._pages = PageRepository(session)
        self._versions = PageVersionRepository(session)
        self._labels = PageLabelRepository(session)
        self._restrictions = PageRestrictionRepository(session)

    # ── 조회 ────────────────────────────────────────────────────

    async def get(self, actor: Actor, page_id: UUID) -> PageView:
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_VIEW, scope=Scope.space(page.space_id), subject=page
        )
        return await self.to_view(page)

    async def get_by_path(self, actor: Actor, space_key: str, path: str) -> PageView:
        """`SPACE/부모/자식` 으로 연다. 사람이 주고받는 주소다."""
        space = await self._spaces.get_by_key(space_key)
        if space is None:
            raise NotFoundError("스페이스를 찾을 수 없다.")
        page = await self._pages.get_by_path(space.id, path.strip("/"))
        if page is None:
            raise NotFoundError("문서를 찾을 수 없다.")
        await self._perms.require(
            self._s, actor, perms.PAGE_VIEW, scope=Scope.space(space.id), subject=page
        )
        return await self.to_view(page, space=space)

    async def tree(self, actor: Actor, space_id: UUID) -> list[Page]:
        """스페이스의 문서 트리. **볼 수 있는 것만** 남긴다.

        제한을 통과 못 한 가지는 통째로 빠진다 — 제목만 보여도 정보가 샌다.
        """
        space = await self._require_space(space_id)
        await self._perms.require(self._s, actor, perms.PAGE_VIEW, scope=Scope.space(space.id))
        rows = await self._pages.tree_of(space.id)
        return await self._visible(actor, rows)

    async def _visible(self, actor: Actor, rows: list[Page]) -> list[Page]:
        """제한에 걸리는 문서와 그 후손을 뺀다. 제한을 **한 번에** 조회한다."""
        restrictions = await self._restrictions.for_pages([p.id for p in rows])
        if not restrictions:
            return rows

        blocked: set[str] = set()
        visible: list[Page] = []
        # path 순으로 정렬돼 있으니 부모가 항상 먼저 나온다.
        for page in sorted(rows, key=lambda p: p.path):
            if any(page.path.startswith(f"{prefix}/") for prefix in blocked):
                blocked.add(page.path)
                continue
            rules = [r for r in restrictions.get(page.id, []) if r.mode == "view"]
            if rules and not any(r.principal_id in actor.principal_ids for r in rules):
                blocked.add(page.path)
                continue
            visible.append(page)
        return visible

    async def history(self, actor: Actor, page_id: UUID) -> list[PageVersion]:
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_VIEW, scope=Scope.space(page.space_id), subject=page
        )
        return await self._versions.history(page.id)

    async def version(self, actor: Actor, page_id: UUID, number: int) -> PageVersion:
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_VIEW, scope=Scope.space(page.space_id), subject=page
        )
        version = await self._versions.by_number(page.id, number)
        if version is None:
            raise NotFoundError("그 판을 찾을 수 없다.")
        return version

    # ── 생성·수정 ───────────────────────────────────────────────

    async def create(self, actor: Actor, payload: NewPage) -> PageView:
        space = await self._require_space(payload.space_id)
        await self._perms.require(self._s, actor, perms.PAGE_CREATE, scope=Scope.space(space.id))

        title = self._validate_title(payload.title)
        body = self._validate_body(payload.body)
        parent = await self._resolve_parent(space.id, payload.parent_id)
        if parent is not None:
            # 상위 문서를 편집할 수 있어야 그 아래에 만들 수 있다. 아니면
            # 제한된 가지 안쪽에 아무나 문서를 끼워 넣을 수 있다.
            await self._perms.require(
                self._s, actor, perms.PAGE_EDIT, scope=Scope.space(space.id), subject=parent
            )

        siblings = await self._pages.children_of(parent.id if parent else None, space.id)
        slug = unique_slug(slugify(title), {s.slug for s in siblings})
        parent_path = parent.path if parent else ""

        page = self._pages.add(
            Page(
                space_id=space.id,
                parent_id=parent.id if parent else None,
                path=join_path(parent_path, slug),
                slug=slug,
                title=title,
                status="published" if payload.publish else "draft",
                position=len(siblings),
            )
        )
        await self._s.flush()

        version = await self._write_version(page, actor, title=title, body=body, message=None)
        if payload.publish:
            page.current_version_id = version.id

        if payload.labels:
            await self._labels.replace(page.id, self._validate_labels(payload.labels))
        if payload.front_matter:
            version.front_matter = payload.front_matter

        await self._s.flush()
        log.info("wiki.page_created", actor=str(actor.user_id), page=str(page.id))
        return await self.to_view(page, space=space)

    async def update(
        self,
        actor: Actor,
        page_id: UUID,
        *,
        title: str | None = None,
        body: str | None = None,
        front_matter: dict[str, Any] | None = None,
        labels: list[str] | None = None,
        message: str | None = None,
        publish: bool | None = None,
        expected_version: int | None = None,
    ) -> PageView:
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_EDIT, scope=Scope.space(page.space_id), subject=page
        )
        self._check_version(page, expected_version)

        changed = False
        new_title = page.title if title is None else self._validate_title(title)
        current = await self._current_version(page)
        new_body = current.body if current else ""
        if body is not None:
            new_body = self._validate_body(body)

        # 본문이나 제목이 바뀌면 **새 판**을 만든다. 판을 덮어쓰면 이력이
        # 거짓말이 된다 — 되돌릴 수 없는 편집이 조용히 섞인다.
        if new_title != page.title or (current is None) or new_body != current.body:
            version = await self._write_version(
                page, actor, title=new_title, body=new_body, message=message
            )
            if front_matter is not None:
                version.front_matter = front_matter
            elif current is not None:
                version.front_matter = dict(current.front_matter)
            page.title = new_title
            if page.status == "published" or publish:
                page.current_version_id = version.id
            changed = True
        elif front_matter is not None and current is not None:
            current.front_matter = front_matter
            changed = True

        if publish is not None and publish and page.status != "published":
            page.status = "published"
            if page.current_version_id is None:
                latest = await self._versions.history(page.id, limit=1)
                if latest:
                    page.current_version_id = latest[0].id
            changed = True

        if labels is not None:
            before = await self._labels.for_page(page.id)
            after = self._validate_labels(labels)
            if sorted(before) != sorted(after):
                await self._labels.replace(page.id, after)
                changed = True

        if changed:
            page.version += 1
            await self._s.flush()
        return await self.to_view(page)

    async def restore(self, actor: Actor, page_id: UUID, number: int) -> PageView:
        """옛 판의 내용으로 **새 판**을 만든다.

        판을 되감지 않는다 — 되감으면 그 사이 이력이 사라진다. 복원도 편집의
        하나이므로 이력에 남아야 한다.
        """
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_EDIT, scope=Scope.space(page.space_id), subject=page
        )
        source = await self._versions.by_number(page.id, number)
        if source is None:
            raise NotFoundError("그 판을 찾을 수 없다.")

        version = await self._write_version(
            page,
            actor,
            title=source.title,
            body=source.body,
            message=f"{number}판으로 복원",
        )
        version.front_matter = dict(source.front_matter)
        page.title = source.title
        if page.status == "published":
            page.current_version_id = version.id
        page.version += 1
        await self._s.flush()
        return await self.to_view(page)

    async def move(
        self,
        actor: Actor,
        page_id: UUID,
        *,
        new_parent_id: UUID | None,
        position: int | None = None,
    ) -> PageView:
        """문서를 옮긴다. 후손의 경로도 함께 고친다."""
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_MOVE, scope=Scope.space(page.space_id), subject=page
        )

        parent = await self._resolve_parent(page.space_id, new_parent_id)
        if parent is not None:
            if parent.id == page.id:
                raise ValidationError("자기 자신 아래로 옮길 수 없다.", code="wiki.move_into_self")
            # 후손 아래로 옮기면 트리가 끊긴 고리가 된다.
            if parent.path == page.path or parent.path.startswith(f"{page.path}/"):
                raise ValidationError(
                    "자기 하위 문서 아래로 옮길 수 없다.", code="wiki.move_into_descendant"
                )
            await self._perms.require(
                self._s, actor, perms.PAGE_EDIT, scope=Scope.space(page.space_id), subject=parent
            )

        subtree = await self._pages.subtree(page.space_id, page.path)
        depth_below = max(p.path.count("/") for p in subtree) - page.path.count("/")
        new_depth = (parent.path.count("/") + 1 if parent else 0) + depth_below
        if new_depth >= MAX_DEPTH:
            raise ValidationError(
                f"문서 깊이는 {MAX_DEPTH}단계를 넘을 수 없다.",
                code="wiki.max_depth_exceeded",
                details={"max": MAX_DEPTH},
            )

        siblings = await self._pages.children_of(parent.id if parent else None, page.space_id)
        taken = {s.slug for s in siblings if s.id != page.id}
        slug = unique_slug(page.slug, taken)
        old_path = page.path
        new_path = join_path(parent.path if parent else "", slug)

        page.parent_id = parent.id if parent else None
        page.slug = slug
        page.path = new_path
        if position is not None:
            page.position = position
        page.version += 1

        # 후손 경로를 다시 쓴다. 접두사만 바꾼다 — 각자의 slug 은 그대로다.
        for descendant in subtree:
            if descendant.id == page.id:
                continue
            descendant.path = new_path + descendant.path[len(old_path) :]

        await self._s.flush()
        log.info("wiki.page_moved", actor=str(actor.user_id), page=str(page.id), path=page.path)
        return await self.to_view(page)

    async def archive(self, actor: Actor, page_id: UUID) -> PageView:
        """휴지통으로. 후손도 함께 간다 — 부모 없는 문서를 남기지 않는다."""
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_DELETE, scope=Scope.space(page.space_id), subject=page
        )
        now = utcnow()
        for node in await self._pages.subtree(page.space_id, page.path):
            if node.archived_at is None:
                node.archived_at = now
        page.version += 1
        await self._s.flush()
        return await self.to_view(page)

    # ── 제한 ────────────────────────────────────────────────────

    async def set_restrictions(
        self,
        actor: Actor,
        page_id: UUID,
        *,
        mode: str,
        principals: list[tuple[str, UUID]],
    ) -> list[PageRestriction]:
        """열람·편집 제한을 통째로 바꾼다. 빈 목록이면 제한 해제."""
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_RESTRICT, scope=Scope.space(page.space_id), subject=page
        )
        if mode not in {"view", "edit"}:
            raise ValidationError("view 또는 edit 이어야 한다.", code="wiki.invalid_restriction")

        await self._restrictions.clear(page.id, mode)
        for kind, principal_id in principals:
            if kind not in {"user", "group"}:
                raise ValidationError(
                    "user 또는 group 이어야 한다.", code="wiki.invalid_restriction"
                )
            self._restrictions.add(
                PageRestriction(
                    page_id=page.id, mode=mode, principal_kind=kind, principal_id=principal_id
                )
            )
        await self._s.flush()
        return await self._restrictions.for_page(page.id)

    async def restrictions(self, actor: Actor, page_id: UUID) -> list[PageRestriction]:
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_VIEW, scope=Scope.space(page.space_id), subject=page
        )
        return await self._restrictions.for_page(page.id)

    # ── 임포트·내보내기 ─────────────────────────────────────────

    async def import_markdown(
        self,
        actor: Actor,
        *,
        space_id: UUID,
        filename: str,
        content: str,
        parent_id: UUID | None = None,
    ) -> PageView:
        """`.md` 한 편을 문서로. 제목은 front matter → 첫 H1 → 파일명 순."""
        stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "Untitled"
        parsed = parse_document(content, fallback_title=stem)
        return await self.create(
            actor,
            NewPage(
                space_id=space_id,
                title=parsed.title,
                parent_id=parent_id,
                body=parsed.body,
                front_matter=parsed.front_matter,
                labels=parsed.labels,
                # 올린 문서는 바로 읽히는 게 기대다. 초안으로 두면 올려 놓고
                # "왜 안 보이지" 가 된다.
                publish=True,
            ),
        )

    async def import_archive(
        self, actor: Actor, *, space_id: UUID, data: bytes, parent_id: UUID | None = None
    ) -> list[PageView]:
        """ZIP 묶음을 트리째. 폴더 구조가 곧 문서 트리다.

        얕은 것부터 만든다 — 부모가 먼저 있어야 자식이 그 아래로 들어간다.
        폴더에 대응하는 `.md` 가 없으면 그 자리에 빈 문서를 만들지 않고,
        자식을 한 단계 위로 붙인다. 빈 껍데기 문서가 트리에 늘어서면
        훑어보기 더 어렵다.
        """
        space = await self._require_space(space_id)
        await self._perms.require(self._s, actor, perms.PAGE_CREATE, scope=Scope.space(space.id))
        entries = read_archive(data)

        #: ZIP 안의 폴더 경로 → 만들어진 문서. 자식이 부모를 찾을 때 쓴다.
        created_by_dir: dict[str, UUID] = {}
        views: list[PageView] = []
        for entry in entries:
            directory, _, _ = entry.path.rpartition("/")
            views.append(
                await self._import_entry(
                    actor, space_id, entry, directory, created_by_dir, parent_id
                )
            )
        log.info(
            "wiki.archive_imported",
            actor=str(actor.user_id),
            space=str(space_id),
            pages=len(views),
        )
        return views

    async def _import_entry(
        self,
        actor: Actor,
        space_id: UUID,
        entry: ArchiveEntry,
        directory: str,
        created_by_dir: dict[str, UUID],
        root_parent: UUID | None,
    ) -> PageView:
        parent = self._nearest_parent(directory, created_by_dir) or root_parent
        view = await self.create(
            actor,
            NewPage(
                space_id=space_id,
                title=entry.document.title,
                parent_id=parent,
                body=entry.document.body,
                front_matter=entry.document.front_matter,
                labels=entry.document.labels,
                publish=True,
            ),
        )
        stem = entry.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        # `docs/deploy/index.md` 는 `docs/deploy` 폴더의 대표 문서로 본다.
        # 그래야 그 폴더의 다른 파일들이 이 문서 아래로 들어간다.
        if stem.lower() in {"index", "readme"}:
            created_by_dir[directory] = view.page.id
        else:
            child_dir = f"{directory}/{stem}" if directory else stem
            created_by_dir[child_dir] = view.page.id
        return view

    @staticmethod
    def _nearest_parent(directory: str, created: dict[str, UUID]) -> UUID | None:
        """가장 가까운 조상 폴더의 문서. 없으면 None(최상위)."""
        current = directory
        while current:
            if current in created:
                return created[current]
            current, _, _ = current.rpartition("/")
        return None

    async def export_markdown(self, actor: Actor, page_id: UUID) -> tuple[str, str]:
        """문서 하나를 `(파일명, 내용)` 으로."""
        view = await self.get(actor, page_id)
        content = render_document(
            title=view.page.title,
            body=view.body,
            labels=view.labels,
            front_matter=view.current.front_matter if view.current else {},
        )
        return f"{view.page.slug}.md", content

    async def export_space(self, actor: Actor, space_id: UUID) -> bytes:
        """스페이스를 ZIP 으로. 문서 경로가 그대로 폴더 구조가 된다."""
        space = await self._require_space(space_id)
        await self._perms.require(self._s, actor, perms.PAGE_VIEW, scope=Scope.space(space.id))
        rows = await self._visible(actor, await self._pages.tree_of(space.id))

        files: list[tuple[str, str]] = []
        for page in rows:
            view = await self.to_view(page, space=space)
            files.append(
                (
                    f"{page.path}.md",
                    render_document(
                        title=page.title,
                        body=view.body,
                        labels=view.labels,
                        front_matter=view.current.front_matter if view.current else {},
                    ),
                )
            )
        return write_archive(files)

    # ── 내부 ────────────────────────────────────────────────────

    async def to_view(self, page: Page, *, space: Space | None = None) -> PageView:
        current = await self._current_version(page)
        space = space or await self._spaces.get(page.space_id)
        return PageView(
            page=page,
            current=current,
            labels=await self._labels.for_page(page.id),
            space_key=space.key if space else "",
        )

    async def _current_version(self, page: Page) -> PageVersion | None:
        if page.current_version_id is None:
            # 아직 게시 전이면 가장 최근 초안을 본다. 편집 화면이 이걸 연다.
            latest = await self._versions.history(page.id, limit=1)
            return latest[0] if latest else None
        return await self._versions.get(page.current_version_id)

    async def _write_version(
        self, page: Page, actor: Actor, *, title: str, body: str, message: str | None
    ) -> PageVersion:
        version = self._versions.add(
            PageVersion(
                page_id=page.id,
                number=await self._versions.next_number(page.id),
                title=title,
                # 저장 전에 정규화한다. 에디터·API·임포터가 모두 이 함수를 지난다.
                body=normalize_markdown(body),
                author_id=actor.user_id,
                message=message,
            )
        )
        await self._s.flush()
        return version

    async def _resolve_parent(self, space_id: UUID, parent_id: UUID | None) -> Page | None:
        if parent_id is None:
            return None
        parent = await self._pages.get(parent_id)
        if parent is None or parent.space_id != space_id:
            raise ValidationError("상위 문서를 찾을 수 없다.", code="wiki.parent_not_found")
        if parent.path.count("/") + 1 >= MAX_DEPTH:
            raise ValidationError(
                f"문서 깊이는 {MAX_DEPTH}단계를 넘을 수 없다.",
                code="wiki.max_depth_exceeded",
                details={"max": MAX_DEPTH},
            )
        return parent

    async def _require_page(self, page_id: UUID) -> Page:
        page = await self._pages.get(page_id)
        if page is None:
            raise NotFoundError("문서를 찾을 수 없다.")
        return page

    async def _require_space(self, space_id: UUID) -> Space:
        space = await self._spaces.get(space_id)
        if space is None:
            raise NotFoundError("스페이스를 찾을 수 없다.")
        return space

    @staticmethod
    def _check_version(page: Page, expected: int | None) -> None:
        if expected is not None and expected != page.version:
            raise OptimisticLockError(
                "그 사이 문서가 바뀌었다.", details={"expected": expected, "actual": page.version}
            )

    @staticmethod
    def _validate_title(title: str) -> str:
        cleaned = title.strip()
        if not cleaned:
            raise ValidationError("제목이 비었다.", code="wiki.empty_title")
        if len(cleaned) > 500:
            raise ValidationError("제목이 너무 길다.", code="wiki.title_too_long")
        return cleaned

    @staticmethod
    def _validate_body(body: str) -> str:
        if len(body) > MAX_BODY_LENGTH:
            raise ValidationError(
                "본문이 너무 길다.",
                code="wiki.body_too_long",
                details={"max": MAX_BODY_LENGTH},
            )
        return body

    @staticmethod
    def _validate_labels(labels: list[str]) -> list[str]:
        cleaned = [label.strip().lower() for label in labels if label.strip()]
        if len(cleaned) > MAX_LABELS:
            raise ValidationError(
                f"라벨은 {MAX_LABELS}개까지다.",
                code="wiki.too_many_labels",
                details={"max": MAX_LABELS},
            )
        return list(dict.fromkeys(cleaned))


__all__ = [
    "NewPage",
    "PageRestrictionGuard",
    "PageService",
    "PageView",
    "SpaceService",
]

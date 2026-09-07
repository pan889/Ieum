"""wiki 서비스. 스페이스·문서 트리·버전.

본문 정본은 마크다운 텍스트다 (ADR-0008). 저장 전에 항상 정규화한다 —
정규화가 없으면 에디터 왕복마다 diff 가 오염돼 버전 비교가 쓸모없어진다
(wiki-markdown.md 7절).
"""

from __future__ import annotations

import mimetypes
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.attachments import AttachmentService
from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    OptimisticLockError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.logging import get_logger
from ieum.core.markdown import MAX_LENGTH as MAX_BODY_LENGTH
from ieum.core.markdown import extract_mentions, to_plaintext
from ieum.core.markdown import normalize as normalize_markdown
from ieum.core.markdown.anchors import Anchor, AnchorMatch, locate
from ieum.core.markdown.diff import DiffResult, diff_lines
from ieum.core.markdown.links import ISSUE as ISSUE_SCHEME
from ieum.core.markdown.links import extract_links
from ieum.core.outbox import publish
from ieum.core.pagination import Page as PageResult
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.core.storage import ObjectStore
from ieum.core.time import utcnow
from ieum.modules.identity import contracts as identity
from ieum.modules.issues import contracts as issues
from ieum.modules.org import contracts as org_links
from ieum.modules.search import contracts as search
from ieum.modules.wiki import events as wiki_events
from ieum.modules.wiki import permissions as perms
from ieum.modules.wiki.attachments import OWNER_PAGE
from ieum.modules.wiki.models import (
    MAX_DEPTH,
    PAGE_KINDS,
    SPACE_KINDS,
    Page,
    PageComment,
    PageDraft,
    PageRestriction,
    PageTemplate,
    PageVersion,
    Space,
)
from ieum.modules.wiki.portable import (
    MAX_EXPORT_ASSET_BYTES,
    SKIPPED_MANIFEST,
    ArchiveEntry,
    asset_folder,
    asset_targets,
    attachment_targets,
    encode_target,
    parse_document,
    read_archive,
    render_document,
    resolve_asset,
    rewrite_assets,
    write_archive,
)
from ieum.modules.wiki.repository import (
    PageCommentRepository,
    PageDraftRepository,
    PageLabelRepository,
    PageRepository,
    PageRestrictionRepository,
    PageTemplateRepository,
    PageVersionRepository,
    SpaceRepository,
)
from ieum.modules.wiki.slug import join_path, slugify, unique_slug

log = get_logger(__name__)

#: 블로그 글의 주소 앞머리. 트리 문서와 섞이지 않게 한 칸 띄워 둔다.
BLOG_PREFIX = "blog"

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
    #: `page`(트리) 또는 `blog`(날짜순). 블로그 글은 부모를 갖지 않는다.
    kind: str = "page"


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


def _kind_of(front_matter: dict[str, Any]) -> str:
    """front matter 의 `kind`. 모르는 값이면 보통 문서로 본다.

    내보낸 `.md` 를 다시 올렸을 때 블로그 글이 트리 문서가 되어 버리면
    라운드트립이 깨진다 (wiki-markdown.md 8절).
    """
    value = front_matter.get("kind")
    return value if isinstance(value, str) and value in PAGE_KINDS else "page"


def _with_kind(front_matter: dict[str, Any], kind: str) -> dict[str, Any]:
    """보통 문서에는 안 적는다. 기본값을 적으면 모든 파일이 지저분해진다."""
    return front_matter if kind == "page" else {**front_matter, "kind": kind}


def _skipped_manifest(lines: list[str]) -> str:
    """내보내기에서 빠진 첨부의 목록. 사람이 읽고 손으로 옮길 수 있게."""
    header = (
        "These attachments were not included in the export because the archive\n"
        f"would exceed {MAX_EXPORT_ASSET_BYTES} bytes of attachments.\n"
        "Download them from the original site.\n\n"
        "page\treference\tsize\n"
    )
    return header + "\n".join(lines) + "\n"


async def resolve_page_mentions(
    session: AsyncSession,
    permissions: PermissionService,
    page: Page,
    text: str | None,
) -> list[UUID]:
    """본문의 멘션 중 **이 문서를 볼 수 있는 사람만** 남긴다.

    권한 검사를 여기서 하는 이유는 notify 가 문서 제한(restriction)을 못 보기
    때문이다. 거르지 않으면 아무나 멘션해서 제한된 문서의 제목을 알림으로
    흘릴 수 있다 — 이슈 쪽과 같은 규칙이다.
    """
    if not text:
        return []
    scope = Scope.space(page.space_id)
    allowed: list[UUID] = []
    for user_id in extract_mentions(text):
        mentioned = await identity.load_actor(session, user_id)
        if mentioned is None or not mentioned.is_active:
            continue
        if not await permissions.has(
            session, mentioned, perms.PAGE_VIEW, scope=scope, subject=page
        ):
            continue
        allowed.append(user_id)
    return allowed


class PageService:
    def __init__(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        *,
        store: ObjectStore | None = None,
    ) -> None:
        self._s = session
        self._perms = permissions
        # 임포트가 첨부를 흡수할 때만 쓴다. 없으면 상대경로를 그대로 둔다 —
        # 조용히 링크를 지우느니 깨진 링크가 낫다. 무엇이 있었는지는 남는다.
        self._store = store
        self._spaces = SpaceRepository(session)
        self._pages = PageRepository(session)
        self._versions = PageVersionRepository(session)
        self._labels = PageLabelRepository(session)
        self._restrictions = PageRestrictionRepository(session)
        self._drafts = PageDraftRepository(session)

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
        kind = self._validate_kind(payload.kind)
        if kind == "blog":
            return await self._create_post(actor, space, title=title, payload=payload)
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
        await self._reindex(page, space=space)
        await self._relink(page)
        # 초안은 알리지 않는다. 아직 아무에게도 보이지 않는 글이다.
        # 빈 문서도 마찬가지다 — 화면의 만들기는 제목만 받고 본문은 그다음에
        # 쓴다. 여기서 알리면 문서 하나에 "새 문서" 와 "수정됨" 이 잇달아
        # 날아간다. 사람이 한 일은 하나인데.
        if page.status == "published" and body.strip():
            publish(
                self._s,
                wiki_events.PagePublished(
                    aggregate_id=page.id,
                    space_id=space.id,
                    space_key=space.key,
                    path=page.path,
                    title=page.title,
                    actor_id=actor.user_id,
                    mentioned_ids=await resolve_page_mentions(self._s, self._perms, page, body),
                ),
            )
        log.info("wiki.page_created", actor=str(actor.user_id), page=str(page.id))
        return await self.to_view(page, space=space)

    @staticmethod
    def _validate_kind(kind: str) -> str:
        if kind not in PAGE_KINDS:
            raise ValidationError(
                "문서 성격이 올바르지 않다.",
                code="wiki.page_kind_invalid",
                details={"allowed": list(PAGE_KINDS)},
            )
        return kind

    async def _create_post(
        self, actor: Actor, space: Space, *, title: str, payload: NewPage
    ) -> PageView:
        """블로그 글 하나.

        트리에 안 들어간다 — 날짜순으로 흐르는 글이라 위치가 아니라 시간이
        자리를 정한다. 주소는 `blog/<slug>` 라 트리 문서와 섞이지 않는다.
        나머지(버전·코멘트·검색·권한)는 문서와 완전히 같다.
        """
        body = self._validate_body(payload.body)
        taken = {page.slug for page in await self._pages.blog_slugs(space.id)}
        slug = unique_slug(slugify(title), taken)
        page = self._pages.add(
            Page(
                space_id=space.id,
                parent_id=None,
                kind="blog",
                path=join_path(BLOG_PREFIX, slug),
                slug=slug,
                title=title,
                status="published" if payload.publish else "draft",
                published_at=utcnow() if payload.publish else None,
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
        await self._reindex(page, space=space)
        await self._relink(page)
        if page.status == "published" and body.strip():
            publish(
                self._s,
                wiki_events.PagePublished(
                    aggregate_id=page.id,
                    space_id=space.id,
                    space_key=space.key,
                    path=page.path,
                    title=page.title,
                    actor_id=actor.user_id,
                    mentioned_ids=await resolve_page_mentions(self._s, self._perms, page, body),
                ),
            )
        log.info("wiki.post_created", actor=str(actor.user_id), page=str(page.id))
        return await self.to_view(page, space=space)

    async def posts(
        self, actor: Actor, space_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[PageView], int]:
        """블로그 글 목록, 최신순."""
        space = await self._require_space(space_id)
        await self._perms.require(self._s, actor, perms.PAGE_VIEW, scope=Scope.space(space.id))
        rows = await self._visible(
            actor, await self._pages.blog_of(space.id, limit=limit, offset=offset)
        )
        views = [await self.to_view(row, space=space) for row in rows]
        return views, await self._pages.blog_count(space.id)

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
        # 게시 여부를 미리 잡아 둔다. 아래에서 뒤집히므로, 뒤에서 보면
        # "처음 게시" 와 "이미 게시된 문서 수정" 을 구분할 수 없다.
        was_published = page.status == "published"
        new_version: PageVersion | None = None
        new_title = page.title if title is None else self._validate_title(title)
        current = await self._current_version(page)
        previous_body = current.body if current else ""
        new_body = previous_body
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
            new_version = version
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
            await self._reindex(page)
            await self._relink(page)
            await self._announce_change(
                page,
                actor,
                body=new_body,
                version=new_version,
                message=message,
                # 본문이 비어 있던 문서에 처음 글이 들어온 것이 "새 문서" 다.
                # 만들기가 제목만 받으므로, 이걸 수정이라고 하면 아무도 새
                # 문서를 알림으로 못 본다.
                first_time=not was_published or not previous_body.strip(),
            )
            # 저장했으면 초안은 할 일을 다했다. 남기면 다음에 열 때
            # "저장 안 한 편집이 있다" 고 거짓말을 한다.
            await self._drafts.clear(page.id, actor.user_id)
        return await self.to_view(page)

    async def _announce_change(
        self,
        page: Page,
        actor: Actor,
        *,
        body: str,
        version: PageVersion | None,
        message: str | None,
        first_time: bool,
    ) -> None:
        """바뀐 문서를 알린다. 초안은 알리지 않는다.

        판 번호를 싣는 이유: 알림에서 "몇 판이 됐다" 를 말할 수 있어야
        워처가 무엇을 볼지 정한다. 라벨만 바뀐 경우처럼 새 판이 없으면
        지금 판을 그대로 싣는다.
        """
        if page.status != "published":
            return
        space = await self._spaces.get(page.space_id)
        if space is None:
            return
        mentioned = await resolve_page_mentions(self._s, self._perms, page, body)
        if first_time:
            publish(
                self._s,
                wiki_events.PagePublished(
                    aggregate_id=page.id,
                    space_id=space.id,
                    space_key=space.key,
                    path=page.path,
                    title=page.title,
                    actor_id=actor.user_id,
                    mentioned_ids=mentioned,
                ),
            )
            return
        number = version.number if version is not None else 0
        if number == 0:
            current = await self._current_version(page)
            number = current.number if current is not None else 0
        publish(
            self._s,
            wiki_events.PageUpdated(
                aggregate_id=page.id,
                space_id=space.id,
                space_key=space.key,
                path=page.path,
                title=page.title,
                actor_id=actor.user_id,
                mentioned_ids=mentioned,
                version_number=number,
                message=(message or "").strip(),
            ),
        )

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
        await self._reindex(page)
        await self._relink(page)
        # 복원도 편집이다. 워처에게는 본문이 바뀐 것과 다르지 않다.
        await self._announce_change(
            page,
            actor,
            body=source.body,
            version=version,
            message=version.message,
            first_time=False,
        )
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
        # 경로가 바뀌면 후손의 경로도 바뀐다. 색인의 `ref` 가 옛 경로로
        # 남으면 검색 결과에서 눌러도 없는 문서로 간다.
        await self._reindex_subtree(page)
        log.info("wiki.page_moved", actor=str(actor.user_id), page=str(page.id), path=page.path)
        return await self.to_view(page)

    async def archive(self, actor: Actor, page_id: UUID) -> PageView:
        """휴지통으로. 후손도 함께 간다 — 부모 없는 문서를 남기지 않는다."""
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_DELETE, scope=Scope.space(page.space_id), subject=page
        )
        now = utcnow()
        subtree = await self._pages.subtree(page.space_id, page.path)
        for node in subtree:
            if node.archived_at is None:
                node.archived_at = now
        page.version += 1
        await self._s.flush()
        # 휴지통으로 간 문서는 검색에서 빠진다. 후손도 함께 갔으므로 함께.
        await search.remove_documents(self._s, kind=search.PAGE, entity_ids=[n.id for n in subtree])
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
        # 제한은 아래로 상속된다. 이 문서만 고치면 자식들이 계속 검색에 뜬다.
        await self._reindex_subtree(page)
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
                kind=_kind_of(parsed.front_matter),
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
        archive = read_archive(data)

        #: ZIP 안의 폴더 경로 → 만들어진 문서. 자식이 부모를 찾을 때 쓴다.
        created_by_dir: dict[str, UUID] = {}
        views: list[PageView] = []
        for entry in archive.entries:
            directory, _, _ = entry.path.rpartition("/")
            view = await self._import_entry(
                actor, space_id, entry, directory, created_by_dir, parent_id
            )
            await self._absorb_assets(actor, view, entry.path, archive.assets)
            views.append(view)
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
                kind=_kind_of(entry.document.front_matter),
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

    async def _absorb_assets(
        self, actor: Actor, view: PageView, document_path: str, assets: dict[str, bytes]
    ) -> None:
        """본문이 가리키는 묶음 안의 파일을 첨부로 올리고 주소를 바꾼다.

        안 하면 `![그림](images/a.png)` 이 통째로 깨진 링크가 된다 — 올린
        사람은 ZIP 에 그림을 같이 넣었는데도.

        **가리켜진 것만** 올린다. 묶음에 딸려 온 `.DS_Store` 까지 첨부가 되면
        안 된다. 못 찾은 경로는 그대로 둔다: 조용히 지우면 무엇이 있었는지도
        사라진다.
        """
        if self._store is None or not assets:
            return
        targets = asset_targets(view.body)
        if not targets:
            return

        attachments = AttachmentService(self._s, self._store)
        replacements: dict[str, str] = {}
        for target in targets:
            resolved = resolve_asset(document_path, target)
            if resolved is None:
                continue
            data = assets.get(resolved)
            if data is None:
                continue
            filename = posixpath.basename(resolved)
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            try:
                row = await attachments.ingest(
                    actor,
                    owner_type=OWNER_PAGE,
                    owner_id=view.page.id,
                    filename=filename,
                    mime=mime,
                    data=data,
                )
            except ValidationError:
                # 너무 크거나 막힌 형식이다. 문서를 통째로 거절하지 않는다 —
                # 글은 멀쩡한데 그림 하나 때문에 임포트가 실패하면 곤란하다.
                log.info("wiki.asset_skipped", page=str(view.page.id), path=resolved)
                continue
            replacements[target] = f"attachment:{row.id}/{row.filename}"

        if not replacements:
            return

        rewritten = rewrite_assets(view.body, replacements)
        if rewritten == view.body:
            return
        # 방금 만든 문서의 **첫 판**을 고친다. 새 판을 만들면 올리자마자
        # 이력이 두 줄이 되고, 첫 줄은 아무도 못 본 깨진 판이다.
        current = await self._current_version(view.page)
        if current is None:
            return
        current.body = normalize_markdown(rewritten)
        await self._s.flush()
        await self._reindex(view.page)
        await self._relink(view.page)

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
        """스페이스를 ZIP 으로. 문서 경로가 그대로 폴더 구조가 된다.

        첨부도 함께 담고 본문의 `attachment:` 를 상대 경로로 되돌린다. 안 하면
        내보낸 묶음이 이 서버에 묶인다 — 그림이 전부 깨진 채로. 다시 올리면
        임포트가 상대 경로를 도로 첨부로 흡수하므로(`_absorb_assets`) 왕복이
        닫힌다.
        """
        space = await self._require_space(space_id)
        await self._perms.require(self._s, actor, perms.PAGE_VIEW, scope=Scope.space(space.id))
        # 블로그 글도 담는다. 안 담으면 내보내기가 문서의 일부를 조용히 잃는다.
        rows = await self._visible(actor, await self._pages.tree_of(space.id, kind=None))

        files: list[tuple[str, str | bytes]] = []
        skipped: list[str] = []
        budget = MAX_EXPORT_ASSET_BYTES
        for page in rows:
            view = await self.to_view(page, space=space)
            body, budget = await self._pack_assets(actor, page, view.body, files, budget, skipped)
            files.append(
                (
                    f"{page.path}.md",
                    render_document(
                        title=page.title,
                        body=body,
                        labels=view.labels,
                        front_matter=_with_kind(
                            view.current.front_matter if view.current else {}, page.kind
                        ),
                    ),
                )
            )
        if skipped:
            # 조용히 빠지면 옮긴 쪽에서 영영 모른다. 무엇이 왜 빠졌는지 적는다.
            files.append((SKIPPED_MANIFEST, _skipped_manifest(skipped)))
        return write_archive(files)

    async def _pack_assets(
        self,
        actor: Actor,
        page: Page,
        body: str,
        files: list[tuple[str, str | bytes]],
        budget: int,
        skipped: list[str],
    ) -> tuple[str, int]:
        """문서의 첨부를 묶음에 담고, 본문 주소를 상대 경로로 바꾼다.

        `docs/guide.md` 의 첨부는 `docs/guide.assets/<id>/<파일명>` 에 둔다.
        문서와 같은 폴더라 상대 경로가 그대로 맞고, 하위 문서 폴더와도
        겹치지 않는다. id 를 한 겹 끼우는 것은 같은 이름의 파일 둘이 서로를
        덮어쓰지 않게 하기 위해서다.

        총량을 넘기거나 읽을 수 없는 첨부는 **본문을 그대로 둔다** — 주소만
        바꿔 놓고 파일을 안 담으면 깨진 링크가 되고, 무엇이 있었는지도 사라진다.
        """
        store = self._store
        if store is None:
            return body, budget
        refs = attachment_targets(body)
        if not refs:
            return body, budget

        attachments = AttachmentService(self._s, store)
        folder = asset_folder(page.path)
        relative_base = posixpath.basename(folder)
        replacements: dict[str, str] = {}
        # 같은 첨부를 두 가지 표기로 가리킬 수 있다(`attachment:<id>` 와
        # `attachment:<id>/a.png`). 한 번만 담는다 — 두 번 담으면 ZIP 에 같은
        # 이름이 두 개 들어가고 상한도 두 배로 깎인다.
        packed: dict[UUID, str] = {}
        for ref in refs:
            done = packed.get(ref.attachment_id)
            if done is not None:
                replacements[ref.target] = done
                continue
            found = await attachments.read(actor, ref.attachment_id)
            if found is None:
                # 지워졌거나 볼 수 없는 첨부다. 본문은 손대지 않는다.
                continue
            row, data = found
            if len(data) > budget:
                skipped.append(f"{page.path}.md\t{ref.target}\t{len(data)} bytes")
                continue
            budget -= len(data)
            # 파일명은 **행에서** 가져온다. 본문에 적힌 이름은 사람이 고칠 수
            # 있고, 거기에 `../` 가 섞이면 묶음이 폴더 밖으로 새어 나간다.
            inner = f"{row.id}/{row.filename}"
            files.append((f"{folder}/{inner}", data))
            packed[ref.attachment_id] = encode_target(f"{relative_base}/{inner}")
            replacements[ref.target] = packed[ref.attachment_id]

        return rewrite_assets(body, replacements), budget

    async def compare(
        self, actor: Actor, page_id: UUID, *, before: int, after: int
    ) -> tuple[PageVersion, PageVersion, DiffResult]:
        """두 판의 본문 차이.

        본문은 이미 정규화돼 있으므로 서식 흔들림이 diff 에 섞이지 않는다 —
        정규화가 없었으면 한 글자만 고쳐도 문서 절반이 바뀐 것으로 보였다.
        """
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_VIEW, scope=Scope.space(page.space_id), subject=page
        )
        old = await self._versions.by_number(page.id, before)
        new = await self._versions.by_number(page.id, after)
        if old is None or new is None:
            raise NotFoundError("그 판을 찾을 수 없다.")
        return old, new, diff_lines(old.body, new.body)

    # ── 초안(자동 저장) ─────────────────────────────────────────

    async def save_draft(
        self, actor: Actor, page_id: UUID, *, title: str, body: str, base_version: int | None
    ) -> PageDraft:
        """저장하지 않은 편집을 남긴다. 사람마다 문서마다 하나.

        판을 만들지 않는다. 30초마다 판이 하나씩 쌓이면 "무엇이 언제
        바뀌었나" 를 볼 수 없게 된다 — 이력이 오염되면 되돌리기도 못 쓴다.
        """
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_EDIT, scope=Scope.space(page.space_id), subject=page
        )
        self._validate_body(body)

        existing = await self._drafts.get(page.id, actor.user_id)
        if existing is not None:
            existing.title = title[:500]
            existing.body = body
            existing.base_version = base_version
            return existing
        return self._drafts.add(
            PageDraft(
                page_id=page.id,
                author_id=actor.user_id,
                title=title[:500],
                body=body,
                base_version=base_version,
            )
        )

    async def get_draft(self, actor: Actor, page_id: UUID) -> PageDraft | None:
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_VIEW, scope=Scope.space(page.space_id), subject=page
        )
        return await self._drafts.get(page.id, actor.user_id)

    async def discard_draft(self, actor: Actor, page_id: UUID) -> None:
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_VIEW, scope=Scope.space(page.space_id), subject=page
        )
        await self._drafts.clear(page.id, actor.user_id)

    # ── 복사 ────────────────────────────────────────────────────

    async def copy(
        self, actor: Actor, page_id: UUID, *, new_parent_id: UUID | None, title: str | None = None
    ) -> PageView:
        """문서를 가지째 복사한다.

        이력은 따라가지 않는다. 복사본은 새 문서고, 원본의 판 번호를 물려받으면
        "v7 로 되돌리기" 가 원본의 v7 을 뜻하는지 복사본의 v7 을 뜻하는지
        알 수 없어진다. 지금 본문 하나가 복사본의 v1 이다.
        """
        source = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_MOVE, scope=Scope.space(source.space_id), subject=source
        )
        await self._perms.require(
            self._s, actor, perms.PAGE_CREATE, scope=Scope.space(source.space_id)
        )

        parent = await self._resolve_parent(source.space_id, new_parent_id)
        if parent is not None and (
            parent.id == source.id or parent.path.startswith(f"{source.path}/")
        ):
            raise ValidationError(
                "자기 하위 문서 아래로 복사할 수 없다.", code="wiki.copy_into_descendant"
            )

        subtree = sorted(
            await self._pages.subtree(source.space_id, source.path), key=lambda p: p.path
        )
        #: 원본 경로 → 복사본. 자식이 부모를 찾을 때 쓴다.
        copies: dict[str, Page] = {}
        root: PageView | None = None
        for node in subtree:
            if node.is_archived:
                continue
            target_parent = (
                parent if node.id == source.id else copies.get(node.path.rsplit("/", 1)[0])
            )
            # 부모가 안 만들어졌으면(휴지통에 있었으면) 자식도 건너뛴다.
            if node.id != source.id and target_parent is None:
                continue
            view = await self._copy_one(
                actor,
                node,
                parent=target_parent,
                title=title if node.id == source.id else None,
            )
            copies[node.path] = view.page
            if node.id == source.id:
                root = view
        if root is None:
            raise NotFoundError("복사할 문서를 찾을 수 없다.")
        log.info(
            "wiki.page_copied",
            actor=str(actor.user_id),
            source=str(source.id),
            created=len(copies),
        )
        return root

    async def _copy_one(
        self, actor: Actor, node: Page, *, parent: Page | None, title: str | None
    ) -> PageView:
        version = await self._current_version(node)
        return await self.create(
            actor,
            NewPage(
                space_id=node.space_id,
                title=title or node.title,
                parent_id=parent.id if parent else None,
                body=version.body if version else "",
                front_matter=dict(version.front_matter) if version else {},
                labels=await self._labels.for_page(node.id),
                publish=node.status == "published",
            ),
        )

    # ── 이슈 링크 ───────────────────────────────────────────────

    async def _relink(self, page: Page) -> None:
        """본문의 `issue:KEY` 를 링크 표에 반영한다.

        문서 → 이슈 한 방향만 우리가 쓴다. 이슈 모듈은 위키를 모르기 때문에
        (의존 그래프가 `wiki ──▶ issues`), 반대 방향은 이 표를 거꾸로 읽어
        위키가 답한다 — "이 이슈를 언급한 문서" 는 위키 API 다.
        """
        version = await self._current_version(page)
        body = version.body if version and not page.is_archived else ""
        targets: list[tuple[str, UUID]] = []
        for ref in extract_links(body, schemes=(ISSUE_SCHEME,)):
            found = await issues.get_issue_by_key(self._s, ref.target.upper())
            # 없는 키는 그냥 지나간다. 오타 하나로 문서 저장이 막히면 안 된다.
            if found is not None and not found.is_archived:
                targets.append((org_links.ISSUE, found.id))
        await org_links.replace_links(
            self._s,
            from_type=org_links.PAGE,
            from_id=page.id,
            kind=org_links.MENTIONS,
            targets=targets,
        )

    async def pages_mentioning(self, actor: Actor, issue_id: UUID) -> list[Page]:
        """이 이슈를 언급한 문서. 볼 수 있는 것만.

        위키가 답한다. 이슈 모듈이 답하려면 위키를 알아야 하고, 그러면 의존
        그래프에 고리가 생긴다.
        """
        rows = await org_links.links_to(
            self._s, to_type=org_links.ISSUE, to_id=issue_id, from_type=org_links.PAGE
        )
        if not rows:
            return []
        pages = [
            p for p in (await self._pages.by_ids([r.from_id for r in rows])) if not p.is_archived
        ]

        visible: list[Page] = []
        for page in pages:
            scope = Scope.space(page.space_id)
            if await self._perms.has(self._s, actor, perms.PAGE_VIEW, scope=scope, subject=page):
                visible.append(page)
        return sorted(visible, key=lambda p: p.path)

    # ── 검색 색인 ───────────────────────────────────────────────

    async def _reindex(self, page: Page, *, space: Space | None = None) -> None:
        """문서 하나를 검색 색인에 반영한다. 원본과 **같은 트랜잭션**이다.

        제한이 걸린 가지의 문서는 볼 수 있는 주체를 함께 넣는다. 스코프만
        보면 검색이 제한을 우회하는 통로가 된다.
        """
        if page.is_archived or page.status != "published":
            # 초안은 아직 아무에게도 보일 것이 아니다.
            await search.remove_document(self._s, kind=search.PAGE, entity_id=page.id)
            return

        version = await self._current_version(page)
        space = space or await self._spaces.get(page.space_id)
        labels = await self._labels.for_page(page.id)
        body = to_plaintext(version.body) if version else ""
        if labels:
            body = f"{body}\n\n{' '.join(labels)}"

        await search.index_document(
            self._s,
            kind=search.PAGE,
            entity_id=page.id,
            scope_kind="space",
            scope_id=page.space_id,
            ref=f"{space.key}/{page.path}" if space else page.path,
            title=page.title,
            body=body,
            restricted_to=await self._effective_viewers(page),
            updated_at=page.updated_at,
        )

    async def _reindex_subtree(self, page: Page) -> None:
        """가지 전체를 다시 색인한다.

        경로가 바뀌거나 제한이 바뀌면 **후손까지** 달라진다. 제한은 아래로
        상속되므로, 조상 하나만 고치고 말면 자식들이 계속 검색에 뜬다.
        """
        for node in await self._pages.subtree(page.space_id, page.path):
            await self._reindex(node)

    async def _effective_viewers(self, page: Page) -> list[UUID] | None:
        """이 문서를 볼 수 있는 주체. 제한이 없으면 None.

        제한은 가장 가까운 조상에서 물려받는다 (`_visible` 과 같은 규칙).
        """
        # 자기 자신도 본다. `_ancestor_paths` 는 조상만 준다.
        paths = [*_ancestor_paths(page.path), page.path]
        candidates = await self._pages.by_paths(page.space_id, paths)
        by_path = {p.path: p for p in candidates}
        rules = await self._restrictions.for_pages([p.id for p in candidates])
        # 자기 자신부터 위로 올라가며 처음 만나는 view 제한을 쓴다.
        for path in reversed(paths):
            node = by_path.get(path)
            if node is None:
                continue
            view_rules = [r for r in rules.get(node.id, []) if r.mode == "view"]
            if view_rules:
                return [r.principal_id for r in view_rules]
        return None

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


@dataclass(frozen=True, slots=True)
class CommentView:
    """코멘트 하나와, 지금 문서에서 그 인용이 어디에 붙는지."""

    comment: PageComment
    #: 인라인 코멘트일 때만. 못 붙었으면 None(고아).
    match: AnchorMatch | None

    @property
    def is_orphaned(self) -> bool:
        return self.comment.anchor is not None and self.match is None


class PageCommentService:
    """문서 코멘트. `anchor` 가 있으면 인라인 (wiki-markdown.md 6절).

    앵커는 **평문**에서 잡는다. 사람은 렌더된 글을 드래그하지 `**굵게**` 같은
    원문을 고르지 않는다. 원문에 맞추면 굵게를 기울임으로 바꾸기만 해도
    멀쩡한 인용이 고아가 된다.

    고아를 지우지 않는다. 소리 없이 사라지는 코멘트는 아무도 믿지 않는다.
    """

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._comments = PageCommentRepository(session)
        self._pages = PageRepository(session)
        self._versions = PageVersionRepository(session)
        self._spaces = SpaceRepository(session)

    async def add(
        self,
        actor: Actor,
        page_id: UUID,
        *,
        body: str,
        anchor: dict[str, Any] | None = None,
        parent_id: UUID | None = None,
    ) -> CommentView:
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.COMMENT_ADD, scope=Scope.space(page.space_id), subject=page
        )
        text = self._require_text(body)

        parsed = Anchor.from_json(anchor) if anchor else None
        if parsed is not None and not parsed.exact.strip():
            raise ValidationError("인용할 텍스트가 비어 있다.", code="wiki.comment_anchor_empty")
        if parent_id is not None:
            await self._require_same_page_parent(parent_id, page_id)

        plain = await self._plaintext(page)
        match = locate(plain, parsed) if parsed is not None else None
        comment = self._comments.add(
            PageComment(
                page_id=page_id,
                author_id=actor.user_id,
                body=text,
                anchor=parsed.as_json() if parsed else None,
                # 달자마자 고아면 인용을 잘못 보낸 것이다. 그래도 거절하지
                # 않는다 — 쓴 글을 잃는 쪽이 더 나쁘다.
                anchor_status="ok" if parsed is None or match else "orphaned",
                parent_id=parent_id,
            )
        )
        await self._s.flush()

        space = await self._spaces.get(page.space_id)
        if space is not None:
            publish(
                self._s,
                wiki_events.PageCommented(
                    aggregate_id=page.id,
                    space_id=page.space_id,
                    space_key=space.key,
                    path=page.path,
                    title=page.title,
                    actor_id=actor.user_id,
                    # 코멘트 본문의 멘션이다. 문서 본문이 아니라 방금 쓴 글에서 찾는다.
                    mentioned_ids=await resolve_page_mentions(self._s, self._perms, page, text),
                    comment_id=comment.id,
                    inline=parsed is not None,
                ),
            )

        log.info(
            "wiki.comment_added",
            actor=str(actor.user_id),
            page=str(page_id),
            inline=parsed is not None,
            orphaned=comment.anchor_status == "orphaned",
        )
        return CommentView(comment=comment, match=match)

    async def list_for(self, actor: Actor, page_id: UUID) -> list[CommentView]:
        """코멘트를 모으면서 **읽을 때마다** 다시 앵커링한다.

        본문이 바뀔 때 한 번만 다시 붙이면, 되돌리기·복원으로 본문이 원래대로
        돌아와도 고아로 남는다. 다시 붙는 게 맞다.
        """
        page = await self._require_page(page_id)
        await self._perms.require(
            self._s, actor, perms.PAGE_VIEW, scope=Scope.space(page.space_id), subject=page
        )
        rows = await self._comments.for_page(page_id)
        plain = await self._plaintext(page)

        views: list[CommentView] = []
        for row in rows:
            match = locate(plain, Anchor.from_json(row.anchor)) if row.anchor else None
            status = "ok" if row.anchor is None or match else "orphaned"
            if row.anchor_status != status:
                row.anchor_status = status
            views.append(CommentView(comment=row, match=match))
        return views

    async def edit(self, actor: Actor, comment_id: UUID, body: str) -> CommentView:
        comment, page = await self._require_comment(comment_id)
        scope = Scope.space(page.space_id)
        if not await self._perms.has(
            self._s, actor, perms.COMMENT_EDIT_ANY, scope=scope, subject=page
        ):
            if comment.author_id != actor.user_id:
                raise PermissionDeniedError(
                    "타인의 코멘트를 수정할 권한이 없다.",
                    details={"permission": perms.COMMENT_EDIT_ANY},
                )
            await self._perms.require(
                self._s, actor, perms.COMMENT_EDIT_OWN, scope=scope, subject=page
            )
        comment.body = self._require_text(body)
        comment.edited_at = utcnow()
        plain = await self._plaintext(page)
        match = locate(plain, Anchor.from_json(comment.anchor)) if comment.anchor else None
        return CommentView(comment=comment, match=match)

    async def resolve(self, actor: Actor, comment_id: UUID, *, resolved: bool) -> CommentView:
        """해결 표시. 지우지 않는다 — 왜 그렇게 됐는지가 이력이다."""
        comment, page = await self._require_comment(comment_id)
        await self._perms.require(
            self._s, actor, perms.COMMENT_ADD, scope=Scope.space(page.space_id), subject=page
        )
        comment.resolved_at = utcnow() if resolved else None
        plain = await self._plaintext(page)
        match = locate(plain, Anchor.from_json(comment.anchor)) if comment.anchor else None
        return CommentView(comment=comment, match=match)

    async def delete(self, actor: Actor, comment_id: UUID) -> None:
        comment, page = await self._require_comment(comment_id)
        scope = Scope.space(page.space_id)
        if not await self._perms.has(
            self._s, actor, perms.COMMENT_EDIT_ANY, scope=scope, subject=page
        ):
            if comment.author_id != actor.user_id:
                raise PermissionDeniedError(
                    "타인의 코멘트를 지울 권한이 없다.",
                    details={"permission": perms.COMMENT_EDIT_ANY},
                )
            await self._perms.require(
                self._s, actor, perms.COMMENT_EDIT_OWN, scope=scope, subject=page
            )
        await self._s.delete(comment)

    async def _plaintext(self, page: Page) -> str:
        """앵커를 맞출 대상. 지금 보이는 판의 평문이다."""
        if page.current_version_id is None:
            latest = await self._versions.history(page.id, limit=1)
            body = latest[0].body if latest else ""
        else:
            version = await self._versions.get(page.current_version_id)
            body = version.body if version else ""
        # 코드에 단 코멘트가 항상 고아가 되면 코드 리뷰를 못 한다.
        return to_plaintext(body, include_code=True)

    async def _require_page(self, page_id: UUID) -> Page:
        page = await self._pages.get(page_id)
        if page is None or page.is_archived:
            raise NotFoundError("문서를 찾을 수 없다.")
        return page

    async def _require_comment(self, comment_id: UUID) -> tuple[PageComment, Page]:
        comment = await self._comments.get(comment_id)
        if comment is None:
            raise NotFoundError("코멘트를 찾을 수 없다.")
        return comment, await self._require_page(comment.page_id)

    async def _require_same_page_parent(self, parent_id: UUID, page_id: UUID) -> None:
        parent = await self._comments.get(parent_id)
        if parent is None or parent.page_id != page_id:
            raise ValidationError(
                "답글을 달 코멘트를 찾을 수 없다.", code="wiki.comment_parent_not_found"
            )
        if parent.parent_id is not None:
            # 한 단계까지만. 더 깊어지면 화면에서 읽을 수 없다.
            raise ValidationError(
                "답글에는 답글을 달 수 없다.", code="wiki.comment_nesting_too_deep"
            )

    @staticmethod
    def _require_text(body: str) -> str:
        text = normalize_markdown(body)
        if not text:
            raise ValidationError("내용을 비울 수 없다.", code="wiki.comment_empty")
        return text


class PageTemplateService:
    """문서 템플릿 (회의록·결정기록·요구사항).

    스페이스 전용이거나 전역이다. 전역은 스페이스 관리자가 아니라 전역
    관리자만 만든다 — 한 스페이스에서 만든 템플릿이 온 조직에 뜨면 곤란하다.
    """

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._templates = PageTemplateRepository(session)
        self._spaces = SpaceRepository(session)

    async def list_for(self, actor: Actor, space_id: UUID) -> list[PageTemplate]:
        space = await self._require_space(space_id)
        await self._perms.require(self._s, actor, perms.PAGE_VIEW, scope=Scope.space(space.id))
        return await self._templates.for_space(space.id)

    async def create(
        self,
        actor: Actor,
        *,
        space_id: UUID | None,
        name: str,
        body: str,
        category: str | None = None,
    ) -> PageTemplate:
        if space_id is None:
            # 전역 템플릿은 스페이스를 만들 수 있는 사람만. 스페이스 관리자가
            # 온 조직에 보이는 것을 만들 수 있으면 안 된다.
            await self._perms.require(self._s, actor, perms.SPACE_CREATE, scope=Scope.global_())
        else:
            space = await self._require_space(space_id)
            await self._perms.require(
                self._s, actor, perms.SPACE_ADMIN, scope=Scope.space(space.id)
            )

        cleaned = name.strip()
        if not cleaned:
            raise ValidationError("템플릿 이름이 필요하다.", code="wiki.template_name_required")
        return self._templates.add(
            PageTemplate(
                space_id=space_id,
                name=cleaned[:200],
                body=normalize_markdown(self._validate_body(body)),
                category=(category or "").strip()[:100] or None,
            )
        )

    async def delete(self, actor: Actor, template_id: UUID) -> None:
        template = await self._templates.get(template_id)
        if template is None:
            raise NotFoundError("템플릿을 찾을 수 없다.")
        if template.space_id is None:
            await self._perms.require(self._s, actor, perms.SPACE_CREATE, scope=Scope.global_())
        else:
            await self._perms.require(
                self._s, actor, perms.SPACE_ADMIN, scope=Scope.space(template.space_id)
            )
        await self._templates.delete(template)

    async def _require_space(self, space_id: UUID) -> Space:
        space = await self._spaces.get(space_id)
        if space is None or space.is_archived:
            raise NotFoundError("스페이스를 찾을 수 없다.")
        return space

    @staticmethod
    def _validate_body(body: str) -> str:
        if len(body) > MAX_BODY_LENGTH:
            raise ValidationError(
                "본문이 너무 길다.", code="wiki.body_too_long", details={"max": MAX_BODY_LENGTH}
            )
        return body


__all__ = [
    "CommentView",
    "NewPage",
    "PageCommentService",
    "PageRestrictionGuard",
    "PageService",
    "PageTemplateService",
    "PageView",
    "SpaceService",
]

"""저장소 등록과 링크 기록 (A22, M6).

## 웹훅이 이슈를 고르지 않는다

바깥에서 온 몸이 "이 이슈에 붙여라" 를 말하지 못하게 한다. 이슈는 **커밋
메시지의 키**로만 찾고, 그 키의 프로젝트가 이 저장소에 등록된 것이어야
한다(`Repository.project_ids`). 그러지 않으면 시크릿을 아는 쪽이 설치 전체의
이슈에 글을 붙일 수 있다.

## 못 찾은 키는 조용히 버린다

`ENG-9999` 가 없는 이슈일 수 있다(오타, 지운 이슈, 아직 안 만든 것). 그때
400 을 주면 코드 호스트는 재전송을 반복하고, 결국 사람이 웹훅을 끈다. 받고,
찾은 것만 붙이고, 몇 개를 붙였는지 돌려준다.

## 시크릿은 한 번만 보여 준다

등록 응답에만 담는다. 다시 못 읽는다 — 잃으면 새로 만든다. 읽을 수 있게
두면 그 화면을 볼 수 있는 사람 전부가 바깥에서 이슈에 글을 붙일 수 있다.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import get_settings
from ieum.core.context import Actor
from ieum.core.crypto import SecretBox
from ieum.core.exceptions import ConflictError, NotFoundError, ValidationError
from ieum.core.logging import get_logger
from ieum.core.permissions import PermissionService, Scope
from ieum.modules.issues import contracts as issues
from ieum.modules.org import contracts as org
from ieum.modules.vcs import permissions as perms
from ieum.modules.vcs.models import PROVIDERS, ChangeLink, Repository
from ieum.modules.vcs.refs import find_refs
from ieum.modules.vcs.webhooks import Change

log = get_logger(__name__)

#: 시크릿 암복호화 용도 이름. 용도를 나누면 한 키가 새도 다른 용도의 값은
#: 못 읽는다 (`core/crypto.py`).
SECRET_PURPOSE = "vcs.repository.secret"  # noqa: S105 - HKDF 용도 라벨이지 비밀번호가 아니다

MAX_NAME = 200
#: 저장소 하나에 붙일 수 있는 프로젝트 수. 모노레포가 팀 셋을 담는 정도까지.
MAX_PROJECTS = 20


@dataclass(frozen=True, slots=True)
class NewRepository:
    provider: str
    name: str
    project_ids: tuple[UUID, ...]
    url: str | None = None


@dataclass(frozen=True, slots=True)
class IssuedRepository:
    """등록 결과. **시크릿은 여기서만 나온다.**"""

    repository: Repository
    secret: str


@dataclass(frozen=True, slots=True)
class LinkedChange:
    """이슈에 붙은 변경 하나 + 어느 저장소인가."""

    link: ChangeLink
    repository_name: str
    provider: str


def secret_box() -> SecretBox:
    return SecretBox(get_settings().secret_key.get_secret_value(), purpose=SECRET_PURPOSE)


class RepositoryService:
    """저장소를 등록하고 지운다."""

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def list_for(self, actor: Actor, project_id: UUID) -> list[Repository]:
        """이 프로젝트에 붙은 저장소. **꺼진 것도 준다** — 왜 안 오는지 보려면
        목록에 있어야 한다."""
        await self._perms.require(self._s, actor, perms.REPO_VIEW, scope=Scope.project(project_id))
        rows = list(
            (await self._s.execute(select(Repository).order_by(Repository.name))).scalars().all()
        )
        # `project_ids` 는 JSONB 목록이라 인덱스를 걸어 좁히기 어렵다. 저장소
        # 수는 설치당 수십 개 규모이므로 파이썬에서 고른다 — 수천 개가 되는
        # 날 GIN 인덱스와 `@>` 로 바꾼다.
        return [row for row in rows if str(project_id) in row.project_ids]

    async def create(self, actor: Actor, payload: NewRepository) -> IssuedRepository:
        if payload.provider not in PROVIDERS:
            raise ValidationError(
                "그런 호스트는 없다.",
                code="vcs.unknown_provider",
                details={"value": payload.provider, "allowed": ", ".join(PROVIDERS)},
            )
        name = payload.name.strip()
        if not name:
            raise ValidationError("저장소 이름이 필요하다.", code="vcs.name_required")
        project_ids = list(dict.fromkeys(payload.project_ids))
        if not project_ids:
            raise ValidationError(
                "저장소가 어느 프로젝트의 것인지 적어야 한다.",
                code="vcs.projects_required",
            )
        if len(project_ids) > MAX_PROJECTS:
            raise ValidationError(
                f"프로젝트는 {MAX_PROJECTS}개까지 붙인다.", code="vcs.too_many_projects"
            )
        # **적은 프로젝트 전부에** 권한이 있어야 한다. 하나만 보면 권한 있는
        # 프로젝트 하나로 남의 프로젝트에 붙일 길이 열린다.
        found = await org.get_projects(self._s, project_ids)
        for project_id in project_ids:
            if project_id not in found:
                raise NotFoundError("프로젝트를 찾을 수 없다.")
            await self._perms.require(
                self._s, actor, perms.REPO_MANAGE, scope=Scope.project(project_id)
            )

        secret = secrets.token_urlsafe(32)
        row = Repository(
            provider=payload.provider,
            name=name[:MAX_NAME],
            url=(payload.url or "").strip() or None,
            secret_enc=secret_box().encrypt(secret),
            project_ids=[str(value) for value in project_ids],
            created_by=actor.user_id,
        )
        self._s.add(row)
        try:
            await self._s.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "그 저장소는 이미 등록돼 있다.", code="vcs.repository_exists"
            ) from exc
        log.info(
            "vcs.repository_created",
            actor=str(actor.user_id),
            repository=str(row.id),
            provider=row.provider,
        )
        return IssuedRepository(repository=row, secret=secret)

    async def set_enabled(self, actor: Actor, repository_id: UUID, *, enabled: bool) -> Repository:
        row = await self._require(actor, repository_id, perms.REPO_MANAGE)
        row.is_enabled = enabled
        await self._s.flush()
        return row

    async def delete(self, actor: Actor, repository_id: UUID) -> None:
        """지운다. **링크도 함께 사라진다**(FK CASCADE).

        남겨 둘 수도 있었지만, 저장소를 지운 뒤 남은 링크는 눌러도 404 인
        주소다 — 그건 기록이 아니라 막다른 길이다.
        """
        row = await self._require(actor, repository_id, perms.REPO_MANAGE)
        await self._s.delete(row)
        await self._s.flush()

    async def links_for(self, actor: Actor, issue_id: UUID) -> list[LinkedChange]:
        """이 이슈에 붙은 변경. 최신순.

        **이슈를 볼 수 있어야 본다.** 커밋 제목은 이슈의 내용만큼 민감할 수
        있어서(보안 이슈의 수정 커밋이 그렇다) 이슈와 같은 문을 쓴다.
        """
        ref = await issues.get_issue(self._s, issue_id)
        if ref is None:
            raise NotFoundError("이슈를 찾을 수 없다.")
        await self._perms.require(
            self._s,
            actor,
            "issue.view",
            scope=Scope.project(ref.project_id),
            subject=await self._issue_subject(issue_id),
        )
        rows = list(
            (
                await self._s.execute(
                    select(ChangeLink, Repository)
                    .join(Repository, Repository.id == ChangeLink.repository_id)
                    .where(ChangeLink.issue_id == issue_id)
                    .order_by(ChangeLink.happened_at.desc(), ChangeLink.external_ref)
                )
            ).all()
        )
        return [
            LinkedChange(link=link, repository_name=repo.name, provider=repo.provider)
            for link, repo in rows
        ]

    async def _issue_subject(self, issue_id: UUID) -> object | None:
        """객체 수준 관문(보안 레벨)이 볼 대상. 없으면 스코프만 본다."""
        model = issues.issue_model()
        return await self._s.get(model, issue_id)

    async def _require(self, actor: Actor, repository_id: UUID, permission: str) -> Repository:
        row = await self._s.get(Repository, repository_id)
        if row is None:
            raise NotFoundError("저장소를 찾을 수 없다.")
        # 적힌 프로젝트 **하나에라도** 권한이 있으면 다룰 수 있다. 전부를
        # 요구하면 모노레포를 등록한 뒤 아무도 못 지우는 상태가 생긴다.
        for raw in row.project_ids:
            try:
                scope = Scope.project(UUID(raw))
            except ValueError:  # pragma: no cover - 방어적
                continue
            if await self._perms.has(self._s, actor, permission, scope=scope):
                return row
        # 권한이 없으면 `require` 가 제대로 된 거절을 만들게 한다 (감사 로그와
        # step-up 안내가 거기 붙는다).
        first = UUID(row.project_ids[0]) if row.project_ids else None
        await self._perms.require(
            self._s,
            actor,
            permission,
            scope=Scope.project(first) if first else Scope.global_(),
        )
        return row


# ── 웹훅이 부르는 길 ────────────────────────────────────────────


async def record(session: AsyncSession, repository: Repository, changes: list[Change]) -> int:
    """변경들을 이슈에 붙인다. 붙인 링크 수를 돌려준다.

    **액터가 없다.** 코드 호스트는 우리 사용자가 아니다. 그래서 권한 대신
    저장소가 경계다: 키가 가리키는 프로젝트가 이 저장소에 등록돼 있어야 한다.

    `last_event_at` 은 **여기서 건드리지 않는다.** 그건 "들었다" 는 사실이고,
    링크가 하나도 안 생기는 전송(GitHub 의 `ping`)에도 남아야 한다. 그래서
    받는 자리가 적는다 (`webhook_router.py`).
    """
    keys = await _project_keys(session, repository)
    if not keys:
        return 0
    made = 0
    for change in changes:
        for ref in find_refs(change.text, known_projects=keys.values()):
            issue = await issues.get_issue_by_key(session, ref.key)
            if issue is None:
                # 없는 이슈다(오타·지운 이슈). 조용히 지나간다 — 400 을 주면
                # 코드 호스트가 재전송을 반복하고 사람이 웹훅을 끈다.
                continue
            if issue.project_id not in keys:
                # **저장소에 등록되지 않은 프로젝트다.** 키 목록으로 이미
                # 걸러지지만, 프로젝트 키가 바뀐 경우가 있어 한 번 더 본다.
                continue
            if await _link(session, repository, issue.id, change, closing=ref.closing):
                made += 1
    await session.flush()
    return made


async def _project_keys(session: AsyncSession, repository: Repository) -> dict[UUID, str]:
    """이 저장소가 가리킬 수 있는 프로젝트의 `{id: KEY}`."""
    ids: list[UUID] = []
    for raw in repository.project_ids:
        try:
            ids.append(UUID(raw))
        except ValueError:  # pragma: no cover - 방어적
            continue
    found = await org.get_projects(session, ids)
    return {project_id: ref.key for project_id, ref in found.items()}


async def _link(
    session: AsyncSession,
    repository: Repository,
    issue_id: UUID,
    change: Change,
    *,
    closing: bool,
) -> bool:
    """링크 하나. 이미 있으면 만들지 않고 `False`.

    **닫는다는 표시는 켜지기만 한다.** 같은 커밋이 재전송될 때 두 번째 몸에
    그 낱말이 없으면(누가 제목을 고쳤다) 표시가 꺼져 버리는데, 그건 사람이
    한 말을 우리가 지우는 것이다.
    """
    existing = (
        await session.execute(
            select(ChangeLink).where(
                ChangeLink.issue_id == issue_id,
                ChangeLink.repository_id == repository.id,
                ChangeLink.kind == change.kind,
                ChangeLink.external_ref == change.ref,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if closing and not existing.closing:
            existing.closing = True
        # 제목과 주소는 갱신한다 — PR 은 제목이 바뀌고, 그때 목록이 옛 제목을
        # 들고 있으면 눌러 보고서야 안다.
        existing.title = change.title[:500]
        existing.url = change.url[:1000]
        existing.happened_at = change.happened_at
        return False
    session.add(
        ChangeLink(
            repository_id=repository.id,
            issue_id=issue_id,
            kind=change.kind,
            external_ref=change.ref[:200],
            title=change.title[:500],
            url=change.url[:1000],
            author=(change.author or None),
            closing=closing,
            happened_at=change.happened_at,
        )
    )
    return True


__all__ = [
    "MAX_PROJECTS",
    "SECRET_PURPOSE",
    "IssuedRepository",
    "LinkedChange",
    "NewRepository",
    "RepositoryService",
    "record",
    "secret_box",
]

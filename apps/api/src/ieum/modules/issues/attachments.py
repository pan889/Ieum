"""이슈·코멘트 첨부의 권한 판정.

core 는 첨부 테이블만 소유하고 권한은 모른다. 소유자 종류마다 여기서
리졸버를 등록한다 (core/attachments.py 참고).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.attachments import register_owner
from ieum.core.context import Actor
from ieum.core.permissions import Scope, get_permission_service
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.models import Issue, IssueComment

OWNER_ISSUE = "issue"
OWNER_COMMENT = "comment"


class IssueAttachments:
    """이슈에 붙는 첨부. 이슈를 볼 수 있으면 첨부도 볼 수 있다."""

    async def _issue(self, session: AsyncSession, owner_id: UUID) -> Issue | None:
        return await session.get(Issue, owner_id)

    async def can_view(self, session: AsyncSession, actor: Actor, owner_id: UUID) -> bool:
        issue = await self._issue(session, owner_id)
        if issue is None:
            return False
        return await get_permission_service().has(
            session,
            actor,
            perms.ISSUE_VIEW,
            scope=Scope.project(issue.project_id),
            subject=issue,
        )

    async def can_attach(self, session: AsyncSession, actor: Actor, owner_id: UUID) -> bool:
        """파일을 붙이는 건 이슈를 고치는 것과 같은 무게다.

        보고자는 자기 이슈에 붙일 수 있다 — 첨부를 못 붙이면 버그 리포트에
        스크린샷을 못 넣는다.
        """
        issue = await self._issue(session, owner_id)
        if issue is None:
            return False
        service = get_permission_service()
        scope = Scope.project(issue.project_id)
        if await service.has(session, actor, perms.ISSUE_EDIT, scope=scope, subject=issue):
            return True
        return issue.reporter_id == actor.user_id and await service.has(
            session, actor, perms.ISSUE_EDIT_OWN, scope=scope, subject=issue
        )


class CommentAttachments:
    """코멘트에 붙는 첨부. 코멘트가 달린 이슈의 권한을 따른다."""

    async def _issue(self, session: AsyncSession, owner_id: UUID) -> Issue | None:
        comment = await session.get(IssueComment, owner_id)
        if comment is None:
            return None
        return await session.get(Issue, comment.issue_id)

    async def can_view(self, session: AsyncSession, actor: Actor, owner_id: UUID) -> bool:
        comment = await session.get(IssueComment, owner_id)
        if comment is None:
            return False
        issue = await session.get(Issue, comment.issue_id)
        if issue is None:
            return False
        service = get_permission_service()
        scope = Scope.project(issue.project_id)
        if not await service.has(session, actor, perms.ISSUE_VIEW, scope=scope, subject=issue):
            return False
        if not comment.is_internal:
            return True
        # 내부 노트의 첨부는 내부 노트를 볼 수 있는 사람만. 코멘트 본문은
        # 가리면서 첨부는 열어 두면 가린 의미가 없다.
        return await service.has(
            session, actor, perms.COMMENT_VIEW_INTERNAL, scope=scope, subject=issue
        )

    async def can_attach(self, session: AsyncSession, actor: Actor, owner_id: UUID) -> bool:
        comment = await session.get(IssueComment, owner_id)
        if comment is None:
            return False
        issue = await session.get(Issue, comment.issue_id)
        if issue is None:
            return False
        service = get_permission_service()
        scope = Scope.project(issue.project_id)
        if await service.has(session, actor, perms.COMMENT_EDIT_ANY, scope=scope, subject=issue):
            return True
        return comment.author_id == actor.user_id and await service.has(
            session, actor, perms.COMMENT_EDIT_OWN, scope=scope, subject=issue
        )


def install() -> None:
    """기동 시 한 번 부른다."""
    register_owner(OWNER_ISSUE, IssueAttachments())
    register_owner(OWNER_COMMENT, CommentAttachments())


__all__ = ["OWNER_COMMENT", "OWNER_ISSUE", "CommentAttachments", "IssueAttachments", "install"]

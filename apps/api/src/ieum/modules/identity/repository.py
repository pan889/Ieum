"""identity 데이터 접근. 쿼리만 한다 — 권한 판단·규칙 검증·이벤트 발행 금지."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import CursorResult, Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.pagination import Page, PageRequest
from ieum.core.time import utcnow
from ieum.modules.identity.models import (
    ApiToken,
    AuditLog,
    GroupMember,
    LoginAttempt,
    MFACredential,
    User,
    UserGroup,
    UserSession,
)


def normalize_email(email: str) -> str:
    """저장·조회 전 정규화. citext 가 없는 환경에서도 대소문자 무시가 성립한다."""
    return email.strip().lower()


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: UUID) -> User | None:
        return await self._s.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == normalize_email(email))
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        stmt = select(func.count()).select_from(User).where(User.email == normalize_email(email))
        return bool((await self._s.execute(stmt)).scalar_one())

    def add(self, user: User) -> User:
        self._s.add(user)
        return user

    async def list_page(
        self,
        request: PageRequest,
        *,
        query: str | None = None,
        ids: Sequence[UUID] | None = None,
        include_customers: bool = False,
    ) -> Page[User]:
        stmt: Select[tuple[User]] = select(User)
        if query:
            like = f"%{query.strip().lower()}%"
            stmt = stmt.where(func.lower(User.display_name).like(like) | User.email.like(like))
        if ids is not None:
            stmt = stmt.where(User.id.in_(ids))
        if not include_customers:
            # 담당자·멘션 피커는 내부 사용자만 고른다. 포털 고객이 섞이면
            # 실수로 고객을 담당자로 지정하게 된다.
            stmt = stmt.where(User.is_customer.is_(False))
        # 커서는 (created_at, id) 복합. 같은 시각에 만들어진 행도 안정적으로 넘긴다.
        payload = request.cursor_payload
        if payload:
            cursor_created = datetime.fromisoformat(payload["created_at"])
            cursor_id = UUID(payload["id"])
            stmt = stmt.where(
                (User.created_at, User.id) > (cursor_created, cursor_id)  # type: ignore[operator]
            )
        stmt = stmt.order_by(User.created_at, User.id).limit(request.fetch_limit)
        rows = list((await self._s.execute(stmt)).scalars().all())
        return Page.from_rows(
            rows, request, lambda u: {"created_at": u.created_at.isoformat(), "id": str(u.id)}
        )

    async def group_ids_for(self, user_id: UUID) -> frozenset[UUID]:
        stmt = select(GroupMember.group_id).where(GroupMember.user_id == user_id)
        return frozenset((await self._s.execute(stmt)).scalars().all())


class GroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, group_id: UUID) -> UserGroup | None:
        return await self._s.get(UserGroup, group_id)

    async def get_by_name(self, name: str) -> UserGroup | None:
        stmt = select(UserGroup).where(UserGroup.name == name)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, group: UserGroup) -> UserGroup:
        self._s.add(group)
        return group

    async def add_member(self, group_id: UUID, user_id: UUID) -> GroupMember | None:
        stmt = select(GroupMember).where(
            GroupMember.group_id == group_id, GroupMember.user_id == user_id
        )
        if (await self._s.execute(stmt)).scalar_one_or_none() is not None:
            return None
        member = GroupMember(group_id=group_id, user_id=user_id)
        self._s.add(member)
        return member


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, session_id: UUID) -> UserSession | None:
        return await self._s.get(UserSession, session_id)

    async def get_by_refresh_hash(self, token_hash: str) -> UserSession | None:
        stmt = select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get_by_access_hash(self, token_hash: str) -> UserSession | None:
        stmt = select(UserSession).where(UserSession.access_token_hash == token_hash)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, user_session: UserSession) -> UserSession:
        self._s.add(user_session)
        return user_session

    async def list_live_for_user(self, user_id: UUID) -> list[UserSession]:
        stmt = (
            select(UserSession)
            .where(UserSession.user_id == user_id)
            .where(UserSession.revoked_at.is_(None))
            .where(UserSession.expires_at > utcnow())
            .order_by(UserSession.created_at.desc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def revoke_family(self, family_id: UUID) -> int:
        """세션 계열 전체를 폐기한다. 리프레시 토큰 재사용 감지 시 호출된다."""
        stmt = (
            update(UserSession)
            .where(UserSession.family_id == family_id)
            .where(UserSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        result: CursorResult[Any] = await self._s.execute(stmt)  # type: ignore[assignment]
        return int(result.rowcount or 0)

    async def revoke_all_for_user(self, user_id: UUID) -> int:
        stmt = (
            update(UserSession)
            .where(UserSession.user_id == user_id)
            .where(UserSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        result: CursorResult[Any] = await self._s.execute(stmt)  # type: ignore[assignment]
        return int(result.rowcount or 0)


class MFARepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, credential_id: UUID) -> MFACredential | None:
        return await self._s.get(MFACredential, credential_id)

    def add(self, credential: MFACredential) -> MFACredential:
        self._s.add(credential)
        return credential

    async def confirmed_totp_for(self, user_id: UUID) -> MFACredential | None:
        stmt = (
            select(MFACredential)
            .where(MFACredential.user_id == user_id)
            .where(MFACredential.kind == "totp")
            .where(MFACredential.confirmed_at.is_not(None))
            .order_by(MFACredential.created_at.desc())
            .limit(1)
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def unused_backup_codes_for(self, user_id: UUID) -> list[MFACredential]:
        stmt = (
            select(MFACredential)
            .where(MFACredential.user_id == user_id)
            .where(MFACredential.kind == "backup_code")
            .where(MFACredential.used_at.is_(None))
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def delete_backup_codes(self, user_id: UUID) -> None:
        """재발급 시 전량 교체한다."""
        for row in await self.unused_backup_codes_for(user_id):
            await self._s.delete(row)

    async def has_any_confirmed(self, user_id: UUID) -> bool:
        stmt = (
            select(func.count())
            .select_from(MFACredential)
            .where(MFACredential.user_id == user_id)
            .where(MFACredential.kind != "backup_code")
            .where(MFACredential.confirmed_at.is_not(None))
        )
        return bool((await self._s.execute(stmt)).scalar_one())


class ApiTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_hash(self, token_hash: str) -> ApiToken | None:
        stmt = select(ApiToken).where(ApiToken.token_hash == token_hash)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, token: ApiToken) -> ApiToken:
        self._s.add(token)
        return token


class LoginAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def record(self, *, email: str, ip: str | None, succeeded: bool) -> LoginAttempt:
        attempt = LoginAttempt(email=normalize_email(email), ip=ip, succeeded=succeeded)
        self._s.add(attempt)
        return attempt

    async def recent_failures(self, *, email: str, ip: str | None, window_seconds: int) -> int:
        """슬라이딩 윈도우 안의 실패 횟수. 계정과 IP 중 큰 쪽을 쓴다."""
        since = utcnow() - timedelta(seconds=window_seconds)
        base = (
            select(func.count())
            .select_from(LoginAttempt)
            .where(LoginAttempt.created_at >= since)
            .where(LoginAttempt.succeeded.is_(False))
        )
        by_email = (
            await self._s.execute(base.where(LoginAttempt.email == normalize_email(email)))
        ).scalar_one()
        if ip is None:
            return int(by_email)
        by_ip = (await self._s.execute(base.where(LoginAttempt.ip == ip))).scalar_one()
        return int(max(by_email, by_ip))


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def record(
        self,
        *,
        action: str,
        actor_id: UUID | None = None,
        target_type: str | None = None,
        target_id: UUID | None = None,
        ip: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip=ip,
            audit_metadata=metadata or {},
        )
        self._s.add(entry)
        return entry

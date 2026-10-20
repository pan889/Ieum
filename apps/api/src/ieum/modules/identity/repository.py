"""identity 데이터 접근. 쿼리만 한다 — 권한 판단·규칙 검증·이벤트 발행 금지."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import CursorResult, Select, delete, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.pagination import Page, PageRequest, aware_datetime, cursor_field
from ieum.core.time import utcnow
from ieum.modules.identity.models import (
    ApiToken,
    AuditLog,
    GroupMember,
    IdentityProvider,
    LoginAttempt,
    MFACredential,
    SamlFlow,
    SamlSeenAssertion,
    User,
    UserGroup,
    UserIdentity,
    UserSession,
)

#: 기기 목록에 그릴 최대 개수. 사람이 실제로 쓰는 기기는 한 자릿수다.
MAX_LISTED_SESSIONS = 50


def normalize_email(email: str) -> str:
    """저장·조회 전 정규화. citext 가 없는 환경에서도 대소문자 무시가 성립한다."""
    return email.strip().lower()


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: UUID) -> User | None:
        return await self._s.get(User, user_id)

    async def get_many(self, user_ids: Sequence[UUID]) -> list[User]:
        if not user_ids:
            return []
        stmt = select(User).where(User.id.in_(list(user_ids)))
        return list((await self._s.execute(stmt)).scalars().all())

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
        #
        # **`tuple_` 이라야 한다.** 파이썬 튜플끼리 `>` 를 쓰면 SQL 이 아니라
        # 파이썬이 먼저 비교해 버려서 `created_at > x` 하나만 남는다 — 같은
        # 시각에 들어온 행(일괄 초대·SCIM 밀어넣기)이 페이지 경계에 걸리면
        # 다음 페이지에서 통째로 빠졌다.
        payload = request.cursor_payload
        if payload:
            cursor_created = cursor_field(payload, "created_at", aware_datetime)
            cursor_id = cursor_field(payload, "id", UUID)
            stmt = stmt.where(tuple_(User.created_at, User.id) > (cursor_created, cursor_id))
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

    async def remove_member(self, group_id: UUID, user_id: UUID) -> bool:
        """뺐으면 True. 원래 없었으면 False — 부르는 쪽이 감사 로그를 남길지
        정한다. 없던 것을 뺐다고 기록하면 로그가 사실이 아니게 된다."""
        result: CursorResult[Any] = await self._s.execute(  # type: ignore[assignment]
            delete(GroupMember)
            .where(GroupMember.group_id == group_id)
            .where(GroupMember.user_id == user_id)
        )
        return bool(result.rowcount)

    async def all_with_counts(self) -> list[tuple[UserGroup, int]]:
        """그룹과 인원수. 행마다 세면 목록 한 번에 N+1 이 된다.

        `outerjoin` 이라야 빈 그룹도 0 으로 나온다 — 안 나오면 방금 만든
        그룹이 목록에 없고, 사용자는 만들기가 실패한 줄로 읽는다.
        """
        stmt = (
            select(UserGroup, func.count(GroupMember.id))
            .outerjoin(GroupMember, GroupMember.group_id == UserGroup.id)
            .group_by(UserGroup.id)
            .order_by(UserGroup.name)
        )
        return [(group, count) for group, count in (await self._s.execute(stmt)).all()]

    async def members(self, group_id: UUID) -> list[User]:
        stmt = (
            select(User)
            .join(GroupMember, GroupMember.user_id == User.id)
            .where(GroupMember.group_id == group_id)
            .order_by(User.display_name, User.id)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def active_member_ids(self, group_ids: Sequence[UUID]) -> set[UUID]:
        """이 그룹들에 속한 **활성** 사용자 id. 여러 그룹을 한 번에 본다.

        정지된 계정을 빼는 이유: 부르는 쪽(승인 명단, C12)이 "전원 동의" 를
        셀 때 정지된 사람이 명단에 있으면 그 셈이 절대 완성되지 않는다.
        그건 기다리는 것이 아니라 멈춘 것이고, 화면은 둘을 구별해 주지 못한다.

        부르는 쪽도 사람을 한 번 더 읽어 거른다 — 그쪽은 **다른 이유**다(고객
        계정을 뺀다). 그래서 둘 다 있고, 이 필터는 계약으로 따로 붙잡는다
        (`test_desk_approvals.py`).
        """
        if not group_ids:
            return set()
        stmt = (
            select(GroupMember.user_id)
            .join(User, User.id == GroupMember.user_id)
            .where(GroupMember.group_id.in_(list(group_ids)), User.status == "active")
        )
        return set((await self._s.execute(stmt)).scalars().all())

    async def delete(self, group_id: UUID) -> None:
        """그룹을 지운다. 멤버 행은 FK 의 ON DELETE CASCADE 가 정리한다.

        역할 할당은 여기서 못 지운다 — org 의 테이블이다. 부르는 쪽이
        `org.contracts` 로 먼저 정리한다.
        """
        await self._s.execute(delete(UserGroup).where(UserGroup.id == group_id))


class IdentityProviderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, provider_id: UUID) -> IdentityProvider | None:
        return await self._s.get(IdentityProvider, provider_id)

    async def enabled(self) -> list[IdentityProvider]:
        """로그인 화면이 쓴다. 꺼진 것은 버튼도 뜨지 않는다."""
        stmt = (
            select(IdentityProvider)
            .where(IdentityProvider.is_enabled.is_(True))
            .order_by(IdentityProvider.name)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def all(self) -> list[IdentityProvider]:
        """관리 화면이 쓴다. **꺼진 것도 보여야 한다** — 안 보이면 다시 켤
        방법이 없고, 같은 발급자로 새로 등록하는 것도 유일 제약이 막는다."""
        stmt = select(IdentityProvider).order_by(IdentityProvider.name)
        return list((await self._s.execute(stmt)).scalars().all())

    async def find_saml(self, issuer: str) -> IdentityProvider | None:
        """같은 발급자로 등록된 SAML IdP. 부분 유일 인덱스와 짝이다."""
        stmt = select(IdentityProvider).where(
            IdentityProvider.issuer == issuer, IdentityProvider.kind == "saml"
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def find(self, issuer: str, client_id: str) -> IdentityProvider | None:
        """같은 (발급자, 클라이언트) 로 이미 등록된 것. 유일 제약과 짝이다 —
        먼저 물어보지 않으면 두 번째 등록이 500 으로 떨어진다."""
        stmt = select(IdentityProvider).where(
            IdentityProvider.issuer == issuer, IdentityProvider.client_id == client_id
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, provider: IdentityProvider) -> IdentityProvider:
        self._s.add(provider)
        return provider

    async def identity(self, provider_id: UUID, subject: str) -> UserIdentity | None:
        stmt = select(UserIdentity).where(
            UserIdentity.provider_id == provider_id, UserIdentity.subject == subject
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def link(self, provider_id: UUID, user_id: UUID, subject: str) -> UserIdentity:
        row = UserIdentity(provider_id=provider_id, user_id=user_id, subject=subject)
        self._s.add(row)
        return row

    async def idp_group_ids_for(self, user_id: UUID, provider_id: UUID) -> set[UUID]:
        """이 IdP 가 준 그룹만. 손으로 넣은 그룹은 동기화가 건드리면 안 된다.

        **다른 IdP 의 그룹도 건드리지 않는다.** 한 사람이 두 IdP 에 이어져
        있으면, 한쪽으로 로그인할 때마다 다른 쪽이 준 그룹에서 빠진다.

        주인이 비어 있는 옛 `idp` 그룹은 함께 준다 — 빼는 쪽으로만 쓰인다.
        빼지 않으면 IdP 가 더는 보내지 않는 옛 그룹의 권한이 그대로 남는다.
        """
        stmt = (
            select(GroupMember.group_id)
            .join(UserGroup, UserGroup.id == GroupMember.group_id)
            .where(GroupMember.user_id == user_id)
            .where(UserGroup.source == "idp")
            .where(
                or_(UserGroup.idp_provider_id == provider_id, UserGroup.idp_provider_id.is_(None))
            )
        )
        return set((await self._s.execute(stmt)).scalars().all())

    async def drop_members(self, user_id: UUID, group_ids: Sequence[UUID]) -> None:
        if not group_ids:
            return
        await self._s.execute(
            delete(GroupMember)
            .where(GroupMember.user_id == user_id)
            .where(GroupMember.group_id.in_(list(group_ids)))
        )


class SamlRepository:
    """SAML 흐름과 재생 방지 기록."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def start(self, *, provider_id: UUID, request_id: str | None, expires_at: datetime) -> SamlFlow:
        flow = SamlFlow(provider_id=provider_id, request_id=request_id, expires_at=expires_at)
        self._s.add(flow)
        return flow

    async def by_request_id(self, request_id: str) -> SamlFlow | None:
        stmt = select(SamlFlow).where(SamlFlow.request_id == request_id)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def by_handoff(self, handoff_hash: str) -> SamlFlow | None:
        stmt = select(SamlFlow).where(SamlFlow.handoff_hash == handoff_hash)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def remember_assertion(
        self, *, provider_id: UUID, assertion_id: str, expires_at: datetime
    ) -> bool:
        """처음 보는 어설션이면 기록하고 True. 이미 있으면 False (= 재생).

        `INSERT ... ON CONFLICT DO NOTHING` 으로 한 문장에 판단한다. 먼저
        SELECT 하고 나중에 INSERT 하면 두 요청이 같은 어설션을 동시에 들고
        와서 둘 다 통과한다 — 재생을 막겠다는 자리에서 경합을 남기는 셈이다.
        """
        stmt = (
            pg_insert(SamlSeenAssertion)
            .values(
                provider_id=provider_id,
                assertion_id=assertion_id,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(index_elements=[SamlSeenAssertion.assertion_id])
            .returning(SamlSeenAssertion.id)
        )
        return (await self._s.execute(stmt)).scalar_one_or_none() is not None

    async def forget_expired(self) -> int:
        """지난 것을 버린다. 어설션 자신의 유효 시간이 지나면 서명이 맞아도
        검증에서 걸리므로, 기록을 남겨 둘 이유가 없다."""
        now = utcnow()
        seen: CursorResult[Any] = await self._s.execute(  # type: ignore[assignment]
            delete(SamlSeenAssertion).where(SamlSeenAssertion.expires_at < now)
        )
        flows: CursorResult[Any] = await self._s.execute(  # type: ignore[assignment]
            delete(SamlFlow).where(SamlFlow.expires_at < now)
        )
        return int(seen.rowcount or 0) + int(flows.rowcount or 0)


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, session_id: UUID) -> UserSession | None:
        return await self._s.get(UserSession, session_id)

    async def get_by_refresh_hash(self, token_hash: str) -> UserSession | None:
        """리프레시 로테이션용. **행을 잠근다.**

        잠그지 않으면 같은 토큰을 든 요청 둘이 동시에 "아직 안 쓰인 토큰" 을
        읽고 둘 다 새 세션을 받는다 — 탈취한 쪽과 본인이 함께 살아남고 재사용
        탐지는 울리지 않는다. 잠그면 두 번째는 첫 번째가 커밋할 때까지 기다렸다가
        `rotated_to_id` 가 찬 행을 다시 읽고, 재사용으로 처리된다.

        `populate_existing` 은 같은 세션에 이미 올라와 있던 낡은 객체를 잠근
        뒤의 값으로 덮어쓰게 한다.
        """
        stmt = (
            select(UserSession)
            .where(UserSession.refresh_token_hash == token_hash)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get_by_access_hash(self, token_hash: str) -> UserSession | None:
        stmt = select(UserSession).where(UserSession.access_token_hash == token_hash)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, user_session: UserSession) -> UserSession:
        self._s.add(user_session)
        return user_session

    async def list_live_for_user(
        self, user_id: UUID, *, limit: int = MAX_LISTED_SESSIONS
    ) -> list[UserSession]:
        """살아 있는 세션, 최신순.

        상한을 둔다. 로그아웃하지 않고 브라우저를 닫기만 하면 세션이 계속
        쌓이고, 그대로 그리면 기기 목록이 수천 줄이 되어 정작 찾는 기기를
        고를 수 없다. 오래된 것을 통째로 치우는 길은 "모든 기기에서
        로그아웃" 이 이미 맡고 있다.
        """
        stmt = (
            select(UserSession)
            .where(UserSession.user_id == user_id)
            .where(UserSession.revoked_at.is_(None))
            .where(UserSession.expires_at > utcnow())
            .order_by(UserSession.created_at.desc())
            .limit(limit)
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

    async def list_for(self, user_id: UUID) -> list[MFACredential]:
        """화면에 보여 줄 자격증명. 백업 코드는 빼고 준다."""
        stmt = (
            select(MFACredential)
            .where(MFACredential.user_id == user_id)
            .where(MFACredential.kind != "backup_code")
            .order_by(MFACredential.created_at)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def confirmed_webauthn_for(self, user_id: UUID) -> list[MFACredential]:
        """확인된 인증기 전부. **여러 개를 쓰는 것이 정상이다** — 노트북과
        폰과 보안 키. 하나만 다루면 기기를 잃은 사람이 잠긴다."""
        stmt = (
            select(MFACredential)
            .where(MFACredential.user_id == user_id)
            .where(MFACredential.kind == "webauthn")
            .where(MFACredential.confirmed_at.is_not(None))
            .order_by(MFACredential.created_at)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def by_webauthn_credential_id(self, credential_id: str) -> MFACredential | None:
        """자격증명 ID 로 찾는다. **사용자를 묶지 않는다** — 그 ID 는 전역
        유일이고, 어느 계정에 붙어 있는지가 곧 답이다."""
        stmt = select(MFACredential).where(MFACredential.webauthn_credential_id == credential_id)
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

    async def revoke_all_for_user(self, user_id: UUID) -> int:
        """이 사람의 살아 있는 토큰을 전부 폐기한다. 폐기한 개수를 돌려준다."""
        stmt = (
            update(ApiToken)
            .where(ApiToken.user_id == user_id)
            .where(ApiToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        result: CursorResult[Any] = await self._s.execute(stmt)  # type: ignore[assignment]
        return int(result.rowcount or 0)


class LoginAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def record(self, *, email: str, ip: str | None, succeeded: bool) -> LoginAttempt:
        attempt = LoginAttempt(email=normalize_email(email), ip=ip, succeeded=succeeded)
        self._s.add(attempt)
        return attempt

    async def recent_failures(self, *, email: str, ip: str | None, window_seconds: int) -> int:
        """슬라이딩 윈도우 안의 실패 횟수. (계정, IP) 쌍과 IP 중 큰 쪽을 쓴다.

        **계정만으로 세지 않는다.** 그러면 이메일 주소 하나만 아는 사람이 어디서든
        열 번 틀려 주는 것으로 본인을 15분씩 계속 막아 둘 수 있다 — 제한이 곧
        남의 계정을 잠그는 스위치가 된다. 쌍으로 세면 남이 다른 곳에서 틀린
        횟수는 본인 몫에 들어오지 않는다. 한 곳에서 여러 계정을 찔러 보는 것은
        IP 기준이 막는다.

        IP 를 모르면(소켓 주소조차 없는 경우) "모르는 곳" 하나로 묶어 센다.
        """
        since = utcnow() - timedelta(seconds=window_seconds)
        base = (
            select(func.count())
            .select_from(LoginAttempt)
            .where(LoginAttempt.created_at >= since)
            .where(LoginAttempt.succeeded.is_(False))
        )
        from_here = LoginAttempt.ip.is_(None) if ip is None else LoginAttempt.ip == ip
        by_pair = (
            await self._s.execute(
                base.where(LoginAttempt.email == normalize_email(email)).where(from_here)
            )
        ).scalar_one()
        if ip is None:
            return int(by_pair)
        by_ip = (await self._s.execute(base.where(from_here))).scalar_one()
        return int(max(by_pair, by_ip))


@dataclass(frozen=True, slots=True)
class AuditFilter:
    """감사 로그 조회 조건. 전부 선택이고, 안 주면 전체다."""

    actor_id: UUID | None = None
    #: 정확히 일치하거나(`auth.login.failed`), 접두사로 묶거나(`auth.`).
    action: str | None = None
    target_type: str | None = None
    target_id: UUID | None = None
    #: 포함(>=). 기간을 안 주면 전체를 훑는다 — 화면이 기본값을 준다.
    since: datetime | None = None
    #: 미포함(<). 마지막 날을 통째로 담으려면 다음 날 0시를 준다.
    until: datetime | None = None


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def count_recent(self, *, action: str, actor_id: UUID, window_seconds: int) -> int:
        """이 사람이 창 안에서 남긴 그 행동의 수. 2FA 실패 제한이 쓴다.

        `ix_audit_log_actor_id_created_at` 를 그대로 탄다.
        """
        since = utcnow() - timedelta(seconds=window_seconds)
        stmt = (
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.actor_id == actor_id)
            .where(AuditLog.created_at >= since)
            .where(AuditLog.action == action)
        )
        return int((await self._s.execute(stmt)).scalar_one())

    async def list(self, filters: AuditFilter, request: PageRequest) -> Page[AuditLog]:
        """최신순. 감사 로그는 늘 "방금 무슨 일이 있었나" 부터 본다."""
        stmt: Select[tuple[AuditLog]] = select(AuditLog)
        if filters.actor_id is not None:
            stmt = stmt.where(AuditLog.actor_id == filters.actor_id)
        if filters.action:
            # `auth.` 처럼 점으로 끝나면 그 영역 전체다. 인덱스가 접두사
            # 검색을 그대로 받는다(`ix_audit_log_action_created_at`).
            stmt = (
                stmt.where(AuditLog.action.startswith(filters.action))
                if filters.action.endswith(".")
                else stmt.where(AuditLog.action == filters.action)
            )
        if filters.target_type:
            stmt = stmt.where(AuditLog.target_type == filters.target_type)
        if filters.target_id is not None:
            stmt = stmt.where(AuditLog.target_id == filters.target_id)
        if filters.since is not None:
            stmt = stmt.where(AuditLog.created_at >= filters.since)
        if filters.until is not None:
            stmt = stmt.where(AuditLog.created_at < filters.until)

        # 커서는 (created_at, id) 복합. 같은 시각에 쌓인 행도 안정적으로 넘긴다 —
        # 로그인 폭주 때 한 밀리초에 수십 행이 들어온다.
        # 파이썬 튜플 비교는 SQL 이 되지 않는다 — 사용자 목록의 주석 참조.
        payload = request.cursor_payload
        if payload:
            cursor_created = cursor_field(payload, "created_at", aware_datetime)
            cursor_id = cursor_field(payload, "id", UUID)
            stmt = stmt.where(
                tuple_(AuditLog.created_at, AuditLog.id) < (cursor_created, cursor_id)
            )
        stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(
            request.fetch_limit
        )
        rows = list((await self._s.execute(stmt)).scalars().all())
        return Page.from_rows(
            rows, request, lambda r: {"created_at": r.created_at.isoformat(), "id": str(r.id)}
        )

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

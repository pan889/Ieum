"""SCIM 프로비저닝의 행 쓰기 (RFC 7644).

`scim.py` 가 바깥 규약을 값으로 옮기고, 여기가 **DB 를 만진다.**

## 사람이 아닌 액터

이 표면에는 사용자 세션이 없다. 근거는 IdP 가 들고 오는 **프로비저닝
토큰**이고, 그 토큰은 IdP 설정 행에 달려 있다. 그래서 여기의 모든 메서드는
`Actor` 가 아니라 `IdentityProvider` 를 받는다 — 권한 검사를 부를 자리가
없다는 사실을 시그니처가 말한다.

## 자기가 밀어 넣은 것만 만진다

IdP 가 둘일 수 있다(협력사·자회사). 한쪽의 토큰으로 다른 쪽 계정을
비활성화할 수 있으면, 프로비저닝은 조용히 도는 일이라 며칠 뒤에나 발견된다.
`scim_provider_id` 가 그 자물쇠다.

**로컬 계정도 못 만진다.** 사람이 초대해 만든 계정(`scim_provider_id` 가
`NULL`)은 SCIM 의 소유가 아니다. IdP 의 디렉터리에서 사라졌다고 관리자
계정이 잠기면 안 된다.

## 비활성화는 삭제가 아니다

`active: false` 는 우리의 `suspended` 다. 행을 지우면 그 사람이 쓴 코멘트와
이슈의 작성자가 사라지고, 되살릴 방법도 없다. `DELETE` 도 같게 다룬다 —
Okta 는 앱에서 사람을 떼면 `DELETE` 를 보내는데, 그것이 "이 사람의 기록을
지워라" 를 뜻하지는 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.crypto import hash_token
from ieum.core.ids import new_token
from ieum.core.logging import get_logger
from ieum.core.time import utcnow
from ieum.modules.identity import scim
from ieum.modules.identity.models import GroupMember, IdentityProvider, User, UserGroup

log = get_logger(__name__)

SCIM_BASE = "/scim/v2"


@dataclass(frozen=True, slots=True)
class GroupView:
    group: UserGroup
    members: list[tuple[str, str]]


def issue_scim_token(provider: IdentityProvider) -> str:
    """새 프로비저닝 토큰. 행에는 해시만 남고 **평문은 여기서만 나온다.**

    PAT 과 같은 규약이다. 다시 보여 줄 방법이 없어야 새 것을 발급하는 것이
    유일한 복구 방법이 되고, 그러면 옛 토큰은 반드시 죽는다.
    """
    raw = new_token(32)
    provider.scim_token_hash = hash_token(raw)
    provider.scim_token_issued_at = utcnow()
    provider.scim_enabled = True
    return raw


async def provider_for_token(session: AsyncSession, raw: str) -> IdentityProvider | None:
    """토큰이 가리키는 IdP. 꺼져 있으면 없는 것과 같다."""
    if not raw:
        return None
    found = (
        await session.execute(
            select(IdentityProvider).where(
                IdentityProvider.scim_token_hash == hash_token(raw),
                IdentityProvider.scim_enabled.is_(True),
                IdentityProvider.is_enabled.is_(True),
            )
        )
    ).scalar_one_or_none()
    if found is not None:
        # 마지막으로 본 시각. 없으면 "프로비저닝이 돌고 있나" 를 확인할
        # 방법이 화면에 없다 — 메일 채널의 `last_polled_at` 과 같은 판단이다.
        found.scim_last_seen_at = utcnow()
    return found


class ScimUsers:
    """SCIM 의 `/Users`."""

    def __init__(self, session: AsyncSession, provider: IdentityProvider) -> None:
        self._s = session
        self._p = provider

    async def create(self, body: dict[str, Any]) -> User:
        user_name = _required_str(body, "userName")
        external_id = _optional_str(body, "externalId")
        row = await self._by_username(user_name)
        if row is not None:
            # SCIM 은 이걸 `409 uniqueness` 로 말하기를 요구한다. IdP 는 그
            # 답을 보고 "이미 있다" 로 처리하고 넘어간다 — 500 을 주면
            # 재시도 고리에 빠진다.
            raise scim.ScimError(409, "이미 있는 userName 이다", scim_type="uniqueness")

        row = User(
            email=user_name.lower(),
            display_name=_display_name(body, fallback=user_name),
            # **비밀번호가 없다.** IdP 가 인증을 갖는다.
            password_hash=None,
            # 밀어 넣은 계정은 곧바로 쓸 수 있다. `invited` 로 두면 초대
            # 메일을 기다리는데, SCIM 의 요점이 그 왕복을 없애는 것이다.
            status="active" if _active(body, default=True) else "suspended",
            scim_provider_id=self._p.id,
            scim_external_id=external_id,
        )
        self._s.add(row)
        await self._s.flush()
        log.info("scim.user.created", user=str(row.id), provider=str(self._p.id))
        return row

    async def get(self, user_id: UUID) -> User:
        return await self._owned(user_id)

    async def search(
        self, *, found_filter: scim.Filter | None, offset: int, limit: int
    ) -> tuple[list[User], int]:
        stmt = select(User).where(User.scim_provider_id == self._p.id)
        stmt = self._narrow(stmt, found_filter)
        total = (
            await self._s.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()
        rows = (
            (await self._s.execute(stmt.order_by(User.email).offset(offset).limit(limit)))
            .scalars()
            .all()
        )
        return list(rows), int(total)

    async def replace(self, user_id: UUID, body: dict[str, Any]) -> User:
        row = await self._owned(user_id)
        user_name = _required_str(body, "userName")
        clash = await self._by_username(user_name)
        if clash is not None and clash.id != row.id:
            raise scim.ScimError(409, "이미 있는 userName 이다", scim_type="uniqueness")
        row.email = user_name.lower()
        row.display_name = _display_name(body, fallback=user_name)
        external_id = _optional_str(body, "externalId")
        if external_id is not None:
            row.scim_external_id = external_id
        _set_active(row, _active(body, default=row.is_active))
        await self._s.flush()
        return row

    async def patch(self, user_id: UUID, ops: list[scim.PatchOp]) -> User:
        row = await self._owned(user_id)
        for op in ops:
            if op.op == "remove" and op.path == "active":
                # "active 를 지운다" 는 비활성화가 아니다. 짐작하지 않는다.
                raise scim.ScimError(400, "active 는 지울 수 없다", scim_type="invalidValue")
            if op.path == "active":
                _set_active(row, _truthy(op.value))
            elif op.path == "username":
                user_name = str(op.value or "").strip()
                if not user_name:
                    raise scim.ScimError(400, "userName 이 비었다", scim_type="invalidValue")
                clash = await self._by_username(user_name)
                if clash is not None and clash.id != row.id:
                    raise scim.ScimError(409, "이미 있는 userName 이다", scim_type="uniqueness")
                row.email = user_name.lower()
            elif op.path == "displayname":
                row.display_name = str(op.value or "").strip() or row.display_name
            else:
                # `name.givenName` 등. 우리는 표시 이름 하나만 갖는다 —
                # 조각을 받아 두고 안 쓰면 이름이 두 벌이 된다.
                continue
        await self._s.flush()
        return row

    async def deactivate(self, user_id: UUID) -> None:
        """`DELETE`. **행을 지우지 않는다** — 위 모듈 설명 참조."""
        row = await self._owned(user_id)
        _set_active(row, False)
        await self._s.flush()
        log.info("scim.user.deactivated", user=str(row.id), provider=str(self._p.id))

    # ── 내부 ────────────────────────────────────────────────────

    def _narrow(self, stmt: Select[tuple[User]], found: scim.Filter | None) -> Select[tuple[User]]:
        if found is None:
            return stmt
        if found.attribute == "username":
            return stmt.where(User.email == found.value.lower())
        return stmt.where(User.scim_external_id == found.value)

    async def _by_username(self, user_name: str) -> User | None:
        """**이 IdP 의 것만이 아니라 전체를 본다.**

        주소는 설치 전체에서 유일하다. 자기 것만 보면 사람이 초대해 만든
        계정과 같은 주소로 하나 더 만들려다 DB 의 unique 에서 터진다 — 그때는
        SCIM 봉투가 아니라 500 이 나간다.
        """
        return (
            await self._s.execute(select(User).where(User.email == user_name.lower()))
        ).scalar_one_or_none()

    async def _owned(self, user_id: UUID) -> User:
        row = await self._s.get(User, user_id)
        if row is None or row.scim_provider_id != self._p.id:
            # 남의 것과 없는 것을 **같게 답한다.** 다르게 답하면 토큰 하나로
            # "이 id 가 존재하는가" 를 물어볼 수 있다.
            raise scim.ScimError(404, "그런 사용자가 없다")
        return row


class ScimGroups:
    """SCIM 의 `/Groups`."""

    def __init__(self, session: AsyncSession, provider: IdentityProvider) -> None:
        self._s = session
        self._p = provider

    async def create(self, body: dict[str, Any]) -> GroupView:
        name = _required_str(body, "displayName")
        if await self._by_name(name) is not None:
            raise scim.ScimError(409, "이미 있는 displayName 이다", scim_type="uniqueness")
        row = UserGroup(
            name=name,
            source="scim",
            scim_provider_id=self._p.id,
            scim_external_id=_optional_str(body, "externalId"),
        )
        self._s.add(row)
        await self._s.flush()
        await self._set_members(row, scim._member_ids(body.get("members")))
        log.info("scim.group.created", group=str(row.id), provider=str(self._p.id))
        return await self.view(row)

    async def get(self, group_id: UUID) -> GroupView:
        return await self.view(await self._owned(group_id))

    async def search(
        self, *, found_filter: scim.Filter | None, offset: int, limit: int
    ) -> tuple[list[GroupView], int]:
        stmt = select(UserGroup).where(UserGroup.scim_provider_id == self._p.id)
        if found_filter is not None:
            stmt = stmt.where(UserGroup.name == found_filter.value)
        total = (
            await self._s.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()
        rows = (
            (await self._s.execute(stmt.order_by(UserGroup.name).offset(offset).limit(limit)))
            .scalars()
            .all()
        )
        return [await self.view(row) for row in rows], int(total)

    async def replace(self, group_id: UUID, body: dict[str, Any]) -> GroupView:
        row = await self._owned(group_id)
        name = _required_str(body, "displayName")
        clash = await self._by_name(name)
        if clash is not None and clash.id != row.id:
            raise scim.ScimError(409, "이미 있는 displayName 이다", scim_type="uniqueness")
        row.name = name
        # `PUT` 은 통째로 바꾸는 것이다. `members` 가 없으면 **비운다** —
        # 그게 스펙이고, 안 비우면 IdP 에서 뺀 사람이 계속 남는다.
        await self._set_members(row, scim._member_ids(body.get("members")))
        await self._s.flush()
        return await self.view(row)

    async def patch(self, group_id: UUID, ops: list[scim.PatchOp]) -> GroupView:
        row = await self._owned(group_id)
        for op in ops:
            if op.path == "displayname":
                name = str(op.value or "").strip()
                if not name:
                    raise scim.ScimError(400, "displayName 이 비었다", scim_type="invalidValue")
                clash = await self._by_name(name)
                if clash is not None and clash.id != row.id:
                    raise scim.ScimError(409, "이미 있는 displayName 이다", scim_type="uniqueness")
                row.name = name
                continue
            if op.op == "add":
                await self._add_members(row, op.member_ids)
            elif op.op == "remove":
                await self._remove_members(row, op.member_ids)
            else:
                await self._set_members(row, op.member_ids)
        await self._s.flush()
        return await self.view(row)

    async def delete(self, group_id: UUID) -> None:
        """그룹은 **지운다.** 사용자와 다르다.

        그룹에는 남길 기록이 없다(작성자도 이력도 아니다). 그리고 그룹에는
        역할이 붙으므로, 안 지우고 두면 IdP 에서 없앤 그룹이 계속 권한을
        준다 — 그게 SCIM 을 켜는 이유 중 하나다.
        """
        row = await self._owned(group_id)
        await self._s.delete(row)
        await self._s.flush()
        log.info("scim.group.deleted", group=str(group_id), provider=str(self._p.id))

    async def view(self, group: UserGroup) -> GroupView:
        rows = (
            (
                await self._s.execute(
                    select(User.id, User.display_name)
                    .join(GroupMember, GroupMember.user_id == User.id)
                    .where(GroupMember.group_id == group.id)
                    .order_by(User.display_name)
                )
            )
            .tuples()
            .all()
        )
        return GroupView(group=group, members=[(str(uid), name) for uid, name in rows])

    # ── 내부 ────────────────────────────────────────────────────

    async def _by_name(self, name: str) -> UserGroup | None:
        return (
            await self._s.execute(select(UserGroup).where(UserGroup.name == name))
        ).scalar_one_or_none()

    async def _owned(self, group_id: UUID) -> UserGroup:
        row = await self._s.get(UserGroup, group_id)
        if row is None or row.scim_provider_id != self._p.id:
            raise scim.ScimError(404, "그런 그룹이 없다")
        return row

    async def _members_of(self, group: UserGroup) -> dict[UUID, GroupMember]:
        rows = (
            (await self._s.execute(select(GroupMember).where(GroupMember.group_id == group.id)))
            .scalars()
            .all()
        )
        return {row.user_id: row for row in rows}

    async def _add_members(self, group: UserGroup, ids: list[str]) -> None:
        current = await self._members_of(group)
        for user_id in await self._resolve(ids):
            if user_id not in current:
                self._s.add(GroupMember(group_id=group.id, user_id=user_id))
        await self._s.flush()

    async def _remove_members(self, group: UserGroup, ids: list[str]) -> None:
        current = await self._members_of(group)
        for user_id in await self._resolve(ids):
            row = current.get(user_id)
            if row is not None:
                await self._s.delete(row)
        await self._s.flush()

    async def _set_members(self, group: UserGroup, ids: list[str]) -> None:
        wanted = set(await self._resolve(ids))
        current = await self._members_of(group)
        for user_id, row in current.items():
            if user_id not in wanted:
                await self._s.delete(row)
        for user_id in wanted:
            if user_id not in current:
                self._s.add(GroupMember(group_id=group.id, user_id=user_id))
        await self._s.flush()

    async def _resolve(self, ids: list[str]) -> list[UUID]:
        """멤버 id 를 우리 사용자로. **모르는 것은 거절한다.**

        조용히 건너뛰면 IdP 는 "넣었다" 로 알고, 그 사람은 그룹의 권한을 못
        받는다 — 어디에도 안 남는 실패다.
        """
        out: list[UUID] = []
        for raw in ids:
            try:
                user_id = UUID(raw)
            except ValueError as exc:
                raise scim.ScimError(
                    400, f"멤버 id 가 UUID 가 아니다: {raw}", scim_type="invalidValue"
                ) from exc
            row = await self._s.get(User, user_id)
            if row is None or row.scim_provider_id != self._p.id:
                raise scim.ScimError(
                    400, f"이 IdP 가 밀어 넣은 사용자가 아니다: {raw}", scim_type="invalidValue"
                )
            out.append(user_id)
        return out


# ── 값 다루기 ───────────────────────────────────────────────────


def _required_str(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise scim.ScimError(400, f"{key} 가 필요하다", scim_type="invalidValue")
    return value.strip()


def _optional_str(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise scim.ScimError(400, f"{key} 는 글자여야 한다", scim_type="invalidValue")
    return value.strip() or None


def _display_name(body: dict[str, Any], *, fallback: str) -> str:
    for value in (body.get("displayName"), _formatted(body.get("name"))):
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return fallback[:200]


def _formatted(name: Any) -> str | None:
    if not isinstance(name, dict):
        return None
    if isinstance(name.get("formatted"), str):
        return str(name["formatted"])
    parts = [name.get("givenName"), name.get("familyName")]
    joined = " ".join(str(p) for p in parts if isinstance(p, str) and p.strip())
    return joined or None


def _active(body: dict[str, Any], *, default: bool) -> bool:
    value = body.get("active")
    return default if value is None else _truthy(value)


def _truthy(value: Any) -> bool:
    """SCIM 은 불리언을 요구하지만 **글자로 보내는 IdP 가 있다.**

    `"False"` 를 파이썬 진리값으로 읽으면 비활성화가 활성화가 된다.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return bool(value)


def _set_active(row: User, active: bool) -> None:
    """`active` 를 우리 상태로. **삭제가 아니다.**

    IdP 가 미는 계정은 언제나 `active` 아니면 `suspended` 다 — 초대 흐름을
    거치지 않으므로 `invited` 가 될 일이 없다. 그래서 `pre_suspend_status`
    를 볼 것도 없이 바로 정한다. 다만 **비워는 둔다** — 관리 콘솔에서 정지해
    그 열이 채워진 계정을 IdP 가 되살릴 수 있고, 남겨 두면 다음 복구 때
    지나간 값을 읽는다.
    """
    row.status = "active" if active else "suspended"
    row.pre_suspend_status = "active" if not active else None


def stamp(value: datetime | None) -> str:
    """SCIM 의 시각 표기. `meta` 에 들어간다."""
    return (value or utcnow()).isoformat().replace("+00:00", "Z")

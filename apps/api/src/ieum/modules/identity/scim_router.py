"""SCIM 2.0 표면 (RFC 7644).

**`/api/v1` 아래가 아니다.** 경로도 오류 봉투도 우리가 정하지 않았다 — IdP 가
기대하는 모양이 있고, 다르게 주면 대개 "알 수 없는 실패" 로 접고 재시도한다.
그래서 이 라우터는 앱의 다른 규약을 따르지 않는 유일한 자리다:

- 경로: `/scim/v2/Users`, `/scim/v2/Groups` (스펙이 정한 대소문자 그대로).
- 인증: 프로비저닝 **토큰**. 사용자 세션이 아니고, 액터도 사람이 아니다.
- 오류: `{"schemas": ["...:Error"], "status": "409", "scimType": "uniqueness"}`.
- 응답 타입: `application/scim+json`.

## 트랜잭션 경계

다른 라우터와 같다 — 여기서 커밋한다. SCIM 요청 하나가 그룹 멤버 열 명을
바꾸는데, 절반만 반영되면 IdP 는 성공으로 알고 다시 안 보낸다.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.responses import JSONResponse

from ieum.core.deps import DbSession
from ieum.modules.identity import scim
from ieum.modules.identity.models import IdentityProvider, User
from ieum.modules.identity.scim_service import (
    SCIM_BASE,
    GroupView,
    ScimGroups,
    ScimUsers,
    provider_for_token,
    stamp,
)

scim_router = APIRouter(prefix=SCIM_BASE, tags=["scim"])

#: 스펙이 정한 매체 타입. 이걸 안 붙이면 까다로운 IdP 는 응답을 안 읽는다.
SCIM_JSON = "application/scim+json"


def _json(body: dict[str, Any], code: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=code, media_type=SCIM_JSON)


async def _provider(session: DbSession, authorization: str | None) -> IdentityProvider:
    """토큰이 가리키는 IdP. **이것이 이 표면의 인증 전부다.**

    실패를 SCIM 봉투로 말한다. 우리 401 봉투를 주면 IdP 는 인증 실패인지
    서버 오류인지 구별하지 못한다.
    """
    prefix = "bearer "
    raw = ""
    if authorization and authorization.lower().startswith(prefix):
        raw = authorization[len(prefix) :].strip()
    found = await provider_for_token(session, raw)
    if found is None:
        raise scim.ScimError(401, "프로비저닝 토큰이 유효하지 않다")
    return found


AuthHeader = Annotated[str | None, Header(alias="Authorization")]


async def _body(request: Request) -> dict[str, Any]:
    """요청 본문. **Pydantic 모델로 안 받는다.**

    스펙이 정한 필드 이름은 `userName`·`displayName` 처럼 camelCase 이고,
    IdP 는 우리가 모르는 확장 스키마를 함께 보낸다. 모델로 받으면 `extra`
    를 어떻게 할지 정해야 하는데, 거절하면 Entra 가 통째로 막히고 허용하면
    모델이 하는 일이 없다 — 필요한 것만 꺼내 쓰는 편이 정직하다.
    """
    try:
        found = await request.json()
    except Exception as exc:
        raise scim.ScimError(400, "본문이 JSON 이 아니다", scim_type="invalidSyntax") from exc
    if not isinstance(found, dict):
        raise scim.ScimError(400, "본문이 객체가 아니다", scim_type="invalidSyntax")
    return found


def _user_body(row: User) -> dict[str, Any]:
    return scim.user_resource(
        user_id=str(row.id),
        user_name=row.email,
        display_name=row.display_name,
        active=row.is_active,
        external_id=row.scim_external_id,
        created_at=stamp(row.created_at),
        updated_at=stamp(row.updated_at),
        location=f"{SCIM_BASE}/Users/{row.id}",
    )


def _group_body(view: GroupView) -> dict[str, Any]:
    return scim.group_resource(
        group_id=str(view.group.id),
        display_name=view.group.name,
        created_at=stamp(view.group.created_at),
        updated_at=stamp(view.group.updated_at),
        location=f"{SCIM_BASE}/Groups/{view.group.id}",
        members=view.members,
    )


def _uuid(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        # 없는 것으로 답한다. 400 을 주면 IdP 는 자기 요청이 틀렸다고 보고
        # 고치려 드는데, 고칠 것이 없다.
        raise scim.ScimError(404, "그런 자원이 없다") from exc


# ── 무엇을 지원하는가 ──────────────────────────────────────────


@scim_router.get("/ServiceProviderConfig")
async def service_provider_config() -> JSONResponse:
    return _json(scim.service_provider_config(f"{SCIM_BASE}/ServiceProviderConfig"))


# ── 사용자 ──────────────────────────────────────────────────────
#
# 정적 경로를 `/{user_id}` **위에** 둔다. 아래에 두면 `Me` 같은 이름이 id 로
# 잡힌다 — 형제가 여럿이면 "위" 는 첫째 위다.


@scim_router.get("/Users")
async def list_users(
    session: DbSession,
    authorization: AuthHeader = None,
    # `filter`·`startIndex` 는 **스펙이 정한 질의 이름**이다. 우리 규약으로
    # 바꾸면 IdP 가 부르는 주소와 안 맞는다.
    filter: str | None = None,
    startIndex: int | None = None,  # noqa: N803 — 스펙이 정한 이름이다
    count: int | None = None,
) -> JSONResponse:
    provider = await _provider(session, authorization)
    found = scim.parse_filter(filter, allowed=scim.USER_FILTERABLE)
    offset, limit = scim.page_of(startIndex, count)
    rows, total = await ScimUsers(session, provider).search(
        found_filter=found, offset=offset, limit=limit
    )
    await session.commit()
    return _json(
        scim.list_response([_user_body(row) for row in rows], total=total, start_index=offset + 1)
    )


@scim_router.post("/Users", status_code=status.HTTP_201_CREATED)
async def create_user(
    request: Request, session: DbSession, authorization: AuthHeader = None
) -> JSONResponse:
    provider = await _provider(session, authorization)
    row = await ScimUsers(session, provider).create(await _body(request))
    await session.commit()
    return _json(_user_body(row), status.HTTP_201_CREATED)


@scim_router.get("/Users/{user_id}")
async def read_user(
    user_id: str, session: DbSession, authorization: AuthHeader = None
) -> JSONResponse:
    provider = await _provider(session, authorization)
    row = await ScimUsers(session, provider).get(_uuid(user_id))
    await session.commit()
    return _json(_user_body(row))


@scim_router.put("/Users/{user_id}")
async def replace_user(
    user_id: str, request: Request, session: DbSession, authorization: AuthHeader = None
) -> JSONResponse:
    provider = await _provider(session, authorization)
    row = await ScimUsers(session, provider).replace(_uuid(user_id), await _body(request))
    await session.commit()
    return _json(_user_body(row))


@scim_router.patch("/Users/{user_id}")
async def patch_user(
    user_id: str, request: Request, session: DbSession, authorization: AuthHeader = None
) -> JSONResponse:
    provider = await _provider(session, authorization)
    ops = scim.parse_patch(await _body(request), allowed=scim.USER_PATHS)
    row = await ScimUsers(session, provider).patch(_uuid(user_id), ops)
    await session.commit()
    return _json(_user_body(row))


@scim_router.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str, session: DbSession, authorization: AuthHeader = None
) -> Response:
    provider = await _provider(session, authorization)
    await ScimUsers(session, provider).deactivate(_uuid(user_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── 그룹 ────────────────────────────────────────────────────────


@scim_router.get("/Groups")
async def list_groups(
    session: DbSession,
    authorization: AuthHeader = None,
    # `filter`·`startIndex` 는 **스펙이 정한 질의 이름**이다. 우리 규약으로
    # 바꾸면 IdP 가 부르는 주소와 안 맞는다.
    filter: str | None = None,
    startIndex: int | None = None,  # noqa: N803 — 스펙이 정한 이름이다
    count: int | None = None,
) -> JSONResponse:
    provider = await _provider(session, authorization)
    found = scim.parse_filter(filter, allowed=scim.GROUP_FILTERABLE)
    offset, limit = scim.page_of(startIndex, count)
    views, total = await ScimGroups(session, provider).search(
        found_filter=found, offset=offset, limit=limit
    )
    await session.commit()
    return _json(
        scim.list_response(
            [_group_body(view) for view in views], total=total, start_index=offset + 1
        )
    )


@scim_router.post("/Groups", status_code=status.HTTP_201_CREATED)
async def create_group(
    request: Request, session: DbSession, authorization: AuthHeader = None
) -> JSONResponse:
    provider = await _provider(session, authorization)
    view = await ScimGroups(session, provider).create(await _body(request))
    await session.commit()
    return _json(_group_body(view), status.HTTP_201_CREATED)


@scim_router.get("/Groups/{group_id}")
async def read_group(
    group_id: str, session: DbSession, authorization: AuthHeader = None
) -> JSONResponse:
    provider = await _provider(session, authorization)
    view = await ScimGroups(session, provider).get(_uuid(group_id))
    await session.commit()
    return _json(_group_body(view))


@scim_router.put("/Groups/{group_id}")
async def replace_group(
    group_id: str, request: Request, session: DbSession, authorization: AuthHeader = None
) -> JSONResponse:
    provider = await _provider(session, authorization)
    view = await ScimGroups(session, provider).replace(_uuid(group_id), await _body(request))
    await session.commit()
    return _json(_group_body(view))


@scim_router.patch("/Groups/{group_id}")
async def patch_group(
    group_id: str, request: Request, session: DbSession, authorization: AuthHeader = None
) -> JSONResponse:
    provider = await _provider(session, authorization)
    ops = scim.parse_patch(await _body(request), allowed=scim.GROUP_PATHS)
    view = await ScimGroups(session, provider).patch(_uuid(group_id), ops)
    await session.commit()
    return _json(_group_body(view))


@scim_router.delete("/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: str, session: DbSession, authorization: AuthHeader = None
) -> Response:
    provider = await _provider(session, authorization)
    await ScimGroups(session, provider).delete(_uuid(group_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

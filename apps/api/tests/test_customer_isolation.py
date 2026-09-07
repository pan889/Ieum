"""고객 계정 격리 (auth.md 5절).

`user.is_customer=true` 는 포털만 쓴다. 설계는 처음부터 "내부 API 는 권한
검사 **이전에** 일괄 차단" 이었지만, 실제로는 권한 서비스에서만 막고 있었다 —
그건 권한을 **보는** 라우트만 지킨다.

그래서 권한이 필요 없는 라우트가 열려 있었다. 실제로 새던 것은
`/roles/permissions` 다: 내부 권한 정의 47개를 통째로 내주고 있었고, 그
라우트는 PAT 화면이 스코프 후보를 그리려고 일부러 권한 없이 열어 둔 것이다.
목록 엔드포인트들은 ACL 이 비어서 빈 배열을 돌려주고 있었으므로 데이터가
샌 것은 아니지만, **다음에 추가되는 권한 없는 라우트마다 다시 뚫린다.**

여기서 고정하는 것은 두 방향이다:

1. 내부 표면은 닫혀 있다 — 라우트를 하나 더 만들어도 기본이 거절이다.
2. **자기 세션은 스스로 끝낼 수 있다.** 이 방향을 안 보면 막는 쪽만 조여져
   로그인은 되고 나갈 길이 없는 계정이 된다(실제로 처음 구현이 그랬다).
   조직 전체 2FA 강제가 켜진 설치에서는 등록 엔드포인트까지 막혀 통째로
   잠긴다 — 정책만 켜고 등록할 길을 안 이어 두는 것은 이미 한 번 겪은
   실수다(auth.md 3절).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ieum.config import Settings
from ieum.core.crypto import PasswordHashingService
from ieum.modules.identity.models import User

pytestmark = pytest.mark.integration

BASE = "/api/v1"
CUSTOMER_EMAIL = "portal-customer@example.com"
CUSTOMER_PASSWORD = "portal-customer-password-1234"

#: 고객이 닿으면 안 되는 내부 표면. 권한을 보는 것과 안 보는 것을 섞어 둔다 —
#: 후자가 이 결함의 자리였다.
INTERNAL_PATHS = (
    "/roles/permissions",
    "/notifications",
    "/users",
    "/projects",
    "/groups",
    "/audit",
    "/tokens",
    "/spaces",
    "/admin/sso/providers",
    "/admin/security",
    "/admin/workflows",
    "/admin/fields",
    # 데스크의 **관리** 표면. 고객이 쓰는 `/portal/...` 과 한 글자 차이라
    # 여기 없으면 접두사 검사가 어긋나도 아무도 모른다.
    "/portals",
    "/customer-organizations",
)


async def _make_customer(engine: object, settings: Settings) -> None:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]
    hasher = PasswordHashingService(
        memory_cost=settings.argon2_memory_cost,
        time_cost=settings.argon2_time_cost,
        parallelism=settings.argon2_parallelism,
    )
    session: AsyncSession
    async with factory() as session:
        session.add(
            User(
                email=CUSTOMER_EMAIL,
                display_name="Portal Customer",
                status="active",
                is_customer=True,
                password_hash=hasher.hash(CUSTOMER_PASSWORD),
            )
        )
        await session.commit()


async def _customer_headers(
    client: httpx.AsyncClient, engine: object, settings: Settings
) -> dict[str, str]:
    await _make_customer(engine, settings)
    signed_in = await client.post(
        f"{BASE}/auth/login", json={"email": CUSTOMER_EMAIL, "password": CUSTOMER_PASSWORD}
    )
    assert signed_in.status_code == 200, signed_in.text
    body: dict[str, Any] = signed_in.json()
    return {"Authorization": f"Bearer {body['access_token']}"}


class TestTheInternalSurfaceIsClosed:
    async def test_the_permission_catalogue_is_not_readable(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        """**실제로 새던 자리.** 권한 정의 47개가 고객에게 그대로 나갔다.

        이 라우트는 권한을 보지 않는다 — PAT 화면이 스코프 후보를 그려야
        해서 일부러 그렇게 열어 뒀다. 그래서 권한 서비스의 고객 거절이
        여기까지 닿지 않았다.
        """
        headers = await _customer_headers(app_client, engine, settings)
        r = await app_client.get(f"{BASE}/roles/permissions", headers=headers)
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "auth.permission_denied"

    @pytest.mark.parametrize("path", INTERNAL_PATHS)
    async def test_internal_paths_are_refused(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings, path: str
    ) -> None:
        headers = await _customer_headers(app_client, engine, settings)
        r = await app_client.get(f"{BASE}{path}", headers=headers)
        assert r.status_code == 403, f"{path} 가 {r.status_code} 로 열려 있다: {r.text[:200]}"

    async def test_a_customer_cannot_mint_an_api_token(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        """PAT 은 내부 API 를 부르는 열쇠다. 고객이 만들 수 있으면 격리가
        의미를 잃는다."""
        headers = await _customer_headers(app_client, engine, settings)
        r = await app_client.post(
            f"{BASE}/tokens",
            json={"name": "nope", "scopes": ["identity.user.view"]},
            headers=headers,
        )
        assert r.status_code == 403, r.text


class TestTheirOwnSessionStillWorks:
    """막는 쪽만 조이면 **로그인은 되고 나갈 길이 없는** 계정이 된다."""

    async def test_they_can_read_their_own_profile(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        headers = await _customer_headers(app_client, engine, settings)
        r = await app_client.get(f"{BASE}/auth/me", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["email"] == CUSTOMER_EMAIL
        assert r.json()["is_customer"] is True

    async def test_they_can_end_their_session(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        """`/auth/logout` 은 액터를 요구한다. 차단에 걸리면 나갈 길이 없다."""
        headers = await _customer_headers(app_client, engine, settings)
        out = await app_client.post(f"{BASE}/auth/logout", headers=headers)
        assert out.status_code == 204, out.text

        # 정말 끝났는지 본다. 204 만 보면 "받았다" 와 "끊었다" 를 구별 못 한다.
        after = await app_client.get(f"{BASE}/auth/me", headers=headers)
        assert after.status_code == 401, after.text

    async def test_they_can_reach_the_2fa_endpoints(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        """조직 전체 2FA 강제가 켜지면 고객도 등록을 요구받는다. 등록 경로가
        막혀 있으면 그 순간 통째로 잠긴다 — 정책만 켜고 등록할 길을 안 이어
        두는 것은 이미 한 번 겪은 실수다(auth.md 3절)."""
        headers = await _customer_headers(app_client, engine, settings)
        r = await app_client.get(f"{BASE}/auth/mfa/credentials", headers=headers)
        assert r.status_code == 200, r.text

    async def test_they_can_see_their_own_devices(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        """자기 세션 목록은 자기 것이다. 남의 것은 애초에 안 나온다."""
        headers = await _customer_headers(app_client, engine, settings)
        r = await app_client.get(f"{BASE}/auth/sessions", headers=headers)
        assert r.status_code == 200, r.text


class TestOneCharacterApart:
    """`/portal/` 은 열리고 `/portals` 는 닫힌다. 이 한 글자가 경계다.

    `startswith("/api/v1/portal/")` 이므로 `/api/v1/portals/...` 는 통과하지
    못한다 — 슬래시가 그 일을 한다. 접두사에서 슬래시를 빼는 순간 데스크
    **관리** API 가 고객에게 통째로 열린다. 그러니 그 사실 자체를 시험이
    붙잡고 있어야 한다.
    """

    async def test_the_admin_desk_surface_is_refused(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        headers = await _customer_headers(app_client, engine, settings)
        r = await app_client.get(f"{BASE}/portals?project_id={uuid4()}", headers=headers)
        assert r.status_code == 403, r.text

    async def test_the_customer_portal_surface_passes_through(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        """포털이 없으니 404 여야 한다 — **403 이 아니다.**

        403 이면 격리에 걸린 것이고, 그러면 고객은 자기 창구도 못 본다.
        """
        headers = await _customer_headers(app_client, engine, settings)
        r = await app_client.get(f"{BASE}/portal/no-such-portal", headers=headers)
        assert r.status_code == 404, r.text

    async def test_the_customer_portal_surface_needs_no_login(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """게스트 요청이 성립하려면 이 표면이 익명으로 열려 있어야 한다."""
        r = await app_client.get(f"{BASE}/portal/no-such-portal")
        assert r.status_code == 404, r.text


class TestInternalUsersAreUnaffected:
    """조이다가 직원까지 막으면 그건 고친 것이 아니다."""

    async def test_an_admin_still_reads_the_permission_catalogue(
        self, app_client: httpx.AsyncClient
    ) -> None:
        signed_in = await app_client.post(
            f"{BASE}/auth/login",
            json={"email": "admin@example.com", "password": "seed-admin-password-1234"},
        )
        assert signed_in.status_code == 200, signed_in.text
        headers = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}

        r = await app_client.get(f"{BASE}/roles/permissions", headers=headers)
        assert r.status_code == 200, r.text
        assert len(r.json()) > 20


def test_the_allow_list_stays_narrow() -> None:
    """허용 접두사가 늘어나는 것은 **의도한 결정**이어야 한다.

    이 목록이 곧 격리의 경계다. 하나 더 붙이는 순간 그 아래 모든 라우트가
    고객에게 열리므로, 조용히 늘어나지 않게 여기서 못 박는다.
    """
    from ieum.core.deps import CUSTOMER_PREFIXES

    assert CUSTOMER_PREFIXES == ("/api/v1/portal/", "/api/v1/auth/")

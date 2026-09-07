"""포털을 HTTP 로 몰아 본다 (feature-map C1).

**왜 서비스 시험만으로 부족한가**: 라우터가 커밋을 빠뜨려도 서비스 시험은
전부 통과한다. 픽스처가 트랜잭션을 들고 있어서 flush 만으로 다 보이기
때문이다. 실제로 그렇게 만들었고 `POST /portals` 는 201 을 주면서 테이블은
비어 있었다 — 개발 스택에 직접 HTTP 를 던져 보고서야 드러났다.

그래서 여기서는 **쓴 다음 다른 요청으로 읽는다.** `app_client` 는 요청마다
새 세션을 만들므로, 커밋하지 않은 쓰기는 두 번째 요청에서 사라진다.
그 사라짐이 곧 이 파일의 단언이다.

`test_router_commits.py` 는 같은 결함을 정적으로도 막는다. 둘 다 두는 이유:
정적 검사는 "커밋 호출이 있다" 만 보고, 이쪽은 **정말 남았는지**를 본다.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ieum.config import Settings
from ieum.core.crypto import PasswordHashingService
from ieum.modules.identity.models import User

pytestmark = pytest.mark.integration

BASE = "/api/v1"
ADMIN = ("admin@example.com", "seed-admin-password-1234")
CUSTOMER_EMAIL = "desk-customer@example.com"
CUSTOMER_PASSWORD = "desk-customer-password-1234"


async def _token(client: httpx.AsyncClient, email: str, password: str) -> str:
    r = await client.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return str(body["access_token"])


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
                display_name="Desk Customer",
                status="active",
                is_customer=True,
                password_hash=hasher.hash(CUSTOMER_PASSWORD),
            )
        )
        await session.commit()


async def _setup(client: httpx.AsyncClient, *, slug: str, is_public: bool) -> dict[str, Any]:
    """관리자로 포털과 요청 유형을 만든다. 시드 프로젝트를 쓴다."""
    admin = {"Authorization": f"Bearer {await _token(client, *ADMIN)}"}

    # 시드는 프로젝트를 만들지 않는다(설치가 정할 일이다). 여기서 만든다.
    project = await client.post(
        f"{BASE}/projects", json={"key": f"DK{slug[:2].upper()}", "name": "Desk"}, headers=admin
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    types = await client.get(f"{BASE}/issues/types?project_id={project_id}", headers=admin)
    assert types.status_code == 200, types.text
    issue_type_id = types.json()[0]["id"]

    portal = await client.post(
        f"{BASE}/portals",
        json={
            "project_id": project_id,
            "name": "고객 지원",
            "slug": slug,
            "is_public": is_public,
        },
        headers=admin,
    )
    assert portal.status_code == 201, portal.text

    request_type = await client.post(
        f"{BASE}/portals/{portal.json()['id']}/request-types",
        json={
            "issue_type_id": issue_type_id,
            "name": "장비가 안 됩니다",
            "form_schema": {
                "fields": [
                    {"key": "summary", "label": "무엇이 문제인가요", "required": True},
                    {"key": "description", "label": "자세히"},
                ]
            },
            "field_mapping": {},
        },
        headers=admin,
    )
    assert request_type.status_code == 201, request_type.text
    return {
        "admin": admin,
        "portal": portal.json(),
        "request_type": request_type.json(),
    }


class TestWritesActuallyPersist:
    """커밋을 빠뜨리면 여기서 죽는다. 앞의 응답이 아니라 **다음 요청**을 본다."""

    async def test_a_created_portal_is_there_on_the_next_request(
        self, app_client: httpx.AsyncClient
    ) -> None:
        setup = await _setup(app_client, slug="persist", is_public=False)
        again = await app_client.get(
            f"{BASE}/portals/{setup['portal']['id']}", headers=setup["admin"]
        )
        assert again.status_code == 200, again.text
        assert again.json()["slug"] == "persist"
        # 요청 유형도 남았는지 본다 — 포털만 보면 두 번째 커밋 누락을 놓친다.
        assert again.json()["request_type_count"] == 1

    async def test_an_archived_portal_stays_archived(self, app_client: httpx.AsyncClient) -> None:
        setup = await _setup(app_client, slug="stays", is_public=True)
        portal_id = setup["portal"]["id"]
        archived = await app_client.post(
            f"{BASE}/portals/{portal_id}/archive", headers=setup["admin"]
        )
        assert archived.status_code == 200, archived.text
        # 고객 쪽에서 사라졌는가. 여기가 진짜 확인이다 — 응답의 플래그가
        # 아니라 **다음 요청의 결과**를 본다.
        gone = await app_client.get(f"{BASE}/portal/stays")
        assert gone.status_code == 404, gone.text

        restored = await app_client.delete(
            f"{BASE}/portals/{portal_id}/archive", headers=setup["admin"]
        )
        assert restored.status_code == 200, restored.text
        back = await app_client.get(f"{BASE}/portal/stays")
        assert back.status_code == 200, back.text


class TestTheGuestPath:
    async def test_a_guest_files_a_request_without_signing_in(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """액터 의존성이 없어야 성립한다. 401 이 나오면 그 자리가 막힌 것이다."""
        setup = await _setup(app_client, slug="guests", is_public=True)

        info = await app_client.get(f"{BASE}/portal/guests")
        assert info.status_code == 200, info.text
        assert info.json()["allows_guests"] is True

        form = await app_client.get(
            f"{BASE}/portal/guests/request-types/{setup['request_type']['id']}"
        )
        assert form.status_code == 200, form.text
        # 종류는 서버가 정의에서 읽어 채운다. 폼 스키마에는 없다.
        assert [(f["key"], f["kind"]) for f in form.json()["fields"]] == [
            ("summary", "text"),
            ("description", "markdown"),
        ]

        filed = await app_client.post(
            f"{BASE}/portal/guests/guest-requests",
            json={
                "request_type_id": setup["request_type"]["id"],
                "email": "parent@school.example.com",
                "name": "학부모",
                "answers": {"summary": "프린터가 안 됩니다", "description": "3층 복도"},
            },
        )
        assert filed.status_code == 201, filed.text
        assert filed.json()["summary"] == "프린터가 안 됩니다"
        assert filed.json()["key"]

    async def test_a_private_portal_refuses_guests(self, app_client: httpx.AsyncClient) -> None:
        setup = await _setup(app_client, slug="closed", is_public=False)
        r = await app_client.post(
            f"{BASE}/portal/closed/guest-requests",
            json={
                "request_type_id": setup["request_type"]["id"],
                "email": "someone@example.com",
                "name": "누구",
                "answers": {"summary": "제목"},
            },
        )
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "desk.guest_not_allowed"

    async def test_an_unknown_answer_is_refused(self, app_client: httpx.AsyncClient) -> None:
        """조용히 버리면 고객은 적은 내용이 사라진 것을 모른다."""
        setup = await _setup(app_client, slug="strict", is_public=True)
        r = await app_client.post(
            f"{BASE}/portal/strict/guest-requests",
            json={
                "request_type_id": setup["request_type"]["id"],
                "email": "someone@example.com",
                "name": "누구",
                "answers": {"summary": "제목", "nope": "값"},
            },
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "desk.unknown_answer"


class TestTheCustomerPath:
    async def test_a_customer_submits_and_sees_it_in_their_list(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        """고객은 `issue.create` 를 갖지 않는다. 그래도 통과해야 한다.

        그리고 격리(`/api/v1/portal/`)를 통과해야 한다 — 접두사가 어긋나면
        403 이다.
        """
        setup = await _setup(app_client, slug="mine", is_public=False)
        await _make_customer(engine, settings)
        headers = {
            "Authorization": f"Bearer {await _token(app_client, CUSTOMER_EMAIL, CUSTOMER_PASSWORD)}"
        }

        filed = await app_client.post(
            f"{BASE}/portal/mine/requests",
            json={
                "request_type_id": setup["request_type"]["id"],
                "answers": {"summary": "계정이 잠겼습니다"},
            },
            headers=headers,
        )
        assert filed.status_code == 201, filed.text

        # **다음 요청**에서 보이는가. 커밋이 없으면 여기서 0건이다.
        listed = await app_client.get(f"{BASE}/portal/mine/requests", headers=headers)
        assert listed.status_code == 200, listed.text
        assert [row["summary"] for row in listed.json()["items"]] == ["계정이 잠겼습니다"]

        detail = await app_client.get(
            f"{BASE}/portal/mine/requests/{filed.json()['id']}", headers=headers
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["request_type_name"] == "장비가 안 됩니다"

    async def test_a_customer_cannot_reach_the_admin_desk_surface(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        """`/portal/` 과 `/portals` 는 한 글자 차이다 (auth.md 5절)."""
        setup = await _setup(app_client, slug="wall", is_public=False)
        await _make_customer(engine, settings)
        headers = {
            "Authorization": f"Bearer {await _token(app_client, CUSTOMER_EMAIL, CUSTOMER_PASSWORD)}"
        }
        blocked = await app_client.get(f"{BASE}/portals/{setup['portal']['id']}", headers=headers)
        assert blocked.status_code == 403, blocked.text

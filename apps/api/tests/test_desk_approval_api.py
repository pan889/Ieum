"""승인을 HTTP 로 몰아 본다 (feature-map C12).

서비스 시험이 판정을 이미 붙잡고 있다. 여기서 보는 것은 **배선**이고, 이
기능에서 배선이 끊기는 자리가 셋이다:

- **관문이 꽂혀 있는가.** `wiring.py` 에서 꽂지 않으면 승인은 요청되지만
  아무것도 막지 못한다 — 그건 승인이 아니라 표시다. 서비스 시험은 관문을
  손으로 꽂으므로 이 결함을 통과한다.
- **승인자가 요청을 볼 수 있는가.** 승인자는 그 프로젝트의 권한이 없을 수
  있다. `/approvals/mine` 이 권한을 요구하면 명단에 있는 사람이 자기 몫을
  못 본다.
- **정말 저장되는가.** 결정은 커밋돼야 한다. 라우터가 커밋을 빠뜨려도
  서비스 시험은 통과한다(픽스처가 트랜잭션을 들고 있다).
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
APPROVER_EMAIL = "approver@example.com"
APPROVER_PASSWORD = "approver-password-1234"
CUSTOMER_EMAIL = "approval-customer@example.com"
CUSTOMER_PASSWORD = "approval-customer-password-1234"


async def _token(client: httpx.AsyncClient, email: str, password: str) -> str:
    r = await client.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _auth(client: httpx.AsyncClient, email: str, password: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {await _token(client, email, password)}"}


async def _make_people(engine: object, settings: Settings) -> None:
    """승인자(직원)와 고객을 만든다. **승인자에게 역할을 주지 않는다** —
    명단이 근거라는 것을 이 시험이 보려면 권한이 없어야 한다."""
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
                email=APPROVER_EMAIL,
                display_name="결재자",
                status="active",
                password_hash=hasher.hash(APPROVER_PASSWORD),
            )
        )
        session.add(
            User(
                email=CUSTOMER_EMAIL,
                display_name="요청자",
                status="active",
                is_customer=True,
                password_hash=hasher.hash(CUSTOMER_PASSWORD),
            )
        )
        await session.commit()


async def _setup(client: httpx.AsyncClient, engine: object, settings: Settings) -> dict[str, Any]:
    """승인이 걸린 요청 유형 하나와 그것으로 들어온 티켓 하나."""
    await _make_people(engine, settings)
    admin = await _auth(client, *ADMIN)

    project = await client.post(
        f"{BASE}/projects", json={"key": "APV", "name": "Approvals"}, headers=admin
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    types = await client.get(f"{BASE}/issues/types?project_id={project_id}", headers=admin)
    issue_type_id = types.json()[0]["id"]

    people = await client.get(f"{BASE}/users?q=approver", headers=admin)
    assert people.status_code == 200, people.text
    approver_id = next(
        row["id"] for row in people.json()["items"] if row["email"] == APPROVER_EMAIL
    )

    portal = await client.post(
        f"{BASE}/portals",
        json={"project_id": project_id, "name": "Help", "slug": "apv", "is_public": False},
        headers=admin,
    )
    assert portal.status_code == 201, portal.text

    made = await client.post(
        f"{BASE}/portals/{portal.json()['id']}/request-types",
        json={
            "issue_type_id": issue_type_id,
            "name": "접근 요청",
            "form_schema": {"fields": [{"key": "summary", "label": "무엇이 필요합니까"}]},
            "field_mapping": {},
            "approval": {"mode": "one", "user_ids": [approver_id], "group_ids": []},
        },
        headers=admin,
    )
    assert made.status_code == 201, made.text
    assert made.json()["approval"]["mode"] == "one"

    submitted = await client.post(
        f"{BASE}/portal/apv/requests",
        json={
            "request_type_id": made.json()["id"],
            "answers": {"summary": "회계 시스템 계정이 필요합니다"},
        },
        headers=await _auth(client, CUSTOMER_EMAIL, CUSTOMER_PASSWORD),
    )
    assert submitted.status_code == 201, submitted.text
    return {
        "admin": admin,
        "approver_id": approver_id,
        "issue_id": submitted.json()["id"],
        "issue_key": submitted.json()["key"],
        "request_type_id": made.json()["id"],
        "portal_id": portal.json()["id"],
    }


async def _start_work(
    client: httpx.AsyncClient, headers: dict[str, str], issue_id: str
) -> httpx.Response:
    """착수 전이를 실행한다.

    상태 **이름**으로 고른다. 전이 응답에는 갈래(`category`)가 없고, 시드가
    만드는 기본 워크플로우의 이름은 `DEFAULT_WORKFLOW_STATES` 가 정한다.
    """
    available = await client.get(f"{BASE}/issues/{issue_id}/transitions", headers=headers)
    assert available.status_code == 200, available.text
    start = next(row for row in available.json() if row["to_state_name"] == "In Progress")
    return await client.post(
        f"{BASE}/issues/{issue_id}/transition",
        json={"transition_id": start["id"]},
        headers=headers,
    )


class TestTheWholeFlow:
    async def test_a_ticket_waits_for_its_approval_and_then_moves(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        client = app_client
        setup = await _setup(client, engine, settings)

        # 1. 상담원은 착수할 수 없다. **관문이 실제로 꽂혀 있는지**가 여기서
        #    드러난다 — `wiring.py` 에서 빠지면 이 줄이 200 이 된다.
        refused = await _start_work(client, setup["admin"], setup["issue_id"])
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "desk.approval_pending"

        # 2. 승인자는 **역할이 없는데도** 자기 몫을 본다. 근거가 권한이 아니라
        #    명단이기 때문이다.
        approver = await _auth(client, APPROVER_EMAIL, APPROVER_PASSWORD)
        mine = await client.get(f"{BASE}/approvals/mine", headers=approver)
        assert mine.status_code == 200, mine.text
        (waiting,) = mine.json()
        assert waiting["issue_key"] == setup["issue_key"]
        assert waiting["can_decide"] is True

        # 3. 승인한다.
        decided = await client.post(
            f"{BASE}/approvals/{waiting['id']}/decision",
            json={"decision": "approve"},
            headers=approver,
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "approved"

        # 4. **다른 요청으로** 읽는다. 라우터가 커밋하지 않았으면 여기서
        #    사라진다.
        again = await client.get(f"{BASE}/approvals/mine", headers=approver)
        assert again.json() == []

        # 5. 이제 문이 열린다.
        started = await _start_work(client, setup["admin"], setup["issue_id"])
        assert started.status_code == 200, started.text
        assert started.json()["state_category"] == "in_progress"

    async def test_the_history_shows_who_decided_and_why(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        client = app_client
        setup = await _setup(client, engine, settings)
        approver = await _auth(client, APPROVER_EMAIL, APPROVER_PASSWORD)
        (waiting,) = (await client.get(f"{BASE}/approvals/mine", headers=approver)).json()

        await client.post(
            f"{BASE}/approvals/{waiting['id']}/decision",
            json={"decision": "decline", "comment": "이 시스템은 팀장 승인이 더 필요합니다"},
            headers=approver,
        )
        history = await client.get(
            f"{BASE}/approvals?issue_id={setup['issue_id']}", headers=setup["admin"]
        )
        assert history.status_code == 200, history.text
        (row,) = history.json()
        assert row["status"] == "declined"
        assert row["votes"][0]["display_name"] == "결재자"
        assert "팀장 승인" in row["votes"][0]["comment"]
        assert row["can_decide"] is False

    async def test_a_declined_request_is_not_left_stuck(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        """거절이 문을 계속 잠그면 그 티켓은 영원히 접수 상태에 남는다.

        거절은 "이 요청은 안 된다" 이고, 그 뒤에 상담원이 해야 할 일(요청자에게
        설명하고 닫기)은 여전히 티켓을 움직이는 일이다.
        """
        client = app_client
        setup = await _setup(client, engine, settings)
        approver = await _auth(client, APPROVER_EMAIL, APPROVER_PASSWORD)
        (waiting,) = (await client.get(f"{BASE}/approvals/mine", headers=approver)).json()
        await client.post(
            f"{BASE}/approvals/{waiting['id']}/decision",
            json={"decision": "decline"},
            headers=approver,
        )
        moved = await _start_work(client, setup["admin"], setup["issue_id"])
        assert moved.status_code == 200, moved.text

    async def test_cancelling_frees_a_stuck_ticket(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        client = app_client
        setup = await _setup(client, engine, settings)
        history = await client.get(
            f"{BASE}/approvals?issue_id={setup['issue_id']}", headers=setup["admin"]
        )
        (row,) = history.json()
        cancelled = await client.post(
            f"{BASE}/approvals/{row['id']}/cancel", headers=setup["admin"]
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        started = await _start_work(client, setup["admin"], setup["issue_id"])
        assert started.status_code == 200, started.text


class TestRefusals:
    async def test_someone_who_is_not_an_approver_cannot_decide(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        """**관리자도 못 한다.** 대신 줄 수 있으면 명단이 뜻을 잃는다."""
        client = app_client
        setup = await _setup(client, engine, settings)
        history = await client.get(
            f"{BASE}/approvals?issue_id={setup['issue_id']}", headers=setup["admin"]
        )
        (row,) = history.json()
        refused = await client.post(
            f"{BASE}/approvals/{row['id']}/decision",
            json={"decision": "approve"},
            headers=setup["admin"],
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "desk.not_an_approver"

    async def test_a_rule_without_approvers_is_refused_at_the_edge(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        client = app_client
        setup = await _setup(client, engine, settings)
        refused = await client.patch(
            f"{BASE}/portals/request-types/{setup['request_type_id']}",
            json={"approval": {"mode": "one", "user_ids": [], "group_ids": []}},
            headers=setup["admin"],
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "desk.approvers_required"

    async def test_turning_the_rule_off_does_not_free_a_waiting_ticket(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        """설정 변경이 지난 요청의 문을 열면, 승인을 기다리던 티켓이 아무
        결정 없이 통과한다."""
        client = app_client
        setup = await _setup(client, engine, settings)
        off = await client.patch(
            f"{BASE}/portals/request-types/{setup['request_type_id']}",
            json={"clear_approval": True},
            headers=setup["admin"],
        )
        assert off.status_code == 200, off.text
        assert off.json()["approval"] is None

        refused = await _start_work(client, setup["admin"], setup["issue_id"])
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "desk.approval_pending"

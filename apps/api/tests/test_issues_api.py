"""issues·boards 통합 테스트. 라우터 배선과 경로 우선순위를 고정한다."""

from __future__ import annotations

import os
import secrets
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration

BASE = "/api/v1"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "seed-admin-password-1234"


@pytest.fixture(autouse=True, scope="session")
def _seed_env() -> None:
    os.environ.setdefault("SEED_ADMIN_EMAIL", ADMIN_EMAIL)
    os.environ.setdefault("SEED_ADMIN_PASSWORD", ADMIN_PASSWORD)


async def _auth(client: httpx.AsyncClient) -> dict[str, str]:
    r = await client.post(
        f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _project(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    r = await client.post(
        f"{BASE}/projects",
        json={"key": "T" + secrets.token_hex(3).upper(), "name": "API Test"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


class TestMetadataRoutes:
    """`/issues/types` 는 `/issues/{issue_id}` 보다 **먼저** 선언돼야 한다.

    뒤에 두면 FastAPI 가 "types" 를 issue_id 로 읽어 422 를 낸다. 이건 선언
    순서에만 달려 있어서 리팩터링 한 번에 조용히 깨진다.
    """

    async def test_types_not_shadowed_by_issue_id(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        project = await _project(app_client, headers)
        r = await app_client.get(
            f"{BASE}/issues/types", params={"project_id": project["id"]}, headers=headers
        )
        assert r.status_code == 200, r.text
        assert [t["name"] for t in r.json()], "시드 유형이 보여야 한다"

    async def test_states_not_shadowed_by_issue_id(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        project = await _project(app_client, headers)
        r = await app_client.get(
            f"{BASE}/issues/states", params={"project_id": project["id"]}, headers=headers
        )
        assert r.status_code == 200, r.text
        assert {s["category"] for s in r.json()} == {"todo", "in_progress", "done"}

    async def test_fields_not_shadowed_by_issue_id(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        project = await _project(app_client, headers)
        types = (
            await app_client.get(
                f"{BASE}/issues/types", params={"project_id": project["id"]}, headers=headers
            )
        ).json()
        r = await app_client.get(
            f"{BASE}/issues/fields",
            params={"project_id": project["id"], "type_id": types[0]["id"]},
            headers=headers,
        )
        assert r.status_code == 200, r.text


class TestBoardRoutes:
    async def test_board_lifecycle(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        project = await _project(app_client, headers)
        types = (
            await app_client.get(
                f"{BASE}/issues/types", params={"project_id": project["id"]}, headers=headers
            )
        ).json()

        issue = (
            await app_client.post(
                f"{BASE}/issues",
                json={
                    "project_id": project["id"],
                    "type_id": types[0]["id"],
                    "summary": "board card",
                },
                headers=headers,
            )
        ).json()

        created = await app_client.post(
            f"{BASE}/boards",
            json={
                "project_id": project["id"],
                "name": "Main",
                "columns": [
                    {"name": "To Do", "iql": "statusCategory = todo"},
                    {"name": "In Progress", "iql": "statusCategory = in_progress", "wip_limit": 1},
                ],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        board_id = created.json()["id"]

        content = await app_client.get(f"{BASE}/boards/{board_id}/content", headers=headers)
        assert content.status_code == 200, content.text
        columns = content.json()["columns"]
        assert [c["loaded"] for c in columns] == [1, 0]
        assert columns[0]["issues"][0]["key"] == issue["key"]

        transitions = (
            await app_client.get(f"{BASE}/issues/{issue['id']}/transitions", headers=headers)
        ).json()
        start = next(t for t in transitions if t["name"] == "Start progress")

        moved = await app_client.post(
            f"{BASE}/boards/{board_id}/move",
            json={"issue_id": issue["id"], "transition_id": start["id"]},
            headers={**headers, "If-Match": str(issue["version"])},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["state_category"] == "in_progress"

        after = (await app_client.get(f"{BASE}/boards/{board_id}/content", headers=headers)).json()[
            "columns"
        ]
        assert [c["loaded"] for c in after] == [0, 1]

    async def test_move_with_stale_version_conflicts(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        project = await _project(app_client, headers)
        types = (
            await app_client.get(
                f"{BASE}/issues/types", params={"project_id": project["id"]}, headers=headers
            )
        ).json()
        issue = (
            await app_client.post(
                f"{BASE}/issues",
                json={"project_id": project["id"], "type_id": types[0]["id"], "summary": "x"},
                headers=headers,
            )
        ).json()
        board_id = (
            await app_client.post(
                f"{BASE}/boards",
                json={
                    "project_id": project["id"],
                    "name": "Main",
                    "columns": [{"name": "All", "iql": ""}],
                },
                headers=headers,
            )
        ).json()["id"]
        transitions = (
            await app_client.get(f"{BASE}/issues/{issue['id']}/transitions", headers=headers)
        ).json()
        start = next(t for t in transitions if t["name"] == "Start progress")

        first = await app_client.post(
            f"{BASE}/boards/{board_id}/move",
            json={"issue_id": issue["id"], "transition_id": start["id"]},
            headers={**headers, "If-Match": str(issue["version"])},
        )
        assert first.status_code == 200, first.text

        stale = await app_client.post(
            f"{BASE}/boards/{board_id}/move",
            json={"issue_id": issue["id"], "transition_id": start["id"]},
            headers={**headers, "If-Match": str(issue["version"])},
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "common.version_conflict"

    async def test_broken_column_iql_rejected_at_save(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        project = await _project(app_client, headers)
        r = await app_client.post(
            f"{BASE}/boards",
            json={
                "project_id": project["id"],
                "name": "Broken",
                "columns": [{"name": "X", "iql": "status ==== y"}],
            },
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "iql.syntax_error"

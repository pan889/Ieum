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

    async def test_versions_not_shadowed_by_issue_id(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        project = await _project(app_client, headers)
        r = await app_client.get(
            f"{BASE}/issues/versions", params={"project_id": project["id"]}, headers=headers
        )
        assert r.status_code == 200, r.text
        # 새 프로젝트에는 버전이 없다. 빈 목록이어야 하고 422 여선 안 된다.
        assert r.json() == []


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


class TestUserDirectory:
    """담당자 피커가 쓰는 목록. `ids` 로 아는 id 만 이름으로 바꿀 수 있어야 한다."""

    async def test_lists_and_filters_by_ids(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        invited = await app_client.post(
            f"{BASE}/users/invite",
            json={"email": f"pick-{secrets.token_hex(4)}@example.com", "display_name": "Picker"},
            headers=headers,
        )
        assert invited.status_code == 201, invited.text
        target = invited.json()["id"]

        all_users = await app_client.get(f"{BASE}/users", headers=headers)
        assert all_users.status_code == 200, all_users.text
        assert target in {u["id"] for u in all_users.json()["items"]}

        only = await app_client.get(f"{BASE}/users", params={"ids": target}, headers=headers)
        assert only.status_code == 200, only.text
        assert [u["id"] for u in only.json()["items"]] == [target]

    async def test_search_by_name(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        name = f"Needle{secrets.token_hex(3)}"
        await app_client.post(
            f"{BASE}/users/invite",
            json={"email": f"n-{secrets.token_hex(4)}@example.com", "display_name": name},
            headers=headers,
        )
        r = await app_client.get(f"{BASE}/users", params={"q": name}, headers=headers)
        assert r.status_code == 200, r.text
        assert [u["display_name"] for u in r.json()["items"]] == [name]

    async def test_rejects_malformed_ids(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        r = await app_client.get(f"{BASE}/users", params={"ids": "not-a-uuid"}, headers=headers)
        assert r.status_code == 422, r.text


class TestListRowShape:
    """목록 응답은 화면이 필요한 걸 스스로 담고 있어야 한다.

    key 와 상태 이름을 클라이언트가 별도 목록으로 이어 붙이게 하면, 그
    목록에 없는 프로젝트의 이슈는 키도 상태도 못 그린다.
    """

    async def test_summary_carries_key_and_state(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        project = await _project(app_client, headers)
        types = (
            await app_client.get(
                f"{BASE}/issues/types", params={"project_id": project["id"]}, headers=headers
            )
        ).json()
        created = (
            await app_client.post(
                f"{BASE}/issues",
                json={"project_id": project["id"], "type_id": types[0]["id"], "summary": "row"},
                headers=headers,
            )
        ).json()

        listed = await app_client.get(
            f"{BASE}/issues", params={"project_id": project["id"]}, headers=headers
        )
        assert listed.status_code == 200, listed.text
        row = next(i for i in listed.json()["items"] if i["id"] == created["id"])
        assert row["key"] == created["key"]
        assert row["state_name"] == created["state_name"]
        assert row["state_category"] == "todo"

    async def test_search_rows_match_list_rows(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        project = await _project(app_client, headers)
        types = (
            await app_client.get(
                f"{BASE}/issues/types", params={"project_id": project["id"]}, headers=headers
            )
        ).json()
        await app_client.post(
            f"{BASE}/issues",
            json={"project_id": project["id"], "type_id": types[0]["id"], "summary": "searchable"},
            headers=headers,
        )

        listed = (
            await app_client.get(
                f"{BASE}/issues", params={"project_id": project["id"]}, headers=headers
            )
        ).json()["items"]
        found = (
            await app_client.post(
                f"{BASE}/search/issues",
                json={"iql": f'project = "{project["key"]}"'},
                headers=headers,
            )
        ).json()["items"]
        assert [i["key"] for i in found] == [i["key"] for i in listed]
        assert found[0].keys() == listed[0].keys()


class TestPatchShape:
    """PATCH 계약. 조용히 아무것도 안 하는 게 제일 나쁜 실패다."""

    async def _issue(self, client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
        project = await _project(client, headers)
        types = (
            await client.get(
                f"{BASE}/issues/types", params={"project_id": project["id"]}, headers=headers
            )
        ).json()
        return dict(
            (
                await client.post(
                    f"{BASE}/issues",
                    json={
                        "project_id": project["id"],
                        "type_id": types[0]["id"],
                        "summary": "before",
                    },
                    headers=headers,
                )
            ).json()
        )

    async def test_rejects_fields_outside_changes(self, app_client: httpx.AsyncClient) -> None:
        """`{"summary": "x"}` 를 200 으로 삼키면 클라이언트는 안 바뀐 걸 모른다."""
        headers = await _auth(app_client)
        issue = await self._issue(app_client, headers)
        r = await app_client.patch(
            f"{BASE}/issues/{issue['id']}", json={"summary": "sneaky"}, headers=headers
        )
        assert r.status_code == 422, r.text

    async def test_applies_changes(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        issue = await self._issue(app_client, headers)
        r = await app_client.patch(
            f"{BASE}/issues/{issue['id']}",
            json={"changes": {"summary": "after", "priority": 1}},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["summary"] == "after"
        assert r.json()["priority"] == 1

    async def test_null_clears_a_nullable_field(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        issue = await self._issue(app_client, headers)
        me = (await app_client.get(f"{BASE}/auth/me", headers=headers)).json()

        assigned = await app_client.patch(
            f"{BASE}/issues/{issue['id']}",
            json={"changes": {"assignee_id": me["id"]}},
            headers=headers,
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["assignee_id"] == me["id"]

        cleared = await app_client.patch(
            f"{BASE}/issues/{issue['id']}",
            json={"changes": {"assignee_id": None}},
            headers=headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["assignee_id"] is None

    async def test_uuid_arrives_as_a_string_and_is_coerced(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """JSON 에는 UUID 타입이 없다. str 을 그대로 넣으면 같은 값을 다시
        보내도 '바뀐 것' 으로 기록되고 version 이 계속 올라간다."""
        headers = await _auth(app_client)
        issue = await self._issue(app_client, headers)
        me = (await app_client.get(f"{BASE}/auth/me", headers=headers)).json()

        first = await app_client.patch(
            f"{BASE}/issues/{issue['id']}",
            json={"changes": {"assignee_id": me["id"]}},
            headers=headers,
        )
        again = await app_client.patch(
            f"{BASE}/issues/{issue['id']}",
            json={"changes": {"assignee_id": me["id"]}},
            headers=headers,
        )
        assert again.json()["version"] == first.json()["version"], "같은 값 재전송은 no-op"

    async def test_rejects_a_malformed_uuid(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        issue = await self._issue(app_client, headers)
        r = await app_client.patch(
            f"{BASE}/issues/{issue['id']}",
            json={"changes": {"assignee_id": "not-a-uuid"}},
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "issues.invalid_field_value"

    async def test_rejects_null_on_a_required_field(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        issue = await self._issue(app_client, headers)
        r = await app_client.patch(
            f"{BASE}/issues/{issue['id']}", json={"changes": {"summary": None}}, headers=headers
        )
        assert r.status_code == 422, r.text

    async def test_rejects_bool_as_priority(self, app_client: httpx.AsyncClient) -> None:
        """bool 은 int 의 서브클래스다. True 가 우선순위 1 이 되면 안 된다."""
        headers = await _auth(app_client)
        issue = await self._issue(app_client, headers)
        r = await app_client.patch(
            f"{BASE}/issues/{issue['id']}", json={"changes": {"priority": True}}, headers=headers
        )
        assert r.status_code == 422, r.text

    async def test_accepts_an_iso_date_string(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        issue = await self._issue(app_client, headers)
        r = await app_client.patch(
            f"{BASE}/issues/{issue['id']}",
            json={"changes": {"due_date": "2026-12-24"}},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["due_date"] == "2026-12-24"

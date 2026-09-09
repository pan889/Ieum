"""자산을 HTTP 로 몰아 본다 (feature-map C15).

서비스 시험이 규칙을 이미 붙잡고 있다. 여기서 보는 것은 배선이고, 이 기능에서
배선이 끊기는 자리가 셋이다:

- **고정 경로가 `{asset_id}` 형제보다 위에 있는가.** `/assets/types` 가 아래로
  내려가면 `types` 가 자산 id 로 잡혀 UUID 파싱 오류가 난다. 정적 검사로는
  안 잡히고, 라우트를 하나 더할 때마다 다시 생길 수 있다.
- **정말 저장되는가.** 라우터가 커밋을 빠뜨려도 서비스 시험은 통과한다
  (픽스처가 트랜잭션을 들고 있다).
- **목록이 커서를 주는가.** 화면은 그것으로만 다음 쪽에 간다.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration

BASE = "/api/v1"
ADMIN = ("admin@example.com", "seed-admin-password-1234")


async def _auth(client: httpx.AsyncClient) -> dict[str, str]:
    r = await client.post(f"{BASE}/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _asset_type(client: httpx.AsyncClient, headers: dict[str, str], name: str) -> str:
    made = await client.post(f"{BASE}/assets/types", json={"name": name}, headers=headers)
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


async def _asset(
    client: httpx.AsyncClient, headers: dict[str, str], type_id: str, **over: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {"type_id": type_id, "name": "본관 프로젝터"}
    body.update(over)
    made = await client.post(f"{BASE}/assets", json=body, headers=headers)
    assert made.status_code == 201, made.text
    return dict(made.json())


async def _ticket(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    project = await client.post(
        f"{BASE}/projects", json={"key": "CMD", "name": "CMDB"}, headers=headers
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    types = await client.get(f"{BASE}/issues/types?project_id={project_id}", headers=headers)
    made = await client.post(
        f"{BASE}/issues",
        json={
            "project_id": project_id,
            "type_id": types.json()[0]["id"],
            "summary": "프로젝터가 안 켜집니다",
        },
        headers=headers,
    )
    assert made.status_code == 201, made.text
    return dict(made.json())


class TestRegistry:
    async def test_the_fixed_path_wins_over_the_id_sibling(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """`/assets/types` 가 `/{asset_id}` 아래로 내려가면 여기서 422 가 난다."""
        headers = await _auth(app_client)
        listed = await app_client.get(f"{BASE}/assets/types", headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json() == []

    async def test_the_organization_picker_opens_without_step_up(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """**이 시험이 `/assets/organizations` 가 있는 이유다.**

        조직 관리 목록은 step-up 을 요구한다. 그 목록을 자산 화면에서 그대로
        쓰면 화면을 여는 것만으로 403 이 나고, 조직 칸은 죽은 칸이 된다 —
        브라우저에서 실제로 그랬다. 두 응답을 나란히 두어, 이 라우트를 지우고
        관리 목록으로 되돌리면 여기서 걸리게 한다.

        `/assets/organizations` 가 `/{asset_id}` 아래로 내려가도 여기서 잡힌다
        (`organizations` 를 자산 id 로 파싱하려 든다).
        """
        headers = await _auth(app_client)
        managed = await app_client.get(f"{BASE}/customer-organizations?limit=10", headers=headers)
        assert managed.status_code == 403, managed.text
        picker = await app_client.get(f"{BASE}/assets/organizations", headers=headers)
        assert picker.status_code == 200, picker.text
        assert picker.json() == {"items": [], "has_more": False}

    async def test_an_asset_survives_the_round_trip(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        type_id = await _asset_type(app_client, headers, "프로젝터")
        made = await _asset(app_client, headers, type_id, tag="pj-1", location="본관 3층")
        # 자산번호는 대문자로 저장된다.
        assert made["tag"] == "PJ-1"
        assert made["type_name"] == "프로젝터"

        # **다른 요청으로** 읽는다. 커밋하지 않았으면 여기서 사라진다.
        found = await app_client.get(f"{BASE}/assets/{made['id']}", headers=headers)
        assert found.status_code == 200, found.text
        assert found.json()["location"] == "본관 3층"

    async def test_the_list_hands_back_a_cursor(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        type_id = await _asset_type(app_client, headers, "노트북")
        for index in range(3):
            await _asset(app_client, headers, type_id, name=f"노트북 {index}")
        first = await app_client.get(f"{BASE}/assets?limit=2", headers=headers)
        assert first.status_code == 200, first.text
        body = first.json()
        assert len(body["items"]) == 2
        assert body["next_cursor"]
        second = await app_client.get(
            f"{BASE}/assets?limit=2&cursor={body['next_cursor']}", headers=headers
        )
        assert [row["name"] for row in second.json()["items"]] == ["노트북 2"]

    async def test_the_same_tag_twice_is_refused(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        type_id = await _asset_type(app_client, headers, "프로젝터")
        await _asset(app_client, headers, type_id, tag="A-1")
        refused = await app_client.post(
            f"{BASE}/assets",
            json={"type_id": type_id, "name": "둘", "tag": "a-1"},
            headers=headers,
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "desk.asset_tag_taken"

    async def test_an_unreadable_tag_is_refused_at_the_edge(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = await _auth(app_client)
        type_id = await _asset_type(app_client, headers, "프로젝터")
        refused = await app_client.post(
            f"{BASE}/assets",
            json={"type_id": type_id, "name": "공백", "tag": "A 1"},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "desk.invalid_asset_tag"


class TestTicketLinks:
    async def test_linking_and_unlinking_a_ticket(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        type_id = await _asset_type(app_client, headers, "프로젝터")
        asset = await _asset(app_client, headers, type_id, tag="PJ-7")
        issue = await _ticket(app_client, headers)

        linked = await app_client.post(
            f"{BASE}/assets/linked?issue_id={issue['id']}",
            json={"asset_id": asset["id"]},
            headers=headers,
        )
        assert linked.status_code == 201, linked.text
        assert linked.json()["tag"] == "PJ-7"

        listed = await app_client.get(
            f"{BASE}/assets/linked?issue_id={issue['id']}", headers=headers
        )
        assert [row["id"] for row in listed.json()] == [asset["id"]]

        # 자산 쪽에서도 보인다 — 자꾸 고장나는 장비를 읽는 자리다.
        tickets = await app_client.get(f"{BASE}/assets/{asset['id']}/tickets", headers=headers)
        assert [row["key"] for row in tickets.json()] == [issue["key"]]

        gone = await app_client.delete(
            f"{BASE}/assets/linked?issue_id={issue['id']}&asset_id={asset['id']}",
            headers=headers,
        )
        assert gone.status_code == 204, gone.text
        after = await app_client.get(
            f"{BASE}/assets/linked?issue_id={issue['id']}", headers=headers
        )
        assert after.json() == []

    async def test_a_linked_asset_cannot_be_deleted(self, app_client: httpx.AsyncClient) -> None:
        """이력을 지우는 길은 없다. 장비가 나간 것이라면 '사용 종료' 다."""
        headers = await _auth(app_client)
        type_id = await _asset_type(app_client, headers, "프로젝터")
        asset = await _asset(app_client, headers, type_id)
        issue = await _ticket(app_client, headers)
        await app_client.post(
            f"{BASE}/assets/linked?issue_id={issue['id']}",
            json={"asset_id": asset["id"]},
            headers=headers,
        )
        refused = await app_client.delete(f"{BASE}/assets/{asset['id']}", headers=headers)
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "desk.asset_in_use"

        retired = await app_client.patch(
            f"{BASE}/assets/{asset['id']}", json={"status": "retired"}, headers=headers
        )
        assert retired.status_code == 200, retired.text
        assert retired.json()["status"] == "retired"
        # 이력은 남는다.
        tickets = await app_client.get(f"{BASE}/assets/{asset['id']}/tickets", headers=headers)
        assert len(tickets.json()) == 1

    async def test_the_same_link_twice_is_refused(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        type_id = await _asset_type(app_client, headers, "프로젝터")
        asset = await _asset(app_client, headers, type_id)
        issue = await _ticket(app_client, headers)
        for _ in range(1):
            first = await app_client.post(
                f"{BASE}/assets/linked?issue_id={issue['id']}",
                json={"asset_id": asset["id"]},
                headers=headers,
            )
            assert first.status_code == 201, first.text
        again = await app_client.post(
            f"{BASE}/assets/linked?issue_id={issue['id']}",
            json={"asset_id": asset["id"]},
            headers=headers,
        )
        assert again.status_code == 409, again.text
        assert again.json()["error"]["code"] == "desk.asset_already_linked"

"""wiki 통합 테스트. 라우터 배선과 경로 우선순위를 고정한다."""

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


async def _space(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    r = await client.post(
        f"{BASE}/spaces",
        json={"key": "W" + secrets.token_hex(3).upper(), "name": "API Docs"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


class TestSpaceRoutes:
    async def test_create_and_read_back(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)

        by_id = await app_client.get(f"{BASE}/spaces/{space['id']}", headers=headers)
        assert by_id.status_code == 200, by_id.text
        assert by_id.json()["key"] == space["key"]

    async def test_by_key_is_not_shadowed_by_space_id(self, app_client: httpx.AsyncClient) -> None:
        """`/spaces/by-key/X` 는 `/spaces/{space_id}` 보다 먼저 선언돼야 한다.

        뒤에 두면 FastAPI 가 "by-key" 를 UUID 로 읽어 422 를 낸다. 선언 순서에만
        달려 있어 리팩터링 한 번에 조용히 깨진다.
        """
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        r = await app_client.get(f"{BASE}/spaces/by-key/{space['key']}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["id"] == space["id"]

    async def test_search_narrows_the_list(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        r = await app_client.get(f"{BASE}/spaces", params={"q": space["key"]}, headers=headers)
        assert r.status_code == 200, r.text
        assert [s["key"] for s in r.json()["items"]] == [space["key"]]


class TestPageRoutes:
    async def test_full_lifecycle(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)

        created = await app_client.post(
            f"{BASE}/pages",
            json={
                "space_id": space["id"],
                "title": "Deploy Runbook",
                "body": "# Deploy\n\nsteps",
                "labels": ["runbook"],
                "publish": True,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        page = created.json()
        assert page["path"] == "deploy-runbook"
        assert page["status"] == "published"
        assert page["labels"] == ["runbook"]
        assert page["version_number"] == 1

        edited = await app_client.patch(
            f"{BASE}/pages/{page['id']}",
            json={"body": "# Deploy\n\nnew steps", "message": "고침"},
            headers={**headers, "If-Match": str(page["version"])},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["version_number"] == 2

        history = await app_client.get(f"{BASE}/pages/{page['id']}/versions", headers=headers)
        assert [v["number"] for v in history.json()] == [2, 1]

        restored = await app_client.post(
            f"{BASE}/pages/{page['id']}/versions/1/restore", headers=headers
        )
        assert restored.status_code == 200, restored.text
        # 되감지 않고 새 판을 만든다.
        assert restored.json()["version_number"] == 3
        assert "new steps" not in restored.json()["body"]

    async def test_by_path_is_not_shadowed_by_page_id(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        created = await app_client.post(
            f"{BASE}/pages",
            json={"space_id": space["id"], "title": "Guide", "publish": True},
            headers=headers,
        )
        assert created.status_code == 201, created.text

        r = await app_client.get(
            f"{BASE}/pages/by-path",
            params={"space": space["key"], "path": "guide"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["id"] == created.json()["id"]

    async def test_tree_reflects_moves(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)

        async def make(title: str, parent: str | None = None) -> dict[str, Any]:
            body: dict[str, Any] = {"space_id": space["id"], "title": title, "publish": True}
            if parent:
                body["parent_id"] = parent
            r = await app_client.post(f"{BASE}/pages", json=body, headers=headers)
            assert r.status_code == 201, r.text
            return dict(r.json())

        top = await make("Top")
        child = await make("Child", top["id"])

        tree = await app_client.get(f"{BASE}/spaces/{space['id']}/tree", headers=headers)
        assert {n["path"] for n in tree.json()} == {"top", "top/child"}

        moved = await app_client.post(
            f"{BASE}/pages/{child['id']}/move", json={"new_parent_id": None}, headers=headers
        )
        assert moved.status_code == 200, moved.text
        tree_after = await app_client.get(f"{BASE}/spaces/{space['id']}/tree", headers=headers)
        assert {n["path"] for n in tree_after.json()} == {"top", "child"}

    async def test_restrictions_round_trip(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        me = (await app_client.get(f"{BASE}/auth/me", headers=headers)).json()
        created = await app_client.post(
            f"{BASE}/pages",
            json={"space_id": space["id"], "title": "Secret", "publish": True},
            headers=headers,
        )
        page_id = created.json()["id"]

        put = await app_client.put(
            f"{BASE}/pages/{page_id}/restrictions",
            json={"mode": "view", "principals": [{"kind": "user", "id": me["id"]}]},
            headers=headers,
        )
        assert put.status_code == 200, put.text
        assert [r["principal_id"] for r in put.json()] == [me["id"]]

        # 관리자는 자기 자신을 지정했으므로 여전히 열린다.
        assert (await app_client.get(f"{BASE}/pages/{page_id}", headers=headers)).status_code == 200

        cleared = await app_client.put(
            f"{BASE}/pages/{page_id}/restrictions",
            json={"mode": "view", "principals": []},
            headers=headers,
        )
        assert cleared.json() == []

    async def test_patch_rejects_unknown_fields(self, app_client: httpx.AsyncClient) -> None:
        """조용히 무시하면 클라이언트는 200 을 받고 아무것도 안 바뀐 걸 모른다."""
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        created = await app_client.post(
            f"{BASE}/pages",
            json={"space_id": space["id"], "title": "Doc"},
            headers=headers,
        )
        r = await app_client.patch(
            f"{BASE}/pages/{created.json()['id']}",
            json={"titl": "typo"},
            headers=headers,
        )
        assert r.status_code == 422, r.text

    async def test_stale_if_match_conflicts(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        created = await app_client.post(
            f"{BASE}/pages",
            json={"space_id": space["id"], "title": "Doc", "body": "a"},
            headers=headers,
        )
        page = created.json()
        await app_client.patch(f"{BASE}/pages/{page['id']}", json={"body": "b"}, headers=headers)
        stale = await app_client.patch(
            f"{BASE}/pages/{page['id']}",
            json={"body": "c"},
            headers={**headers, "If-Match": str(page["version"])},
        )
        assert stale.status_code == 409, stale.text

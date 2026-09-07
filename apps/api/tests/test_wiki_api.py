"""wiki 통합 테스트. 라우터 배선과 경로 우선순위를 고정한다."""

from __future__ import annotations

import io
import secrets
import zipfile
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration

BASE = "/api/v1"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "seed-admin-password-1234"


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


class TestImportExport:
    async def test_single_markdown_becomes_a_page(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)

        r = await app_client.post(
            f"{BASE}/spaces/{space['id']}/import",
            files={"file": ("runbook.md", b"# Deploy\n\nsteps here", "text/markdown")},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        pages = r.json()
        assert len(pages) == 1
        # 첫 H1 이 제목이 되고 본문에서는 빠진다.
        assert pages[0]["title"] == "Deploy"
        assert pages[0]["body"] == "steps here"
        # 올린 문서는 바로 읽힌다. 초안이면 올려 놓고 "왜 안 보이지" 가 된다.
        assert pages[0]["status"] == "published"

    async def test_archive_becomes_a_tree(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("deploy.md", "# Deploy\n\ntop level")
            archive.writestr("deploy/rollback.md", "# Rollback\n\nunder deploy")

        r = await app_client.post(
            f"{BASE}/spaces/{space['id']}/import",
            files={"file": ("docs.zip", buffer.getvalue(), "application/zip")},
            headers=headers,
        )
        assert r.status_code == 200, r.text

        tree = await app_client.get(f"{BASE}/spaces/{space['id']}/tree", headers=headers)
        # 폴더 구조가 그대로 트리가 된다.
        assert {n["path"] for n in tree.json()} == {"deploy", "deploy/rollback"}

    async def test_export_round_trips_through_the_api(self, app_client: httpx.AsyncClient) -> None:
        """올린 것을 내보내고 다시 올리면 같은 본문이 나와야 한다."""
        headers = await _auth(app_client)
        first = await _space(app_client, headers)
        source = "---\ntitle: Runbook\nlabels: [ops]\n---\n\n## Steps\n\n- build\n- ship"

        created = await app_client.post(
            f"{BASE}/spaces/{first['id']}/import",
            files={"file": ("r.md", source.encode(), "text/markdown")},
            headers=headers,
        )
        page_id = created.json()[0]["id"]

        exported = await app_client.get(f"{BASE}/pages/{page_id}/export", headers=headers)
        assert exported.status_code == 200, exported.text
        assert exported.headers["content-type"].startswith("text/markdown")

        second = await _space(app_client, headers)
        again = await app_client.post(
            f"{BASE}/spaces/{second['id']}/import",
            files={"file": ("r.md", exported.content, "text/markdown")},
            headers=headers,
        )
        assert again.status_code == 200, again.text
        assert again.json()[0]["title"] == created.json()[0]["title"]
        assert again.json()[0]["body"] == created.json()[0]["body"]
        assert again.json()[0]["labels"] == ["ops"]

    async def test_space_export_is_a_zip(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        await app_client.post(
            f"{BASE}/pages",
            json={"space_id": space["id"], "title": "One", "body": "x", "publish": True},
            headers=headers,
        )

        r = await app_client.get(f"{BASE}/spaces/{space['id']}/export", headers=headers)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        # 내보내기는 사용자별 ACL 을 탄다. 중간 캐시에 남으면 안 된다.
        assert r.headers["cache-control"] == "no-store"

        with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
            assert archive.namelist() == ["one.md"]
            assert b"title: One" in archive.read("one.md")

    async def test_attachments_travel_in_the_zip(self, app_client: httpx.AsyncClient) -> None:
        """ZIP → 첨부 → ZIP. 라우터까지 배선돼 있어야 왕복이 닫힌다.

        내보내기에 스토리지를 안 물리면 조용히 텍스트만 나간다 — 옮긴 쪽에서
        그림이 전부 깨진 채로. 서비스 테스트만으로는 그 배선을 못 잡는다.
        """
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        blob = b"\x89PNG\r\n\x1a\n" + b"pixels"

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("guide.md", "# 안내\n\n![그림](images/a.png)\n")
            archive.writestr("images/a.png", blob)
        imported = await app_client.post(
            f"{BASE}/spaces/{space['id']}/import",
            files={"file": ("bundle.zip", buffer.getvalue(), "application/zip")},
            headers=headers,
        )
        assert imported.status_code == 200, imported.text
        assert "attachment:" in imported.json()[0]["body"]
        # 제목은 첫 H1 에서 온다. 파일명이 아니라 그 제목이 경로를 정한다.
        path = imported.json()[0]["path"]

        exported = await app_client.get(f"{BASE}/spaces/{space['id']}/export", headers=headers)
        assert exported.status_code == 200, exported.text
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            images = [n for n in archive.namelist() if n.endswith("a.png")]
            assert len(images) == 1, archive.namelist()
            assert archive.read(images[0]) == blob
            body = archive.read(f"{path}.md").decode()
        # 스킴이 아니라 상대 경로로 나간다. 그래야 다시 올렸을 때 붙는다.
        assert "attachment:" not in body
        assert f"{path}.assets/" in body

    async def test_non_utf8_upload_is_rejected(self, app_client: httpx.AsyncClient) -> None:
        """추측해서 열면 깨진 글자가 문서로 들어앉고 되돌릴 방법이 없다."""
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        r = await app_client.post(
            f"{BASE}/spaces/{space['id']}/import",
            files={"file": ("x.md", "한글".encode("euc-kr"), "text/markdown")},
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "wiki.invalid_encoding"

    async def test_a_korean_title_can_be_exported(self, app_client: httpx.AsyncClient) -> None:
        """한글 제목 문서는 slug 도 한글이다 (로마자로 옮기지 않는다).

        파일명을 헤더에 그대로 넣으면 latin-1 로 인코딩하다 터진다 — 실제로
        한국어 사용자가 첫 문서에서 바로 500 을 봤다.
        """
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        created = await app_client.post(
            f"{BASE}/pages",
            json={"space_id": space["id"], "title": "배포 절차", "body": "본문", "publish": True},
            headers=headers,
        )
        assert created.status_code == 201, created.text

        r = await app_client.get(f"{BASE}/pages/{created.json()['id']}/export", headers=headers)
        assert r.status_code == 200, r.text
        assert "filename*=UTF-8''" in r.headers["content-disposition"]
        assert "본문" in r.text

        # 스페이스 통째로도 마찬가지다.
        zipped = await app_client.get(f"{BASE}/spaces/{space['id']}/export", headers=headers)
        assert zipped.status_code == 200, zipped.text
        with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
            assert archive.namelist() == ["배포-절차.md"]


async def _page(
    client: httpx.AsyncClient, headers: dict[str, str], space_id: str, body: str
) -> dict[str, Any]:
    r = await client.post(
        f"{BASE}/pages",
        json={"space_id": space_id, "title": "Runbook", "body": body, "publish": True},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


class TestComments:
    """문서 코멘트와 인라인 앵커 (wiki-markdown.md 6절)."""

    BODY = "배포 전 마이그레이션을 검토한다. 운영 반영 시 롤백 계획을 준비한다."

    async def test_a_page_comment_needs_no_anchor(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], self.BODY)

        r = await app_client.post(
            f"{BASE}/pages/{page['id']}/comments", json={"body": "잘 읽었다."}, headers=headers
        )
        assert r.status_code == 201, r.text
        assert r.json()["anchor"] is None
        assert r.json()["anchor_status"] == "ok"

    async def test_an_inline_comment_reports_where_it_landed(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], self.BODY)

        r = await app_client.post(
            f"{BASE}/pages/{page['id']}/comments",
            json={
                "body": "여기 확인이 필요하다.",
                "anchor": {"exact": "마이그레이션을 검토한다", "prefix": "배포 전 "},
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        match = r.json()["match"]
        assert match["how"] == "exact"
        assert match["found"] == "마이그레이션을 검토한다"

    async def test_editing_the_page_orphans_a_lost_quote(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """조용히 지우지 않는다. 인용문은 남고 고아로 표시된다."""
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], self.BODY)
        await app_client.post(
            f"{BASE}/pages/{page['id']}/comments",
            json={"body": "확인", "anchor": {"exact": "마이그레이션을 검토한다"}},
            headers=headers,
        )

        await app_client.patch(
            f"{BASE}/pages/{page['id']}",
            json={"body": "이제 완전히 다른 내용만 남았다."},
            headers=headers,
        )

        listed = await app_client.get(f"{BASE}/pages/{page['id']}/comments", headers=headers)
        assert listed.status_code == 200, listed.text
        row = listed.json()[0]
        assert row["anchor_status"] == "orphaned"
        assert row["match"] is None
        # 인용문은 그대로다 — 무엇에 달았던 코멘트인지 보여야 한다.
        assert row["anchor"]["exact"] == "마이그레이션을 검토한다"

    async def test_a_restored_page_re_anchors(self, app_client: httpx.AsyncClient) -> None:
        """되돌리면 다시 붙는다. 한 번 고아면 영영 고아인 것은 틀렸다."""
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], self.BODY)
        await app_client.post(
            f"{BASE}/pages/{page['id']}/comments",
            json={"body": "확인", "anchor": {"exact": "마이그레이션을 검토한다"}},
            headers=headers,
        )
        await app_client.patch(
            f"{BASE}/pages/{page['id']}", json={"body": "딴 얘기."}, headers=headers
        )
        orphaned = await app_client.get(f"{BASE}/pages/{page['id']}/comments", headers=headers)
        assert orphaned.json()[0]["anchor_status"] == "orphaned"

        await app_client.post(f"{BASE}/pages/{page['id']}/versions/1/restore", headers=headers)

        again = await app_client.get(f"{BASE}/pages/{page['id']}/comments", headers=headers)
        assert again.json()[0]["anchor_status"] == "ok"

    async def test_a_small_edit_still_matches(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], self.BODY)
        await app_client.post(
            f"{BASE}/pages/{page['id']}/comments",
            json={"body": "확인", "anchor": {"exact": "마이그레이션을 검토한다"}},
            headers=headers,
        )
        await app_client.patch(
            f"{BASE}/pages/{page['id']}",
            json={"body": "배포 전에 마이그레이션을 꼭 검토한다. 뒤 문장."},
            headers=headers,
        )

        listed = await app_client.get(f"{BASE}/pages/{page['id']}/comments", headers=headers)
        row = listed.json()[0]
        assert row["anchor_status"] == "ok"
        assert row["match"]["how"] == "fuzzy"

    async def test_resolve_and_reopen(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], self.BODY)
        created = await app_client.post(
            f"{BASE}/pages/{page['id']}/comments", json={"body": "확인 부탁"}, headers=headers
        )
        comment_id = created.json()["id"]

        resolved = await app_client.post(
            f"{BASE}/pages/comments/{comment_id}/resolve", json={"resolved": True}, headers=headers
        )
        assert resolved.json()["resolved_at"] is not None

        reopened = await app_client.post(
            f"{BASE}/pages/comments/{comment_id}/resolve",
            json={"resolved": False},
            headers=headers,
        )
        assert reopened.json()["resolved_at"] is None

    async def test_edit_marks_the_time(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], self.BODY)
        created = await app_client.post(
            f"{BASE}/pages/{page['id']}/comments", json={"body": "처음"}, headers=headers
        )
        edited = await app_client.patch(
            f"{BASE}/pages/comments/{created.json()['id']}",
            json={"body": "고쳤다"},
            headers=headers,
        )
        assert edited.json()["body"] == "고쳤다"
        assert edited.json()["edited_at"] is not None

    async def test_replies_are_one_level_deep(self, app_client: httpx.AsyncClient) -> None:
        """더 깊어지면 화면에서 읽을 수 없다."""
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], self.BODY)
        root = await app_client.post(
            f"{BASE}/pages/{page['id']}/comments", json={"body": "질문"}, headers=headers
        )
        reply = await app_client.post(
            f"{BASE}/pages/{page['id']}/comments",
            json={"body": "답", "parent_id": root.json()["id"]},
            headers=headers,
        )
        assert reply.status_code == 201, reply.text

        deeper = await app_client.post(
            f"{BASE}/pages/{page['id']}/comments",
            json={"body": "또 답", "parent_id": reply.json()["id"]},
            headers=headers,
        )
        assert deeper.status_code == 422
        assert deeper.json()["error"]["code"] == "wiki.comment_nesting_too_deep"

    async def test_an_empty_comment_is_rejected(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], self.BODY)
        r = await app_client.post(
            f"{BASE}/pages/{page['id']}/comments", json={"body": "   "}, headers=headers
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "wiki.comment_empty"

    async def test_delete_removes_it(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], self.BODY)
        created = await app_client.post(
            f"{BASE}/pages/{page['id']}/comments", json={"body": "지울 것"}, headers=headers
        )
        gone = await app_client.delete(
            f"{BASE}/pages/comments/{created.json()['id']}", headers=headers
        )
        assert gone.status_code == 204
        listed = await app_client.get(f"{BASE}/pages/{page['id']}/comments", headers=headers)
        assert listed.json() == []


class TestIssueLinks:
    """문서가 이슈를 언급하면 이슈 쪽에서도 보인다 (양방향).

    링크 표는 중립 테이블(`entity_link`)이다. 위키가 쓰고 위키가 읽는다 —
    이슈 모듈이 답하려면 위키를 알아야 하고, 그러면 의존 그래프에 고리가
    생긴다.
    """

    async def _issue(self, client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
        project = await client.post(
            f"{BASE}/projects",
            json={"key": "L" + secrets.token_hex(3).upper(), "name": "Links"},
            headers=headers,
        )
        assert project.status_code == 201, project.text
        issue = await client.post(
            f"{BASE}/issues",
            json={"project_id": project.json()["id"], "summary": "Linked"},
            headers=headers,
        )
        assert issue.status_code == 201, issue.text
        return dict(issue.json())

    async def test_a_page_that_mentions_an_issue_shows_up(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = await _auth(app_client)
        issue = await self._issue(app_client, headers)
        space = await _space(app_client, headers)
        await _page(
            app_client, headers, space["id"], f"자세한 것은 [x](issue:{issue['key']}) 참고."
        )

        r = await app_client.get(f"{BASE}/pages/mentioning/{issue['id']}", headers=headers)
        assert r.status_code == 200, r.text
        assert [row["title"] for row in r.json()] == ["Runbook"]

    async def test_removing_the_link_removes_the_row(self, app_client: httpx.AsyncClient) -> None:
        """본문에서 링크를 지우면 이슈 쪽에서도 사라져야 한다."""
        headers = await _auth(app_client)
        issue = await self._issue(app_client, headers)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], f"[x](issue:{issue['key']})")

        await app_client.patch(
            f"{BASE}/pages/{page['id']}", json={"body": "이제 링크가 없다."}, headers=headers
        )

        r = await app_client.get(f"{BASE}/pages/mentioning/{issue['id']}", headers=headers)
        assert r.json() == []

    async def test_a_trashed_page_stops_showing_up(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        issue = await self._issue(app_client, headers)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], f"[x](issue:{issue['key']})")

        await app_client.post(f"{BASE}/pages/{page['id']}/archive", headers=headers)

        r = await app_client.get(f"{BASE}/pages/mentioning/{issue['id']}", headers=headers)
        assert r.json() == []

    async def test_an_unknown_key_does_not_block_saving(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """오타 하나로 문서 저장이 막히면 안 된다."""
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        r = await app_client.post(
            f"{BASE}/pages",
            json={
                "space_id": space["id"],
                "title": "Typo",
                "body": "[x](issue:NOPE-999) 와 [y](issue:이상함)",
                "publish": True,
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text

    async def test_a_link_inside_a_code_block_is_an_example(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = await _auth(app_client)
        issue = await self._issue(app_client, headers)
        space = await _space(app_client, headers)
        await _page(app_client, headers, space["id"], f"```\n[x](issue:{issue['key']})\n```")

        r = await app_client.get(f"{BASE}/pages/mentioning/{issue['id']}", headers=headers)
        assert r.json() == []


class TestTemplates:
    async def test_create_list_and_delete(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)

        created = await app_client.post(
            f"{BASE}/spaces/{space['id']}/templates",
            json={"name": "회의록", "body": "## 참석자\n\n## 결정"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["space_id"] == space["id"]

        listed = await app_client.get(f"{BASE}/spaces/{space['id']}/templates", headers=headers)
        assert [row["name"] for row in listed.json()] == ["회의록"]

        gone = await app_client.delete(
            f"{BASE}/spaces/templates/{created.json()['id']}", headers=headers
        )
        assert gone.status_code == 204
        again = await app_client.get(f"{BASE}/spaces/{space['id']}/templates", headers=headers)
        assert again.json() == []

    async def test_a_nameless_template_is_rejected(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        r = await app_client.post(
            f"{BASE}/spaces/{space['id']}/templates", json={"name": "   "}, headers=headers
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "wiki.template_name_required"

    async def test_the_body_is_normalized(self, app_client: httpx.AsyncClient) -> None:
        """템플릿도 본문이다. 정규화를 건너뛰면 이걸로 만든 문서마다 diff 가 흔들린다."""
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        r = await app_client.post(
            f"{BASE}/spaces/{space['id']}/templates",
            json={"name": "Sloppy", "body": "*  항목\n*  항목"},
            headers=headers,
        )
        assert r.json()["body"] == "- 항목\n- 항목"

    async def test_another_space_does_not_see_it(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        first = await _space(app_client, headers)
        second = await _space(app_client, headers)
        await app_client.post(
            f"{BASE}/spaces/{first['id']}/templates", json={"name": "Mine"}, headers=headers
        )

        listed = await app_client.get(f"{BASE}/spaces/{second['id']}/templates", headers=headers)
        assert listed.json() == []


class TestVersionDiff:
    async def test_compares_two_versions(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], "처음 본문")
        await app_client.patch(
            f"{BASE}/pages/{page['id']}", json={"body": "고친 본문"}, headers=headers
        )

        r = await app_client.get(
            f"{BASE}/pages/{page['id']}/diff?before=1&after=2", headers=headers
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["added"], body["removed"]) == (1, 1)
        assert {line["op"] for line in body["lines"]} == {"insert", "delete"}

    async def test_an_unknown_version_is_not_found(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], "본문")
        r = await app_client.get(
            f"{BASE}/pages/{page['id']}/diff?before=1&after=99", headers=headers
        )
        assert r.status_code == 404

    async def test_version_zero_is_rejected(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], "본문")
        r = await app_client.get(
            f"{BASE}/pages/{page['id']}/diff?before=0&after=1", headers=headers
        )
        assert r.status_code == 422


class TestDrafts:
    """자동 저장. 판을 만들지 않는다 — 30초마다 판이 쌓이면 이력을 못 읽는다."""

    async def test_saving_a_draft_does_not_make_a_version(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], "처음")

        before = await app_client.get(f"{BASE}/pages/{page['id']}/versions", headers=headers)
        r = await app_client.put(
            f"{BASE}/pages/{page['id']}/draft",
            json={"title": "Runbook", "body": "쓰는 중", "base_version": 1},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        after = await app_client.get(f"{BASE}/pages/{page['id']}/versions", headers=headers)
        assert len(after.json()) == len(before.json())

    async def test_the_draft_comes_back(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], "처음")
        await app_client.put(
            f"{BASE}/pages/{page['id']}/draft",
            json={"title": "T", "body": "쓰다 만 글"},
            headers=headers,
        )

        r = await app_client.get(f"{BASE}/pages/{page['id']}/draft", headers=headers)
        assert r.json()["body"] == "쓰다 만 글"

    async def test_saving_twice_keeps_one_draft(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], "처음")
        for text in ("하나", "둘", "셋"):
            await app_client.put(
                f"{BASE}/pages/{page['id']}/draft", json={"body": text}, headers=headers
            )
        r = await app_client.get(f"{BASE}/pages/{page['id']}/draft", headers=headers)
        assert r.json()["body"] == "셋"

    async def test_publishing_clears_the_draft(self, app_client: httpx.AsyncClient) -> None:
        """남기면 다음에 열 때 "저장 안 한 편집이 있다" 고 거짓말을 한다."""
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], "처음")
        await app_client.put(
            f"{BASE}/pages/{page['id']}/draft", json={"body": "쓰는 중"}, headers=headers
        )

        await app_client.patch(
            f"{BASE}/pages/{page['id']}", json={"body": "다 썼다"}, headers=headers
        )

        r = await app_client.get(f"{BASE}/pages/{page['id']}/draft", headers=headers)
        assert r.json() is None

    async def test_discarding_removes_it(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], "처음")
        await app_client.put(
            f"{BASE}/pages/{page['id']}/draft", json={"body": "버릴 것"}, headers=headers
        )
        gone = await app_client.delete(f"{BASE}/pages/{page['id']}/draft", headers=headers)
        assert gone.status_code == 204
        r = await app_client.get(f"{BASE}/pages/{page['id']}/draft", headers=headers)
        assert r.json() is None

    async def test_no_draft_is_null_not_an_error(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], "처음")
        r = await app_client.get(f"{BASE}/pages/{page['id']}/draft", headers=headers)
        assert r.status_code == 200
        assert r.json() is None


class TestCopy:
    async def test_copies_a_page_with_its_body(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], "복사될 본문")

        r = await app_client.post(
            f"{BASE}/pages/{page['id']}/copy", json={"title": "Runbook (copy)"}, headers=headers
        )
        assert r.status_code == 201, r.text
        assert r.json()["body"] == "복사될 본문"
        assert r.json()["id"] != page["id"]

    async def test_copies_the_whole_branch(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        parent = await _page(app_client, headers, space["id"], "부모")
        child = await app_client.post(
            f"{BASE}/pages",
            json={
                "space_id": space["id"],
                "title": "Child",
                "parent_id": parent["id"],
                "body": "자식",
                "publish": True,
            },
            headers=headers,
        )
        assert child.status_code == 201, child.text

        copied = await app_client.post(
            f"{BASE}/pages/{parent['id']}/copy", json={"title": "Copied"}, headers=headers
        )
        assert copied.status_code == 201, copied.text

        tree = await app_client.get(f"{BASE}/spaces/{space['id']}/tree", headers=headers)
        paths = {node["path"] for node in tree.json()}
        assert "copied" in paths
        assert "copied/child" in paths

    async def test_the_copy_starts_its_own_history(self, app_client: httpx.AsyncClient) -> None:
        """원본의 판 번호를 물려받으면 "v7 로 되돌리기" 가 어느 v7 인지 모른다."""
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        page = await _page(app_client, headers, space["id"], "처음")
        await app_client.patch(
            f"{BASE}/pages/{page['id']}", json={"body": "두 번째"}, headers=headers
        )

        copied = await app_client.post(
            f"{BASE}/pages/{page['id']}/copy", json={"title": "Fresh"}, headers=headers
        )
        history = await app_client.get(
            f"{BASE}/pages/{copied.json()['id']}/versions", headers=headers
        )
        assert [v["number"] for v in history.json()] == [1]

    async def test_cannot_copy_into_its_own_descendant(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        space = await _space(app_client, headers)
        parent = await _page(app_client, headers, space["id"], "부모")
        child = await app_client.post(
            f"{BASE}/pages",
            json={"space_id": space["id"], "title": "Child", "parent_id": parent["id"]},
            headers=headers,
        )

        r = await app_client.post(
            f"{BASE}/pages/{parent['id']}/copy",
            json={"new_parent_id": child.json()["id"]},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "wiki.copy_into_descendant"

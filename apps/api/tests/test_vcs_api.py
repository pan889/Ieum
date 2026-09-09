"""저장소 연동을 HTTP 로 몰아 본다 (feature-map A22).

**웹훅 수신은 인증이 없는 유일한 쓰기 경로다.** 그래서 서비스 시험만으로는
부족하다. 여기서 보는 것은 문 자체다:

- 서명이 틀리면 **아무것도 남지 않는다.**
- 주소의 호스트 이름이 저장한 것과 다르면 404 다 — 그 검사가 없으면 보내는
  쪽이 **어느 방식으로 확인받을지 고를 수 있다**(GitLab 은 공유 토큰이다).
- 우리가 안 읽는 이벤트(`ping`)도 200 이고, **받았다는 시각은 남는다.**
  링크를 만들 때만 적으면 배선을 옳게 끝낸 사람의 화면이 "아무것도 못
  받았다" 로 보인다.
- 시크릿은 등록 응답에만 있다. 목록에는 없다.

그리고 라우터가 커밋하는지도 여기서만 드러난다 — 쓴 다음 **다른 요청으로**
읽는다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pyotp
import pytest

from ieum.core.time import utcnow

pytestmark = pytest.mark.integration

BASE = "/api/v1"
ADMIN = ("admin@example.com", "seed-admin-password-1234")
SHA = "a1b2c3d4" * 5


async def _admin_headers(client: httpx.AsyncClient) -> dict[str, str]:
    """step-up 을 통과하는 관리자. **2FA 를 실제로 등록하고 통과한다** —
    저장소 등록이 step-up 대상이기 때문이다 (`vcs/permissions.py`)."""
    first = await client.post(f"{BASE}/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert first.status_code == 200, first.text
    headers = {"Authorization": f"Bearer {first.json()['access_token']}"}

    enroll = await client.post(f"{BASE}/auth/mfa/totp/enroll", headers=headers)
    assert enroll.status_code == 200, enroll.text
    body = enroll.json()
    secret = body["secret"]
    confirm = await client.post(
        f"{BASE}/auth/mfa/totp/{body['credential_id']}/confirm",
        headers=headers,
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert confirm.status_code == 204, confirm.text

    pending = await client.post(
        f"{BASE}/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}
    )
    assert pending.json()["mfa_required"] is True
    verified_headers = {"Authorization": f"Bearer {pending.json()['access_token']}"}
    # 등록 확인에 쓴 코드는 재사용 방지에 걸린다. 다음 스텝의 코드를 쓴다 —
    # 허용 범위 ±1 안이라 받아들여진다.
    code = pyotp.TOTP(secret).at(utcnow() + timedelta(seconds=30))
    done = await client.post(
        f"{BASE}/auth/mfa/verify", headers=verified_headers, json={"code": code}
    )
    assert done.status_code == 204, done.text
    return verified_headers


async def _project(client: httpx.AsyncClient, headers: dict[str, str], key: str) -> dict[str, Any]:
    made = await client.post(
        f"{BASE}/projects", json={"key": key, "name": f"{key} project"}, headers=headers
    )
    assert made.status_code == 201, made.text
    return dict(made.json())


async def _issue(
    client: httpx.AsyncClient, headers: dict[str, str], project_id: str, summary: str
) -> dict[str, Any]:
    types = await client.get(f"{BASE}/issues/types?project_id={project_id}", headers=headers)
    assert types.status_code == 200, types.text
    made = await client.post(
        f"{BASE}/issues",
        json={"project_id": project_id, "type_id": types.json()[0]["id"], "summary": summary},
        headers=headers,
    )
    assert made.status_code == 201, made.text
    return dict(made.json())


async def _register(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    project_ids: list[str],
    *,
    provider: str = "github",
    name: str = "acme/web",
) -> dict[str, Any]:
    made = await client.post(
        f"{BASE}/repositories",
        json={"provider": provider, "name": name, "project_ids": project_ids},
        headers=headers,
    )
    assert made.status_code == 201, made.text
    return dict(made.json())


def _push(message: str, sha: str = SHA) -> bytes:
    payload = {
        "ref": "refs/heads/main",
        "commits": [
            {
                "id": sha,
                "message": message,
                "url": f"https://github.com/acme/web/commit/{sha}",
                "timestamp": "2026-09-01T10:00:00+09:00",
                "author": {"username": "sujin", "name": "김수진"},
            }
        ],
    }
    return json.dumps(payload).encode("utf-8")


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _deliver(
    client: httpx.AsyncClient,
    repository: dict[str, Any],
    body: bytes,
    *,
    event: str = "push",
    signature: str | None = None,
) -> httpx.Response:
    """**Authorization 헤더가 없다.** 이 문은 서명으로만 확인한다."""
    return await client.post(
        repository["webhook_path"],
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": signature or _sign(body, repository["secret"]),
            "Content-Type": "application/json",
        },
    )


class TestRegistering:
    async def test_the_secret_is_in_the_creation_response_only(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "VCA")
        issued = await _register(app_client, headers, [project["id"]])
        assert issued["secret"]
        assert issued["webhook_path"] == f"{BASE}/vcs/github/{issued['id']}"

        # 다른 요청으로 읽는다. 라우터가 커밋하지 않았으면 여기서 사라진다.
        listed = await app_client.get(
            f"{BASE}/repositories?project_id={project['id']}", headers=headers
        )
        assert listed.status_code == 200, listed.text
        (row,) = listed.json()
        assert row["id"] == issued["id"]
        assert "secret" not in row
        assert row["last_event_at"] is None

    async def test_registering_needs_step_up(self, app_client: httpx.AsyncClient) -> None:
        """저장소 등록은 **바깥에서 우리 이슈에 글을 붙이는 길**을 여는
        일이다. 2FA 없이 몰래 열리면 안 된다."""
        first = await app_client.post(
            f"{BASE}/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}
        )
        headers = {"Authorization": f"Bearer {first.json()['access_token']}"}
        project = await _project(app_client, headers, "VCB")
        refused = await app_client.post(
            f"{BASE}/repositories",
            json={"provider": "github", "name": "acme/web", "project_ids": [project["id"]]},
            headers=headers,
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "auth.step_up_requires_mfa"

    async def test_a_repository_without_projects_is_refused_at_the_edge(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = await _admin_headers(app_client)
        refused = await app_client.post(
            f"{BASE}/repositories",
            json={"provider": "github", "name": "acme/web", "project_ids": []},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text

    async def test_deleting_one_leaves_the_others(self, app_client: httpx.AsyncClient) -> None:
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "VCC")
        one = await _register(app_client, headers, [project["id"]], name="acme/one")
        await _register(app_client, headers, [project["id"]], name="acme/two")
        gone = await app_client.delete(f"{BASE}/repositories/{one['id']}", headers=headers)
        assert gone.status_code == 204, gone.text
        listed = await app_client.get(
            f"{BASE}/repositories?project_id={project['id']}", headers=headers
        )
        assert [row["name"] for row in listed.json()] == ["acme/two"]


class TestTheWebhookDoor:
    async def test_a_signed_push_links_the_commit(self, app_client: httpx.AsyncClient) -> None:
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "HOK")
        issue = await _issue(app_client, headers, project["id"], "로그인이 안 된다")
        repository = await _register(app_client, headers, [project["id"]])

        got = await _deliver(app_client, repository, _push(f"fixes {issue['key']} 고친다"))
        assert got.status_code == 200, got.text
        assert got.json() == {"received": 1, "linked": 1}

        links = await app_client.get(
            f"{BASE}/repositories/links?issue_id={issue['id']}", headers=headers
        )
        assert links.status_code == 200, links.text
        (link,) = links.json()
        assert link["external_ref"] == SHA
        assert link["closing"] is True
        assert link["author"] == "sujin"
        assert link["repository_name"] == "acme/web"

    async def test_a_wrong_signature_leaves_nothing(self, app_client: httpx.AsyncClient) -> None:
        """확인이 없으면 주소를 아는 누구나 이슈에 커밋 제목과 주소를 남길 수
        있다 — 그리고 그건 이슈 이력에 영구히 남는다."""
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "BAD")
        issue = await _issue(app_client, headers, project["id"], "고쳐야 한다")
        repository = await _register(app_client, headers, [project["id"]])

        body = _push(f"{issue['key']} 몰래 붙인다")
        refused = await _deliver(
            app_client, repository, body, signature=_sign(body, "not-the-secret")
        )
        assert refused.status_code == 401, refused.text
        assert refused.json()["error"]["code"] == "vcs.bad_signature"

        links = await app_client.get(
            f"{BASE}/repositories/links?issue_id={issue['id']}", headers=headers
        )
        assert links.json() == []

    async def test_a_tampered_body_is_refused(self, app_client: httpx.AsyncClient) -> None:
        """서명은 **몸에** 붙는다. 서명을 그대로 두고 몸을 바꾸면 어긋난다."""
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "TAM")
        issue = await _issue(app_client, headers, project["id"], "고쳐야 한다")
        repository = await _register(app_client, headers, [project["id"]])
        body = _push(f"{issue['key']} 원래 메시지")
        signature = _sign(body, repository["secret"])
        refused = await _deliver(
            app_client, repository, _push(f"{issue['key']} 바꾼 메시지"), signature=signature
        )
        assert refused.status_code == 401, refused.text

    async def test_no_signature_at_all_is_refused(self, app_client: httpx.AsyncClient) -> None:
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "NOS")
        repository = await _register(app_client, headers, [project["id"]])
        refused = await app_client.post(
            repository["webhook_path"],
            content=_push("NOS-1 고친다"),
            headers={"X-GitHub-Event": "push"},
        )
        assert refused.status_code == 401, refused.text

    async def test_the_host_in_the_path_must_match_what_we_stored(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """이 검사가 없으면 보내는 쪽이 **확인 방식을 고를 수 있다** —
        GitLab 쪽은 서명이 아니라 공유 토큰이다."""
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "MIS")
        repository = await _register(app_client, headers, [project["id"]])
        wrong = repository["webhook_path"].replace("/github/", "/gitlab/")
        refused = await app_client.post(
            wrong,
            content=b"{}",
            headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": repository["secret"]},
        )
        assert refused.status_code == 404, refused.text

    async def test_an_unknown_repository_id_is_not_found(
        self, app_client: httpx.AsyncClient
    ) -> None:
        refused = await app_client.post(
            f"{BASE}/vcs/github/0198f0a0-0000-7000-8000-000000000000",
            content=b"{}",
            headers={"X-GitHub-Event": "push"},
        )
        assert refused.status_code == 404, refused.text

    async def test_a_ping_records_that_we_heard_something(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """GitHub 은 웹훅을 만들 때 `ping` 을 먼저 보낸다. 링크를 만들 때만
        시각을 적으면 배선을 옳게 끝낸 사람의 화면이 "아무것도 못 받았다"
        로 보인다."""
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "PNG")
        repository = await _register(app_client, headers, [project["id"]])
        body = json.dumps({"zen": "Keep it logically awesome."}).encode()
        got = await _deliver(app_client, repository, body, event="ping")
        assert got.status_code == 200, got.text
        assert got.json() == {"received": 0, "linked": 0}

        listed = await app_client.get(
            f"{BASE}/repositories?project_id={project['id']}", headers=headers
        )
        assert listed.json()[0]["last_event_at"] is not None

    async def test_a_redelivery_does_not_stack(self, app_client: httpx.AsyncClient) -> None:
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "AGA")
        issue = await _issue(app_client, headers, project["id"], "고쳐야 한다")
        repository = await _register(app_client, headers, [project["id"]])
        body = _push(f"{issue['key']} 고친다")
        first = await _deliver(app_client, repository, body)
        again = await _deliver(app_client, repository, body)
        assert first.json()["linked"] == 1
        assert again.json()["linked"] == 0
        links = await app_client.get(
            f"{BASE}/repositories/links?issue_id={issue['id']}", headers=headers
        )
        assert len(links.json()) == 1

    async def test_a_disabled_repository_records_the_time_but_no_links(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """끄고 나서도 전송이 오는지는 운영자가 알아야 한다."""
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "OFF")
        issue = await _issue(app_client, headers, project["id"], "고쳐야 한다")
        repository = await _register(app_client, headers, [project["id"]])
        turned = await app_client.post(
            f"{BASE}/repositories/{repository['id']}/enabled",
            json={"is_enabled": False},
            headers=headers,
        )
        assert turned.status_code == 200, turned.text

        got = await _deliver(app_client, repository, _push(f"{issue['key']} 고친다"))
        assert got.status_code == 200, got.text
        assert got.json()["linked"] == 0
        links = await app_client.get(
            f"{BASE}/repositories/links?issue_id={issue['id']}", headers=headers
        )
        assert links.json() == []
        listed = await app_client.get(
            f"{BASE}/repositories?project_id={project['id']}", headers=headers
        )
        assert listed.json()[0]["last_event_at"] is not None

    async def test_a_form_encoded_delivery_is_read_too(self, app_client: httpx.AsyncClient) -> None:
        """GitHub 의 웹훅 설정에서 content type 을
        `application/x-www-form-urlencoded` 로 고르면 몸이 `payload=...` 로
        온다. 안 받으면 그 설치는 전송마다 422 를 받는데, 화면에는 "커밋이
        안 붙는다" 로만 보인다."""
        from urllib.parse import urlencode

        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "FRM")
        issue = await _issue(app_client, headers, project["id"], "고쳐야 한다")
        repository = await _register(app_client, headers, [project["id"]])
        inner = _push(f"{issue['key']} 폼으로 온다").decode()
        body = urlencode({"payload": inner}).encode()
        got = await app_client.post(
            repository["webhook_path"],
            content=body,
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": _sign(body, repository["secret"]),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        assert got.status_code == 200, got.text
        assert got.json() == {"received": 1, "linked": 1}

    async def test_a_body_that_is_not_json_is_refused(self, app_client: httpx.AsyncClient) -> None:
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "JNK")
        repository = await _register(app_client, headers, [project["id"]])
        refused = await _deliver(app_client, repository, b"not json at all")
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "vcs.invalid_payload"

    async def test_an_oversized_body_is_refused(
        self, app_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """인증 없는 문이다. 상한이 없으면 몸 하나로 프로세스의 메모리를
        채울 수 있다. `Content-Length` 를 보내는 요청은 읽기 전에 끊는다."""
        from ieum.modules.vcs import webhook_router

        monkeypatch.setattr(webhook_router, "MAX_BODY_BYTES", 64)
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "BIG")
        repository = await _register(app_client, headers, [project["id"]])
        refused = await _deliver(app_client, repository, _push("BIG-1 " + "x" * 500))
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "vcs.payload_too_large"

    async def test_an_oversized_chunked_body_is_refused(
        self, app_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**길이를 안 밝히고 오면** 헤더 검사가 못 잡는다. 청크 전송에는
        `Content-Length` 가 없다 — 실제로 막는 것은 읽은 뒤의 검사다."""
        from ieum.modules.vcs import webhook_router

        monkeypatch.setattr(webhook_router, "MAX_BODY_BYTES", 64)
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "CHK")
        repository = await _register(app_client, headers, [project["id"]])
        body = _push("CHK-1 " + "x" * 500)

        async def chunks() -> AsyncIterator[bytes]:
            yield body

        sent = await app_client.post(
            repository["webhook_path"],
            content=chunks(),
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": _sign(body, repository["secret"]),
                "Content-Type": "application/json",
            },
        )
        assert "content-length" not in {key.lower() for key in sent.request.headers}
        assert sent.status_code == 422, sent.text
        assert sent.json()["error"]["code"] == "vcs.payload_too_large"

    async def test_a_commit_naming_an_unregistered_project_links_nothing(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """시크릿을 아는 쪽이 설치 전체의 이슈에 글을 붙일 수 있으면 그건
        연동이 아니라 구멍이다."""
        headers = await _admin_headers(app_client)
        mine = await _project(app_client, headers, "MYP")
        theirs = await _project(app_client, headers, "THP")
        outsider = await _issue(app_client, headers, theirs["id"], "남의 이슈")
        repository = await _register(app_client, headers, [mine["id"]])

        got = await _deliver(app_client, repository, _push(f"{outsider['key']} 몰래 붙인다"))
        assert got.status_code == 200, got.text
        assert got.json()["linked"] == 0
        links = await app_client.get(
            f"{BASE}/repositories/links?issue_id={outsider['id']}", headers=headers
        )
        assert links.json() == []


class TestGitLabDoor:
    async def test_the_shared_token_opens_it(self, app_client: httpx.AsyncClient) -> None:
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "GLB")
        issue = await _issue(app_client, headers, project["id"], "고쳐야 한다")
        repository = await _register(
            app_client, headers, [project["id"]], provider="gitlab", name="acme/gl"
        )
        assert repository["webhook_path"] == f"{BASE}/vcs/gitlab/{repository['id']}"
        payload = {
            "object_attributes": {
                "iid": 3,
                "title": f"{issue['key']} 정리",
                "description": "",
                "source_branch": "fix",
                "url": "https://gitlab.example.com/acme/gl/-/merge_requests/3",
                "updated_at": "2026-09-01T02:00:00Z",
            },
            "user": {"username": "sujin"},
        }
        got = await app_client.post(
            repository["webhook_path"],
            content=json.dumps(payload).encode(),
            headers={
                "X-Gitlab-Event": "Merge Request Hook",
                "X-Gitlab-Token": repository["secret"],
            },
        )
        assert got.status_code == 200, got.text
        assert got.json() == {"received": 1, "linked": 1}
        links = await app_client.get(
            f"{BASE}/repositories/links?issue_id={issue['id']}", headers=headers
        )
        (link,) = links.json()
        assert (link["kind"], link["external_ref"], link["provider"]) == (
            "pull_request",
            "3",
            "gitlab",
        )

    async def test_a_wrong_token_is_refused(self, app_client: httpx.AsyncClient) -> None:
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers, "GLW")
        repository = await _register(
            app_client, headers, [project["id"]], provider="gitlab", name="acme/glw"
        )
        refused = await app_client.post(
            repository["webhook_path"],
            content=b"{}",
            headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "nope"},
        )
        assert refused.status_code == 401, refused.text

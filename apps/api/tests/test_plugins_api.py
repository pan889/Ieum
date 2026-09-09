"""앱을 HTTP 로 몰아 본다 (M6 "플러그인 훅").

서비스 시험이 규칙을 이미 붙잡고 있다. 여기서 보는 것은 배선이고, 이
기능에서 배선이 끊기는 자리가 넷이다:

- **고정 경로가 `{app_id}` 형제보다 위에 있는가.** `/apps/slots` 가 아래로
  내려가면 `slots` 가 앱 id 로 잡혀 UUID 파싱 오류가 난다.
- **정말 저장되는가.** 라우터가 커밋을 빠뜨려도 서비스 시험은 통과한다
  (픽스처가 트랜잭션을 들고 있다).
- **표면이 둘로 갈려 있는가.** 앱 토큰으로 사람용 엔드포인트를 열 수 없어야
  하고, 사람의 토큰으로 앱 자리를 채울 수 없어야 한다. 섞이면 조용하다 —
  통해 버리면 아무 오류도 안 난다.
- **토큰이 한 번만 나오는가.** 목록에는 앞머리만 실려야 한다.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pyotp
import pytest

from ieum.core.time import utcnow

pytestmark = pytest.mark.integration

BASE = "/api/v1"
ADMIN = ("admin@example.com", "seed-admin-password-1234")


async def _plain(client: httpx.AsyncClient) -> dict[str, str]:
    """2FA 를 통과하지 **않은** 관리자. step-up 이 거절하는지 볼 때 쓴다."""
    r = await client.post(f"{BASE}/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _auth(client: httpx.AsyncClient) -> dict[str, str]:
    """step-up 을 통과하는 관리자. **2FA 를 실제로 등록하고 통과한다** —
    앱 등록이 step-up 대상이기 때문이다 (`plugins/permissions.py`).

    vcs 의 저장소 등록 시험과 같은 절차다. 앱 등록도 "화면 안에 남의 글을
    놓는 자리를 내주는 일" 이라 같은 무게로 뒀다.
    """
    headers = await _plain(client)

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
    verified = {"Authorization": f"Bearer {pending.json()['access_token']}"}
    # 등록 확인에 쓴 코드는 재사용 방지에 걸린다. 다음 스텝의 코드를 쓴다.
    code = pyotp.TOTP(secret).at(utcnow() + timedelta(seconds=30))
    done = await client.post(f"{BASE}/auth/mfa/verify", headers=verified, json={"code": code})
    assert done.status_code == 204, done.text
    return verified


async def _app(
    client: httpx.AsyncClient, headers: dict[str, str], slug: str, **over: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "CI 봇", "slug": slug, **over}
    made = await client.post(f"{BASE}/apps", json=body, headers=headers)
    assert made.status_code == 201, made.text
    return dict(made.json())


async def _issue(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    """프로젝트를 직접 만든다 — 시드에는 프로젝트가 없다."""
    key = f"P{uuid4().hex[-5:].upper()}"
    made_project = await client.post(
        f"{BASE}/projects", json={"key": key, "name": f"{key} 프로젝트"}, headers=headers
    )
    assert made_project.status_code == 201, made_project.text
    project = made_project.json()
    types = await client.get(f"{BASE}/issues/types?project_id={project['id']}", headers=headers)
    assert types.status_code == 200, types.text
    made = await client.post(
        f"{BASE}/issues",
        json={
            "project_id": project["id"],
            "type_id": types.json()[0]["id"],
            "summary": "빌드가 깨졌다",
        },
        headers=headers,
    )
    assert made.status_code == 201, made.text
    return dict(made.json())


class TestStepUp:
    async def test_registering_needs_step_up(self, app_client: httpx.AsyncClient) -> None:
        """**앱 등록은 몰래 되지 않는다.**

        세션을 훔친 사람이 앱 하나를 등록해 두면 그 뒤로 모든 이슈에 그 글이
        실린다 — 웹훅·저장소 등록과 같은 무게다.
        """
        headers = await _plain(app_client)
        refused = await app_client.post(
            f"{BASE}/apps", json={"name": "몰래", "slug": "sneaky"}, headers=headers
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "auth.step_up_requires_mfa"

    async def test_reading_the_list_does_not(self, app_client: httpx.AsyncClient) -> None:
        """보는 것은 step-up 이 아니다. 목록을 보려고 2FA 를 다시 묻는 것은
        과하고, 앞머리 말고는 비밀이 없다."""
        headers = await _plain(app_client)
        listed = await app_client.get(f"{BASE}/apps", headers=headers)
        assert listed.status_code == 200, listed.text


class TestWiring:
    async def test_the_fixed_path_wins_over_the_id_sibling(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """`/apps/slots` 가 `/{app_id}` 아래로 내려가면 여기서 422 가 난다."""
        headers = await _auth(app_client)
        listed = await app_client.get(f"{BASE}/apps/slots", headers=headers)
        assert listed.status_code == 200, listed.text
        names = {row["name"] for row in listed.json()}
        assert {"issue.panel", "issue.link", "settings.link"} <= names

    async def test_an_app_survives_the_round_trip(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        issued = await _app(app_client, headers, "ci-round")
        assert issued["token"].startswith("ieum_app_")

        listed = await app_client.get(f"{BASE}/apps", headers=headers)
        assert listed.status_code == 200, listed.text
        found = next(row for row in listed.json() if row["slug"] == "ci-round")
        # **토큰은 목록에 없다.** 앞머리만 남는다.
        assert "token" not in found
        assert found["token_prefix"] in issued["token"]

    async def test_the_token_is_not_in_the_detail_either(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = await _auth(app_client)
        issued = await _app(app_client, headers, "ci-detail")
        one = await app_client.get(f"{BASE}/apps/{issued['app']['id']}", headers=headers)
        assert one.status_code == 200, one.text
        assert "token" not in one.json()

    async def test_a_dangerous_link_is_refused_at_the_edge(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """검사가 서비스에만 있고 라우터가 다른 길로 쓰면 여기서 통과한다."""
        headers = await _auth(app_client)
        issued = await _app(app_client, headers, "ci-danger")
        bad = await app_client.post(
            f"{BASE}/apps/{issued['app']['id']}/slots",
            json={
                "slot": "issue.link",
                "kind": "link",
                "label": "누르면",
                "url_template": "javascript:alert(1)",
            },
            headers=headers,
        )
        assert bad.status_code == 422, bad.text
        assert bad.json()["error"]["code"] == "plugins.url_scheme"

    async def test_removing_really_removes(self, app_client: httpx.AsyncClient) -> None:
        """라우터가 커밋을 빠뜨리면 여기서 잡힌다."""
        headers = await _auth(app_client)
        issued = await _app(app_client, headers, "ci-bye")
        gone = await app_client.delete(f"{BASE}/apps/{issued['app']['id']}", headers=headers)
        assert gone.status_code == 204, gone.text
        again = await app_client.get(f"{BASE}/apps/{issued['app']['id']}", headers=headers)
        assert again.status_code == 404


class TestTwoSurfaces:
    async def test_the_app_writes_with_its_own_token(self, app_client: httpx.AsyncClient) -> None:
        """앱이 자기 토큰으로 이슈 하나의 자기 패널에 글을 쓴다."""
        headers = await _auth(app_client)
        issue = await _issue(app_client, headers)
        issued = await _app(app_client, headers, "ci-writes")
        placed = await app_client.post(
            f"{BASE}/apps/{issued['app']['id']}/slots",
            json={"slot": "issue.panel", "kind": "panel", "label": "빌드 상태"},
            headers=headers,
        )
        assert placed.status_code == 201, placed.text

        wrote = await app_client.put(
            f"{BASE}/apps/self/panels",
            json={"issue_key": issue["key"], "label": "빌드 상태", "body": "빌드 **통과**"},
            headers={"Authorization": f"Bearer {issued['token']}"},
        )
        assert wrote.status_code == 204, wrote.text

        # 사람이 이슈 화면에서 그 글을 읽는다.
        seen = await app_client.get(
            f"{BASE}/apps/contributions/issue/{issue['id']}", headers=headers
        )
        assert seen.status_code == 200, seen.text
        assert [row["body"] for row in seen.json()] == ["빌드 **통과**"]

    async def test_a_human_token_cannot_write_a_panel(self, app_client: httpx.AsyncClient) -> None:
        """**표면이 갈려 있다.** 사람의 액세스 토큰으로는 앱 칸을 못 채운다."""
        headers = await _auth(app_client)
        issue = await _issue(app_client, headers)
        refused = await app_client.put(
            f"{BASE}/apps/self/panels",
            json={"issue_key": issue["key"], "label": "빌드", "body": "내가 쓴다"},
            headers=headers,
        )
        assert refused.status_code == 401, refused.text

    async def test_an_app_token_cannot_open_the_human_surface(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """반대 방향. 앱 토큰으로 앱 목록을 읽을 수 없다 — 읽히면 앱 하나가
        다른 앱의 자리와 앞머리를 훑을 수 있다."""
        headers = await _auth(app_client)
        issued = await _app(app_client, headers, "ci-nosy")
        refused = await app_client.get(
            f"{BASE}/apps", headers={"Authorization": f"Bearer {issued['token']}"}
        )
        assert refused.status_code == 401, refused.text

    async def test_no_token_at_all_is_refused(self, app_client: httpx.AsyncClient) -> None:
        refused = await app_client.put(
            f"{BASE}/apps/self/panels",
            json={"issue_key": "NOPE-1", "label": "빌드", "body": "글"},
        )
        assert refused.status_code == 401, refused.text

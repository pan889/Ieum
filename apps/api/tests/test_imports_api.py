"""이관 미리 보기를 HTTP 로 몰아 본다.

**이 화면이 존재하는 이유는 "적재를 누르기 전에 알게 하는 것"** 이다. 그래서
여기서 보는 것은 개수가 아니라 **못 옮기는 것들이 실제로 응답에 실리는가** 다.

시드가 주는 어휘를 그대로 쓴다(유형 Task·Bug·Story·Sub-task, 상태 Open·
In Progress·Resolved·Closed). Redmine 의 `New` 는 그 어디에도 없다 — 이관에서
가장 흔한 모양이 바로 그것이고, 그때 무슨 일이 일어나는지가 이 파일의 주제다.
"""

from __future__ import annotations

import json
import secrets
from datetime import timedelta
from typing import Any

import httpx
import pyotp
import pytest

from ieum.core.time import utcnow
from ieum.migrate.archive import (
    Archive,
    Comment,
    Issue,
    Manifest,
    Person,
    Project,
    Relation,
    Source,
    write_archive,
)

pytestmark = pytest.mark.integration

BASE = "/api/v1"
ADMIN = ("admin@example.com", "seed-admin-password-1234")


async def _admin_headers(client: httpx.AsyncClient) -> dict[str, str]:
    """step-up 을 통과한 관리자. 이관은 step-up 대상이다(`imports/permissions.py`)."""
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
    verified = {"Authorization": f"Bearer {pending.json()['access_token']}"}
    code = pyotp.TOTP(secret).at(utcnow() + timedelta(seconds=30))
    done = await client.post(f"{BASE}/auth/mfa/verify", headers=verified, json={"code": code})
    assert done.status_code == 204, done.text
    return verified


async def _login_only(client: httpx.AsyncClient) -> dict[str, str]:
    r = await client.post(f"{BASE}/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _project(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    key = "IM" + secrets.token_hex(2).upper()
    made = await client.post(
        f"{BASE}/projects", json={"key": key, "name": f"{key} 이관"}, headers=headers
    )
    assert made.status_code == 201, made.text
    return dict(made.json())


def _archive(**kwargs: Any) -> bytes:
    """Redmine 어댑터가 뽑아 놓은 것과 같은 모양의 묶음."""
    issues = kwargs.pop(
        "issues",
        [
            Issue(
                source_id="1",
                summary="첫 이슈",
                type="Bug",
                status="New",
                priority="Normal",
                author="u1",
                comments=[Comment(source_id="c1", body="댓글", created_at="2026-01-02T00:00:00Z")],
            ),
            Issue(source_id="2", summary="둘째", type="Bug", status="Closed", priority="High"),
        ],
    )
    people = kwargs.pop(
        "people",
        [
            Person(source_id="u1", name="하나 김", email="hana@example.com"),
            Person(source_id="u2", name="이름만", email=""),
            Person(source_id="u3", name="관리자", email=ADMIN[0]),
        ],
    )
    return write_archive(
        Archive(
            manifest=Manifest(
                source=Source(kind="redmine", base_url="https://redmine.example.com"),
                project=Project(key="my-project", name="옮겨 올 프로젝트"),
                taken_at="2026-09-12T00:00:00Z",
                adapter="ieum-migrate-redmine/1",
            ),
            people=people,
            issues=issues,
        )
    )


async def _preview(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    project_id: str,
    data: bytes,
    overrides: dict[str, Any] | None = None,
) -> httpx.Response:
    form: dict[str, str] = {"project_id": project_id}
    if overrides is not None:
        form["overrides"] = json.dumps(overrides)
    return await client.post(
        f"{BASE}/imports/preview",
        data=form,
        files={"file": ("archive.zip", data, "application/zip")},
        headers=headers,
    )


class TestWhatItSaysBeforeAnythingMoves:
    async def test_it_counts_what_is_in_the_bundle(self, app_client: httpx.AsyncClient) -> None:
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers)

        r = await _preview(app_client, headers, project["id"], _archive())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["counted"] == {
            "issues": 2,
            "comments": 1,
            "people": 3,
            # 아무것도 안 옮겼으니 0. 이 값이 이 화면의 멱등 표시다.
            "already_here": 0,
        }
        assert body["source_kind"] == "redmine"
        assert body["project_name"] == "옮겨 올 프로젝트"

    async def test_a_word_we_do_not_have_blocks_the_load(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """**이 시험이 이 파일의 이유다.** Redmine 의 `New` 는 우리에게 없다.

        조용히 아무 상태로나 넣으면 이관은 "성공" 하고, 옮긴 사람은 몇 달 뒤에
        전부 엉뚱한 칸에 있는 것을 본다.
        """
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers)

        body = (await _preview(app_client, headers, project["id"], _archive())).json()
        assert body["can_load"] is False
        assert body["blocking"] == ["상태: New"]

        statuses = {m["source"]: m for m in body["statuses"]}
        assert statuses["New"]["how"] == "unmatched"
        assert statuses["Closed"]["how"] == "name"

    async def test_the_person_can_pair_it_and_then_it_can_load(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers)

        first = (await _preview(app_client, headers, project["id"], _archive())).json()
        open_state = next(c for c in first["status_choices"] if c["name"] == "Open")

        second = (
            await _preview(
                app_client,
                headers,
                project["id"],
                _archive(),
                {"statuses": {"New": open_state["id"]}},
            )
        ).json()
        assert second["can_load"] is True
        assert next(m for m in second["statuses"] if m["source"] == "New")["how"] == "override"

    async def test_subtask_types_are_not_offered(self, app_client: httpx.AsyncClient) -> None:
        """부모 없는 이슈를 하위작업 유형에 넣으면 만들어지는 순간 규칙에 걸린다.

        그 실패는 **이관 도중에** 나타나서 절반만 들어온 상태를 남긴다.
        """
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers)

        body = (await _preview(app_client, headers, project["id"], _archive())).json()
        names = {c["name"] for c in body["type_choices"]}
        assert "Bug" in names
        assert "Sub-task" not in names

    async def test_known_priority_names_are_matched_by_name(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """묶음이 쓰는 이름(Normal·High)은 아는 이름이라 순서로 짐작하지 않는다.

        예전에는 나온 순서로 폈고, 그 순서는 소스가 정한 것이 아니라 이슈가
        나온 순서였다 — 첫 이슈가 High 면 High 가 가장 낮은 자리로 갔다.
        """
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers)

        body = (await _preview(app_client, headers, project["id"], _archive())).json()
        by_source = {m["source"]: m for m in body["priorities"]}
        assert by_source["Normal"]["target_id"] == "3"
        assert by_source["High"]["target_id"] == "2"
        assert {m["how"] for m in body["priorities"]} == {"name"}
        # 우선순위는 못 이어도 이슈를 막지 않는다.
        assert "우선순위" not in " ".join(body["blocking"])


class TestPeople:
    async def test_it_says_why_each_person_is_unlinked(self, app_client: httpx.AsyncClient) -> None:
        """**둘은 고치는 방법이 다르다** — 소스에 메일이 없는 것과, 우리 쪽에
        계정이 없는 것. 하나로 묶어 보여 주면 무엇을 해야 하는지 모른다."""
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers)

        body = (await _preview(app_client, headers, project["id"], _archive())).json()
        by_id = {p["source_id"]: p for p in body["people"]}
        assert by_id["u3"]["reason"] == ""
        assert by_id["u3"]["user_id"] != ""
        assert by_id["u2"]["reason"] == "no_email"
        assert by_id["u1"]["reason"] == "unknown_email"


class TestWhatGetsDropped:
    async def test_a_parent_outside_the_bundle_is_reported(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """어댑터는 프로젝트 하나만 뽑는다. 다른 프로젝트의 부모는 여기 없다 —
        버리는 것은 맞지만 **몇 개를 버렸는지 말하지 않으면** 아무도 모른다."""
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers)
        data = _archive(
            issues=[Issue(source_id="9", summary="고아", type="Bug", status="Closed", parent="99")]
        )

        body = (await _preview(app_client, headers, project["id"], data)).json()
        assert body["dangling_parents"] == ["9"]

    async def test_an_unknown_relation_kind_is_named_not_swallowed(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers)
        data = _archive(
            issues=[
                Issue(
                    source_id="9",
                    summary="관계",
                    type="Bug",
                    status="Closed",
                    relations=[Relation(kind="follows", target="10")],
                )
            ]
        )

        body = (await _preview(app_client, headers, project["id"], data)).json()
        assert body["unknown_relation_kinds"] == ["follows"]


class TestTheDoor:
    async def test_a_broken_bundle_says_what_is_wrong(self, app_client: httpx.AsyncClient) -> None:
        """ "묶음이 잘못됐다" 만 보여 주면 관리자가 아무것도 못 한다."""
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers)

        r = await _preview(app_client, headers, project["id"], b"not a zip at all")
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "imports.unreadable_archive"
        assert r.json()["error"]["message"].strip()

    async def test_unreadable_pairings_are_refused_not_ignored(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """짝을 버리고 미리 보기를 보여 주면, 그 화면을 믿고 적재를 누른다."""
        headers = await _admin_headers(app_client)
        project = await _project(app_client, headers)

        r = await app_client.post(
            f"{BASE}/imports/preview",
            data={"project_id": project["id"], "overrides": "{broken"},
            files={"file": ("a.zip", _archive(), "application/zip")},
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "imports.bad_overrides"

    async def test_a_session_that_has_not_passed_two_factor_is_refused(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """**이것은 step-up 시험이 아니다.**

        2FA 를 등록한 계정으로 그냥 로그인하면 그 토큰은 **모든 경로에서**
        403 이다(재어 봤다: `/projects` 도 `/notifications` 도 그렇다). 그래서
        여기서 403 을 봐도 step-up 이 이유인지 알 수 없다 — 처음에 이 시험을
        "step-up 을 요구한다" 로 써 놨다가, `requires_step_up` 을 빼도 초록인
        것을 보고 알았다.

        step-up 자체는 `test_imports_service.py` 가 고정한다. 여기 남긴 것은
        그보다 앞의 문이다: 반쯤 로그인한 세션은 여기 못 들어온다.
        """
        strong = await _admin_headers(app_client)
        project = await _project(app_client, strong)

        weak = await _login_only(app_client)
        r = await _preview(app_client, weak, project["id"], _archive())
        assert r.status_code == 403, r.text

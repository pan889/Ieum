"""SCIM 표면을 IdP 처럼 두드린다 (RFC 7644).

`test_identity_scim.py` 는 값 다루기를 손으로 못 박았다. 여기는 **실제 앱에
HTTP 로** 붙어, IdP 가 보내는 순서 그대로 돌려 본다: 있는지 물어보고,
만들고, 그룹에 넣고, 비활성화한다.

붙잡는 것 — 첫째가 이 파일의 이유다:

- **자기가 밀어 넣은 것만 만진다.** IdP 가 둘일 때 한쪽의 토큰으로 다른 쪽
  계정을 비활성화할 수 있으면, 프로비저닝은 조용히 도는 일이라 며칠 뒤에나
  발견된다. 사람이 초대한 계정도 SCIM 의 소유가 아니다.
- **오류가 SCIM 봉투로 나간다.** 우리 봉투를 주면 IdP 는 인증 실패인지 서버
  오류인지 구별하지 못하고 재시도 고리에 빠진다.
- **비활성화는 삭제가 아니다.** `DELETE` 도 마찬가지다 — 행을 지우면 그
  사람이 쓴 글의 작성자가 사라진다.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ieum.core.crypto import hash_token
from ieum.core.ids import new_id, new_token
from ieum.modules.identity.models import IdentityProvider, User

PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"


def _provider_row(token: str, *, name: str = "Okta") -> IdentityProvider:
    return IdentityProvider(
        name=name,
        kind="saml",
        issuer=f"https://idp.example/{new_id().hex[-8:]}",
        authorization_endpoint="https://idp.example/sso",
        saml_certificates=["-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"],
        scim_enabled=True,
        scim_token_hash=hash_token(token),
    )


@pytest_asyncio.fixture
async def scim_token(engine: object) -> str:
    """프로비저닝을 켠 IdP 하나와 그 토큰.

    `app_client` 와 같은 엔진에 직접 넣는다 — 이 표면에는 관리 API 가 아직
    없고, 있어도 그건 다른 시험의 일이다.
    """
    token = new_token(16)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]
    async with factory() as s:
        s.add(_provider_row(token))
        await s.commit()
    return token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_user(
    client: httpx.AsyncClient, token: str, *, user_name: str, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {"schemas": [USER_SCHEMA], "userName": user_name, **extra}
    found = await client.post("/scim/v2/Users", json=body, headers=auth(token))
    assert found.status_code == 201, found.text
    return dict(found.json())


class TestTheTokenIsTheDoor:
    async def test_no_token_is_refused_in_scim_shape(self, app_client: httpx.AsyncClient) -> None:
        found = await app_client.get("/scim/v2/Users")
        assert found.status_code == 401
        body = found.json()
        # 우리 `{"error": {...}}` 봉투가 아니다.
        assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
        assert body["status"] == "401"
        assert found.headers["content-type"].startswith("application/scim+json")

    async def test_a_wrong_token_is_refused(self, app_client: httpx.AsyncClient) -> None:
        found = await app_client.get("/scim/v2/Users", headers=auth("nope"))
        assert found.status_code == 401

    async def test_a_disabled_provider_is_a_closed_door(
        self, app_client: httpx.AsyncClient, engine: object, scim_token: str
    ) -> None:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]
        async with factory() as s:
            row = (
                await s.execute(
                    select(IdentityProvider).where(
                        IdentityProvider.scim_token_hash == hash_token(scim_token)
                    )
                )
            ).scalar_one()
            row.is_enabled = False
            await s.commit()
        found = await app_client.get("/scim/v2/Users", headers=auth(scim_token))
        assert found.status_code == 401

    async def test_it_says_what_it_supports(self, app_client: httpx.AsyncClient) -> None:
        """Entra 는 이걸 읽고 무엇을 보낼지 정한다. 열려 있어야 한다."""
        found = await app_client.get("/scim/v2/ServiceProviderConfig")
        assert found.status_code == 200
        assert found.json()["bulk"]["supported"] is False


class TestTheProvisioningRun:
    async def test_the_order_an_idp_actually_uses(
        self, app_client: httpx.AsyncClient, scim_token: str
    ) -> None:
        email = f"scim-{new_id().hex[-8:]}@example.com"

        # 1. 있는지 물어본다. 없으면 빈 목록이지 오류가 아니다.
        found = await app_client.get(
            "/scim/v2/Users", params={"filter": f'userName eq "{email}"'}, headers=auth(scim_token)
        )
        assert found.status_code == 200
        assert found.json()["totalResults"] == 0

        # 2. 만든다.
        created = await _create_user(
            app_client, scim_token, user_name=email, displayName="가나다", externalId="okta-1"
        )
        assert created["userName"] == email
        assert created["active"] is True
        assert created["meta"]["location"] == f"/scim/v2/Users/{created['id']}"

        # 3. 다시 물어보면 이제 있다.
        found = await app_client.get(
            "/scim/v2/Users", params={"filter": f'userName eq "{email}"'}, headers=auth(scim_token)
        )
        assert found.json()["totalResults"] == 1
        assert found.json()["Resources"][0]["id"] == created["id"]

        # 4. 그룹을 만들고 넣는다.
        group = await app_client.post(
            "/scim/v2/Groups",
            json={"schemas": [GROUP_SCHEMA], "displayName": f"팀-{new_id().hex[-6:]}"},
            headers=auth(scim_token),
        )
        assert group.status_code == 201, group.text
        group_id = group.json()["id"]

        patched = await app_client.patch(
            f"/scim/v2/Groups/{group_id}",
            json={
                "schemas": [PATCH_SCHEMA],
                "Operations": [
                    {"op": "add", "path": "members", "value": [{"value": created["id"]}]}
                ],
            },
            headers=auth(scim_token),
        )
        assert patched.status_code == 200, patched.text
        assert [m["value"] for m in patched.json()["members"]] == [created["id"]]

        # 5. 사람을 뺀다 (Okta 의 모양).
        removed = await app_client.patch(
            f"/scim/v2/Groups/{group_id}",
            json={
                "schemas": [PATCH_SCHEMA],
                "Operations": [{"op": "remove", "path": f'members[value eq "{created["id"]}"]'}],
            },
            headers=auth(scim_token),
        )
        assert removed.json()["members"] == []

        # 6. 퇴사 (Entra 의 모양 — 경로가 없다).
        off = await app_client.patch(
            f"/scim/v2/Users/{created['id']}",
            json={
                "schemas": [PATCH_SCHEMA],
                "Operations": [{"op": "replace", "value": {"active": False}}],
            },
            headers=auth(scim_token),
        )
        assert off.status_code == 200, off.text
        assert off.json()["active"] is False

    async def test_creating_the_same_person_twice_is_a_conflict(
        self, app_client: httpx.AsyncClient, scim_token: str
    ) -> None:
        """`409 uniqueness` 가 아니면 IdP 는 재시도 고리에 빠진다."""
        email = f"dup-{new_id().hex[-8:]}@example.com"
        await _create_user(app_client, scim_token, user_name=email)
        again = await app_client.post(
            "/scim/v2/Users",
            json={"schemas": [USER_SCHEMA], "userName": email},
            headers=auth(scim_token),
        )
        assert again.status_code == 409
        assert again.json()["scimType"] == "uniqueness"

    async def test_it_collides_with_a_locally_invited_account_too(
        self, app_client: httpx.AsyncClient, engine: object, scim_token: str
    ) -> None:
        """자기 것만 보면 DB 의 unique 에서 터지고, 그때는 500 이 나간다."""
        email = f"local-{new_id().hex[-8:]}@example.com"
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]
        async with factory() as s:
            s.add(User(email=email, display_name="사람이 초대함", status="active"))
            await s.commit()

        found = await app_client.post(
            "/scim/v2/Users",
            json={"schemas": [USER_SCHEMA], "userName": email},
            headers=auth(scim_token),
        )
        assert found.status_code == 409
        assert found.json()["scimType"] == "uniqueness"

    async def test_a_filter_we_cannot_run_is_refused_not_emptied(
        self, app_client: httpx.AsyncClient, scim_token: str
    ) -> None:
        """빈 목록을 주면 IdP 는 "없다" 로 읽고 계정을 또 만든다."""
        found = await app_client.get(
            "/scim/v2/Users", params={"filter": 'userName co "a"'}, headers=auth(scim_token)
        )
        assert found.status_code == 400
        assert found.json()["scimType"] == "invalidFilter"

    async def test_deleting_a_user_deactivates_them(
        self, app_client: httpx.AsyncClient, engine: object, scim_token: str
    ) -> None:
        """행을 지우면 그 사람이 쓴 글의 작성자가 사라진다."""
        created = await _create_user(
            app_client, scim_token, user_name=f"gone-{new_id().hex[-8:]}@example.com"
        )
        found = await app_client.delete(f"/scim/v2/Users/{created['id']}", headers=auth(scim_token))
        assert found.status_code == 204

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]
        async with factory() as s:
            row = await s.get(User, created["id"])
            assert row is not None
            assert row.status == "suspended"

    async def test_a_group_put_replaces_the_whole_membership(
        self, app_client: httpx.AsyncClient, scim_token: str
    ) -> None:
        """`members` 가 없으면 비운다 — 안 비우면 IdP 에서 뺀 사람이 남는다."""
        member = await _create_user(
            app_client, scim_token, user_name=f"m-{new_id().hex[-8:]}@example.com"
        )
        name = f"팀-{new_id().hex[-6:]}"
        group = await app_client.post(
            "/scim/v2/Groups",
            json={
                "schemas": [GROUP_SCHEMA],
                "displayName": name,
                "members": [{"value": member["id"]}],
            },
            headers=auth(scim_token),
        )
        assert len(group.json()["members"]) == 1

        replaced = await app_client.put(
            f"/scim/v2/Groups/{group.json()['id']}",
            json={"schemas": [GROUP_SCHEMA], "displayName": name},
            headers=auth(scim_token),
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["members"] == []

    async def test_an_unknown_member_is_refused(
        self, app_client: httpx.AsyncClient, scim_token: str
    ) -> None:
        """조용히 건너뛰면 그 사람은 그룹의 권한을 못 받고, 아무 데도 안 남는다."""
        group = await app_client.post(
            "/scim/v2/Groups",
            json={"schemas": [GROUP_SCHEMA], "displayName": f"팀-{new_id().hex[-6:]}"},
            headers=auth(scim_token),
        )
        found = await app_client.patch(
            f"/scim/v2/Groups/{group.json()['id']}",
            json={
                "schemas": [PATCH_SCHEMA],
                "Operations": [
                    {"op": "add", "path": "members", "value": [{"value": str(new_id())}]}
                ],
            },
            headers=auth(scim_token),
        )
        assert found.status_code == 400
        assert found.json()["scimType"] == "invalidValue"


class TestOnlyItsOwn:
    @pytest_asyncio.fixture
    async def other_token(self, engine: object) -> str:
        token = new_token(16)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]
        async with factory() as s:
            s.add(_provider_row(token, name="Entra"))
            await s.commit()
        return token

    async def test_another_idp_cannot_see_or_touch(
        self, app_client: httpx.AsyncClient, scim_token: str, other_token: str
    ) -> None:
        """**이 파일의 자물쇠다.** 협력사 IdP 가 우리 사람을 끄면 안 된다."""
        mine = await _create_user(
            app_client, scim_token, user_name=f"mine-{new_id().hex[-8:]}@example.com"
        )

        # 없는 것과 남의 것을 같게 답한다 — 다르게 답하면 토큰 하나로
        # "이 id 가 존재하는가" 를 물어볼 수 있다.
        for method, kwargs in (
            ("get", {}),
            ("put", {"json": {"schemas": [USER_SCHEMA], "userName": "x@y.z"}}),
            (
                "patch",
                {
                    "json": {
                        "schemas": [PATCH_SCHEMA],
                        "Operations": [{"op": "replace", "path": "active", "value": False}],
                    }
                },
            ),
            ("delete", {}),
        ):
            found = await getattr(app_client, method)(
                f"/scim/v2/Users/{mine['id']}", headers=auth(other_token), **kwargs
            )
            assert found.status_code == 404, f"{method}: {found.text}"

        # 목록에도 안 나온다.
        listed = await app_client.get("/scim/v2/Users", headers=auth(other_token))
        assert [r["id"] for r in listed.json()["Resources"]] == []

    async def test_a_locally_invited_account_is_not_scims_to_touch(
        self, app_client: httpx.AsyncClient, engine: object, scim_token: str
    ) -> None:
        """IdP 의 디렉터리에서 사라졌다고 관리자 계정이 잠기면 안 된다."""
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]
        async with factory() as s:
            row = User(
                email=f"admin-{new_id().hex[-8:]}@example.com",
                display_name="사람이 초대함",
                status="active",
            )
            s.add(row)
            await s.commit()
            user_id = row.id

        found = await app_client.delete(f"/scim/v2/Users/{user_id}", headers=auth(scim_token))
        assert found.status_code == 404

        async with factory() as s:
            still = await s.get(User, user_id)
            assert still is not None
            assert still.status == "active"


class TestBooleansThatArriveAsText:
    @pytest.mark.parametrize("sent", ["false", "False"])
    async def test_a_string_false_deactivates(
        self, app_client: httpx.AsyncClient, scim_token: str, sent: str
    ) -> None:
        """파이썬 진리값으로 읽으면 비활성화가 활성화가 된다."""
        created = await _create_user(
            app_client, scim_token, user_name=f"txt-{new_id().hex[-8:]}@example.com"
        )
        found = await app_client.patch(
            f"/scim/v2/Users/{created['id']}",
            json={
                "schemas": [PATCH_SCHEMA],
                "Operations": [{"op": "replace", "path": "active", "value": sent}],
            },
            headers=auth(scim_token),
        )
        assert found.status_code == 200, found.text
        assert found.json()["active"] is False

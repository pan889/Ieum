"""identity 통합 테스트. 라우터·의존성·권한 배선까지 함께 본다.

auth.md 7절의 "테스트 필수 항목"을 여기서 고정한다.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

import httpx
import pyotp
import pytest

from ieum.core.time import utcnow

pytestmark = pytest.mark.integration

BASE = "/api/v1"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "seed-admin-password-1234"


@pytest.fixture(autouse=True, scope="session")
def _seed_env() -> None:
    os.environ.setdefault("SEED_ADMIN_EMAIL", ADMIN_EMAIL)
    os.environ.setdefault("SEED_ADMIN_PASSWORD", ADMIN_PASSWORD)


async def _login(client: httpx.AsyncClient) -> dict[str, Any]:
    r = await client.post(
        f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


def _auth(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _enroll_totp(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    r = await client.post(f"{BASE}/auth/mfa/totp/enroll", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    confirm = await client.post(
        f"{BASE}/auth/mfa/totp/{body['credential_id']}/confirm",
        headers=headers,
        json={"code": pyotp.TOTP(body["secret"]).now()},
    )
    assert confirm.status_code == 204, confirm.text
    return str(body["secret"])


class TestLogin:
    async def test_success(self, app_client: httpx.AsyncClient) -> None:
        client = app_client
        tokens = await _login(client)
        assert tokens["access_token"] and tokens["refresh_token"]
        assert tokens["expires_in"] == 900

    async def test_wrong_password_and_unknown_user_are_indistinguishable(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """사용자 열거 방지: 두 경우의 응답이 같아야 한다 (auth.md 2절)."""
        client = app_client
        bad_password = await client.post(
            f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"}
        )
        unknown = await client.post(
            f"{BASE}/auth/login",
            json={"email": "nobody@example.com", "password": "wrong-password"},
        )
        assert bad_password.status_code == unknown.status_code == 401
        assert bad_password.json()["error"]["code"] == unknown.json()["error"]["code"]
        assert bad_password.json()["error"]["message"] == unknown.json()["error"]["message"]

    async def test_error_body_shape(self, app_client: httpx.AsyncClient) -> None:
        """에러 포맷은 계약이다 (CLAUDE.md 8절)."""
        client = app_client
        r = await client.post(
            f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": "nope-nope-nope"}
        )
        error = r.json()["error"]
        assert set(error) == {"code", "message", "details", "trace_id"}
        assert error["trace_id"]
        # 설계 주석이 응답으로 새면 안 된다.
        assert "\n" not in error["message"]


class TestAccessControl:
    async def test_no_token_rejected(self, app_client: httpx.AsyncClient) -> None:
        client = app_client
        assert (await client.get(f"{BASE}/auth/me")).status_code == 401

    async def test_garbage_token_rejected(self, app_client: httpx.AsyncClient) -> None:
        client = app_client
        r = await client.get(f"{BASE}/auth/me", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401

    async def test_revoked_session_is_immediately_dead(self, app_client: httpx.AsyncClient) -> None:
        """즉시 무효화가 되는지 — JWT 를 세션으로 쓰지 않는 이유다."""
        client = app_client
        headers = _auth(await _login(client))
        assert (await client.get(f"{BASE}/auth/me", headers=headers)).status_code == 200
        assert (await client.delete(f"{BASE}/auth/sessions", headers=headers)).status_code == 204
        assert (await client.get(f"{BASE}/auth/me", headers=headers)).status_code == 401


class TestRefreshRotation:
    async def test_rotation_issues_new_tokens(self, app_client: httpx.AsyncClient) -> None:
        client = app_client
        tokens = await _login(client)
        r = await client.post(
            f"{BASE}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert r.status_code == 200
        assert r.json()["refresh_token"] != tokens["refresh_token"]

    async def test_reuse_revokes_entire_family(self, app_client: httpx.AsyncClient) -> None:
        """재사용 감지 시 계열 전체 폐기 (auth.md 7절 필수 항목)."""
        client = app_client
        tokens = await _login(client)
        first = tokens["refresh_token"]

        rotated = await client.post(f"{BASE}/auth/refresh", json={"refresh_token": first})
        assert rotated.status_code == 200
        new_token = rotated.json()["refresh_token"]

        # 이미 쓴 토큰을 다시 쓰면 탈취로 간주한다.
        replay = await client.post(f"{BASE}/auth/refresh", json={"refresh_token": first})
        assert replay.status_code == 401

        # 정상 사용자의 새 토큰도 함께 죽는다 — 그게 의도다.
        after = await client.post(f"{BASE}/auth/refresh", json={"refresh_token": new_token})
        assert after.status_code == 401


class TestMFA:
    async def test_enrollment_and_gate(self, app_client: httpx.AsyncClient) -> None:
        client = app_client
        headers = _auth(await _login(client))
        secret = await _enroll_totp(client, headers)

        # 등록 후 재로그인하면 MFA 게이트가 걸린다.
        pending = await _login(client)
        assert pending["mfa_required"] is True
        pending_headers = _auth(pending)

        blocked = await client.get(f"{BASE}/auth/me", headers=pending_headers)
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "auth.mfa_required"

        # 등록 확인에 쓴 코드와 같은 스텝이면 재사용 방지에 걸린다(의도된 동작).
        # 다음 스텝의 코드로 검증한다 — 허용 범위 ±1 안이라 받아들여진다.
        next_step_code = pyotp.TOTP(secret).at(utcnow() + timedelta(seconds=30))
        verified = await client.post(
            f"{BASE}/auth/mfa/verify",
            headers=pending_headers,
            json={"code": next_step_code},
        )
        assert verified.status_code == 204
        assert (await client.get(f"{BASE}/auth/me", headers=pending_headers)).status_code == 200

    async def test_wrong_confirm_code_rejected(self, app_client: httpx.AsyncClient) -> None:
        client = app_client
        headers = _auth(await _login(client))
        r = await client.post(f"{BASE}/auth/mfa/totp/enroll", headers=headers)
        credential_id = r.json()["credential_id"]
        bad = await client.post(
            f"{BASE}/auth/mfa/totp/{credential_id}/confirm",
            headers=headers,
            json={"code": "000000"},
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "auth.mfa_invalid_code"


class TestMFABypassRegression:
    """MFA 미완료 세션에서 2FA 를 우회할 수 있었던 구멍의 회귀 테스트.

    비밀번호만 아는 공격자가 (a) 백업 코드를 재발급받거나
    (b) 자기 인증기를 새로 등록해서 2FA 를 그대로 통과할 수 있었다.
    강제 등록 흐름을 지원하려고 두 엔드포인트를 미완료 세션에 열어둔 탓이다.
    """

    async def test_pending_session_cannot_reissue_backup_codes(
        self, app_client: httpx.AsyncClient
    ) -> None:
        client = app_client
        headers = _auth(await _login(client))
        await _enroll_totp(client, headers)

        pending = _auth(await _login(client))
        r = await client.post(f"{BASE}/auth/mfa/backup-codes", headers=pending)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "auth.mfa_required"

    async def test_pending_session_cannot_enroll_another_authenticator(
        self, app_client: httpx.AsyncClient
    ) -> None:
        client = app_client
        headers = _auth(await _login(client))
        await _enroll_totp(client, headers)

        pending = _auth(await _login(client))
        r = await client.post(f"{BASE}/auth/mfa/totp/enroll", headers=pending)
        assert r.status_code == 403

    async def test_forced_enrollment_still_works_without_existing_mfa(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """구멍을 막느라 강제 등록 흐름까지 막으면 안 된다."""
        client = app_client
        headers = _auth(await _login(client))
        r = await client.post(f"{BASE}/auth/mfa/totp/enroll", headers=headers)
        assert r.status_code == 200


class TestBackupCodes:
    async def test_single_use(self, app_client: httpx.AsyncClient) -> None:
        client = app_client
        headers = _auth(await _login(client))
        await _enroll_totp(client, headers)
        codes = (await client.post(f"{BASE}/auth/mfa/backup-codes", headers=headers)).json()[
            "codes"
        ]
        assert len(codes) == 10

        pending = _auth(await _login(client))
        first = await client.post(
            f"{BASE}/auth/mfa/verify", headers=pending, json={"code": codes[0]}
        )
        assert first.status_code == 204

        again = await client.post(
            f"{BASE}/auth/mfa/verify", headers=pending, json={"code": codes[0]}
        )
        assert again.status_code == 422

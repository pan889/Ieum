"""identity 통합 테스트. 라우터·의존성·권한 배선까지 함께 본다.

auth.md 7절의 "테스트 필수 항목"을 여기서 고정한다.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pyotp
import pytest

from ieum.config import Settings
from ieum.core.time import utcnow

pytestmark = pytest.mark.integration

BASE = "/api/v1"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "seed-admin-password-1234"
#: 초대받아 만든 임시 계정의 비밀번호. 테스트 전용이다.
INVITED_PASSWORD = "nobody-password-1234"


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


class TestApiTokens:
    """PAT. 발급 시 한 번만 보이고, step-up 은 통과할 수 없다."""

    async def _mfa_headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        tokens = await _login(client)
        headers = _auth(tokens)
        await _enroll_totp(client, headers)
        return headers

    async def test_issue_shows_the_secret_once(self, app_client: httpx.AsyncClient) -> None:
        headers = await self._mfa_headers(app_client)
        created = await app_client.post(
            f"{BASE}/tokens",
            json={"name": "ci", "scopes": ["identity.user.view"]},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["secret"].startswith("ieum_pat_")

        listed = await app_client.get(f"{BASE}/tokens", headers=headers)
        assert listed.status_code == 200, listed.text
        # 목록에는 평문이 없다. 해시만 저장하므로 다시 볼 방법이 없어야 한다.
        assert "secret" not in listed.text
        assert [t["name"] for t in listed.json()] == ["ci"]

    async def test_token_authenticates_within_its_scopes(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = await self._mfa_headers(app_client)
        secret = (
            await app_client.post(
                f"{BASE}/tokens",
                json={"name": "reader", "scopes": ["identity.user.view"]},
                headers=headers,
            )
        ).json()["secret"]
        token_headers = {"Authorization": f"Bearer {secret}"}

        allowed = await app_client.get(f"{BASE}/users", headers=token_headers)
        assert allowed.status_code == 200, allowed.text

    async def test_token_cannot_use_permissions_outside_its_scopes(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """사용자 권한이 넓어도 토큰은 발급 시 고른 것만 쓴다."""
        headers = await self._mfa_headers(app_client)
        secret = (
            await app_client.post(
                f"{BASE}/tokens",
                json={"name": "narrow", "scopes": ["org.project.view"]},
                headers=headers,
            )
        ).json()["secret"]
        token_headers = {"Authorization": f"Bearer {secret}"}

        denied = await app_client.get(f"{BASE}/users", headers=token_headers)
        assert denied.status_code == 403, denied.text

    async def test_token_cannot_issue_another_token(self, app_client: httpx.AsyncClient) -> None:
        """step-up 은 '사람이 방금 MFA 를 통과했다' 는 뜻이다. 토큰은 못 한다.

        막지 않으면 새어 나간 토큰 하나가 토큰을 무한히 찍어낸다.
        """
        headers = await self._mfa_headers(app_client)
        secret = (
            await app_client.post(
                f"{BASE}/tokens",
                json={"name": "seed", "scopes": ["identity.token.issue"]},
                headers=headers,
            )
        ).json()["secret"]

        again = await app_client.post(
            f"{BASE}/tokens",
            json={"name": "child", "scopes": ["identity.user.view"]},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert again.status_code == 403, again.text
        assert again.json()["error"]["code"] == "auth.step_up_not_available_for_token"

    async def test_revoked_token_stops_working(self, app_client: httpx.AsyncClient) -> None:
        headers = await self._mfa_headers(app_client)
        issued = (
            await app_client.post(
                f"{BASE}/tokens",
                json={"name": "temp", "scopes": ["identity.user.view"]},
                headers=headers,
            )
        ).json()
        token_headers = {"Authorization": f"Bearer {issued['secret']}"}
        assert (await app_client.get(f"{BASE}/users", headers=token_headers)).status_code == 200

        revoked = await app_client.delete(f"{BASE}/tokens/{issued['token']['id']}", headers=headers)
        assert revoked.status_code == 204, revoked.text
        # 세션과 같은 원칙이다: 폐기는 즉시 반영된다.
        assert (await app_client.get(f"{BASE}/users", headers=token_headers)).status_code == 401

    async def test_rejects_unknown_scopes(self, app_client: httpx.AsyncClient) -> None:
        """오타 난 스코프를 통과시키면 아무것도 못 하는 토큰이 조용히 생긴다."""
        headers = await self._mfa_headers(app_client)
        r = await app_client.post(
            f"{BASE}/tokens",
            json={"name": "typo", "scopes": ["identity.user.veiw"]},
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "identity.unknown_scope"

    async def test_issuing_requires_enrolled_mfa(self, app_client: httpx.AsyncClient) -> None:
        """2FA 를 등록하지 않은 계정은 토큰을 못 만든다.

        step-up(최근 5분 내 MFA)만으로는 부족하다 — MFA 를 아예 등록하지 않은
        계정은 로그인 자체가 그 창을 채우기 때문이다. PAT 은 만료 없는
        무기명 자격증명이라 비밀번호 하나로 발급되면 안 된다.
        """
        headers = _auth(await _login(app_client))
        r = await app_client.post(
            f"{BASE}/tokens",
            json={"name": "no-mfa", "scopes": ["identity.user.view"]},
            headers=headers,
        )
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "identity.token_requires_mfa"


class TestProfile:
    """언어 설정은 서버가 기억한다.

    브라우저에만 두면 두 가지가 조용히 깨진다: 다른 기기에서 다시 영어로
    열리고, 알림 메일이 옛 언어로 나간다 — 서버는 `user.locale` 로 렌더한다
    (i18n.md 3절).
    """

    async def test_locale_survives_a_new_login(self, app_client: httpx.AsyncClient) -> None:
        headers = _auth(await _login(app_client))
        changed = await app_client.patch(f"{BASE}/users/me", json={"locale": "ko"}, headers=headers)
        assert changed.status_code == 200, changed.text
        assert changed.json()["locale"] == "ko"

        # 새 세션 — 브라우저가 기억하는 것이 아무것도 없는 상태다.
        fresh = _auth(await _login(app_client))
        me = await app_client.get(f"{BASE}/auth/me", headers=fresh)
        assert me.json()["locale"] == "ko"

        back = await app_client.patch(f"{BASE}/users/me", json={"locale": "en"}, headers=fresh)
        assert back.json()["locale"] == "en"

    async def test_unknown_locale_is_refused(self, app_client: httpx.AsyncClient) -> None:
        """카탈로그가 없는 언어를 저장하면 그 사용자는 영어로 폴백된 화면을
        보면서 설정만 다른 값을 가진다. 거절하는 편이 정직하다."""
        headers = _auth(await _login(app_client))
        r = await app_client.patch(f"{BASE}/users/me", json={"locale": "fr"}, headers=headers)
        assert r.status_code == 422, r.text
        body = r.json()["error"]
        assert body["code"] == "identity.unsupported_locale"
        # 무엇을 고를 수 있는지 함께 말해 준다.
        assert "en" in body["details"]["supported"]

    async def test_requires_authentication(self, app_client: httpx.AsyncClient) -> None:
        r = await app_client.patch(f"{BASE}/users/me", json={"locale": "ko"})
        assert r.status_code == 401


class TestLoginFailureIsRecorded:
    """실패한 로그인은 트랜잭션 밖으로 살아남아야 한다.

    실패 경로는 예외를 던지고, 그러면 요청 세션이 롤백된다. 커밋하지 않으면
    감사 기록도 시도 기록도 함께 사라진다 — 감사 로그에 실패가 안 남고,
    실패 횟수를 못 세니 **무차별 대입 제한이 통째로 동작하지 않는다**.
    """

    async def test_the_rate_limiter_actually_fires(
        self, app_client: httpx.AsyncClient, settings: Settings
    ) -> None:
        email = f"bruteforce-{uuid4().hex[:10]}@example.com"
        limit = settings.login_max_attempts
        codes = []
        for _ in range(limit + 2):
            r = await app_client.post(
                f"{BASE}/auth/login", json={"email": email, "password": "wrong-password-1234"}
            )
            codes.append(r.status_code)
        # 잠그지 않고 지연시킨다. 상한을 넘긴 뒤에는 429 가 나와야 한다.
        assert 429 in codes, codes

    async def test_the_failure_lands_in_the_audit_log(self, app_client: httpx.AsyncClient) -> None:
        email = f"ghost-{uuid4().hex[:10]}@example.com"
        await app_client.post(
            f"{BASE}/auth/login", json={"email": email, "password": "wrong-password-1234"}
        )

        headers = _auth(await _login(app_client))
        rows = (
            await app_client.get(f"{BASE}/audit?action=auth.login.failed", headers=headers)
        ).json()["items"]
        assert any(r["metadata"].get("email") == email for r in rows), rows


class TestSessionRevocation:
    """기기 하나만 끊는 길. 전부 끊으면 지금 쓰는 자리에서도 튕겨 나간다."""

    async def test_one_device_goes_and_the_rest_stay(self, app_client: httpx.AsyncClient) -> None:
        first = _auth(await _login(app_client))
        second = _auth(await _login(app_client))

        rows = (await app_client.get(f"{BASE}/auth/sessions", headers=second)).json()
        other = next(r for r in rows if not r["is_current"])

        killed = await app_client.delete(f"{BASE}/auth/sessions/{other['id']}", headers=second)
        assert killed.status_code == 204

        # 끊은 쪽은 즉시 죽고, 끊은 사람 자신은 그대로 붙어 있다.
        assert (await app_client.get(f"{BASE}/auth/me", headers=first)).status_code == 401
        assert (await app_client.get(f"{BASE}/auth/me", headers=second)).status_code == 200

    async def test_someone_elses_session_is_not_revealed(
        self, app_client: httpx.AsyncClient, settings: Settings
    ) -> None:
        """404 를 주면 세션 id 를 넣어 보며 남의 세션 존재를 확인할 수 있다.
        조용히 204 를 주되 **끊지는 않는다**."""
        victim = _auth(await _login(app_client))
        victim_sessions = (await app_client.get(f"{BASE}/auth/sessions", headers=victim)).json()
        target = victim_sessions[0]["id"]

        invited = await _invited_user(app_client, settings)
        r = await app_client.delete(f"{BASE}/auth/sessions/{target}", headers=invited)
        assert r.status_code == 204
        assert (await app_client.get(f"{BASE}/auth/me", headers=victim)).status_code == 200

    async def test_admin_revokes_another_users_sessions(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """퇴사·기기 분실 때 관리자가 하는 일. 원격 폐기가 없으면 비밀번호를
        강제로 바꾸는 수밖에 없다."""
        victim_tokens = await _login(app_client)
        victim = _auth(victim_tokens)
        me = (await app_client.get(f"{BASE}/auth/me", headers=victim)).json()

        admin = _auth(await _login(app_client))
        r = await app_client.delete(f"{BASE}/users/{me['id']}/sessions", headers=admin)
        assert r.status_code == 204
        assert (await app_client.get(f"{BASE}/auth/me", headers=victim)).status_code == 401


class TestAuditLog:
    """감사 로그 조회. 쓰기만 되고 읽을 길이 없으면 감사에 대응할 수 없다."""

    async def test_login_shows_up(self, app_client: httpx.AsyncClient) -> None:
        headers = _auth(await _login(app_client))
        r = await app_client.get(f"{BASE}/audit", headers=headers)
        assert r.status_code == 200, r.text
        rows = r.json()["items"]
        assert rows, "로그인은 반드시 남는다"
        # 최신순이다. 감사 로그는 늘 "방금 무슨 일이 있었나" 부터 본다.
        assert rows[0]["action"] == "auth.login.succeeded"
        # id 만 주면 화면에서 사람을 못 알아본다.
        assert rows[0]["actor_email"] == ADMIN_EMAIL

    async def test_filters_by_action_and_prefix(self, app_client: httpx.AsyncClient) -> None:
        headers = _auth(await _login(app_client))
        await app_client.post(
            f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password-here"}
        )

        exact = await app_client.get(f"{BASE}/audit?action=auth.login.failed", headers=headers)
        assert [r["action"] for r in exact.json()["items"]] == ["auth.login.failed"]

        # 점으로 끝나면 그 영역 전체다.
        area = await app_client.get(f"{BASE}/audit?action=auth.", headers=headers)
        actions = {r["action"] for r in area.json()["items"]}
        assert {"auth.login.failed", "auth.login.succeeded"} <= actions

    async def test_pages_backwards_without_repeating(self, app_client: httpx.AsyncClient) -> None:
        """같은 밀리초에 여러 행이 쌓여도 커서가 흔들리면 안 된다 — 로그인
        폭주 때 실제로 그렇게 들어온다."""
        headers = _auth(await _login(app_client))
        for _ in range(6):
            await app_client.post(
                f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": "nope-nope-nope"}
            )

        first = (await app_client.get(f"{BASE}/audit?limit=3", headers=headers)).json()
        assert first["next_cursor"]
        second = (
            await app_client.get(
                f"{BASE}/audit?limit=3&cursor={first['next_cursor']}", headers=headers
            )
        ).json()

        ids = [r["id"] for r in first["items"]] + [r["id"] for r in second["items"]]
        assert len(ids) == len(set(ids))

    async def test_actions_come_from_constants(self, app_client: httpx.AsyncClient) -> None:
        """아직 한 번도 안 일어난 행동도 목록에 있어야 한다. 없으면 화면에서
        "그런 건 기록 안 하나" 로 읽힌다."""
        headers = _auth(await _login(app_client))
        r = await app_client.get(f"{BASE}/audit/actions", headers=headers)
        assert r.status_code == 200
        assert "auth.refresh.reuse_detected" in r.json()

    async def test_export_is_csv_with_a_bom(self, app_client: httpx.AsyncClient) -> None:
        """BOM 이 없으면 엑셀이 UTF-8 을 로컬 인코딩으로 읽어 한국어가 깨진다."""
        headers = _auth(await _login(app_client))
        r = await app_client.get(f"{BASE}/audit/export", headers=headers)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        # 감사 로그는 사용자별 ACL 을 탄다. 중간 캐시에 남으면 안 된다.
        assert r.headers["cache-control"] == "no-store"
        assert r.content.startswith(b"\xef\xbb\xbf")

        text = r.content.decode("utf-8-sig")
        assert text.splitlines()[0].startswith("created_at,action,actor_email")
        assert ADMIN_EMAIL in text

    async def test_without_the_permission_it_is_refused(
        self, app_client: httpx.AsyncClient, settings: Settings
    ) -> None:
        """목록 하나가 조직의 활동 전부를 드러낸다."""
        invited = await _invited_user(app_client, settings)
        assert (await app_client.get(f"{BASE}/audit", headers=invited)).status_code == 403
        assert (await app_client.get(f"{BASE}/audit/actions", headers=invited)).status_code == 403
        # 거절이 빈 파일로 보이면 안 된다 — 헤더가 나간 뒤에 막으면 그렇게 된다.
        assert (await app_client.get(f"{BASE}/audit/export", headers=invited)).status_code == 403


async def _invited_user(client: httpx.AsyncClient, settings: Settings) -> dict[str, str]:
    """아무 권한도 없는 계정. 초대받은 사람에게는 아무 권한도 없다."""
    from ieum.modules.identity.invites import encode_invite_token

    admin = _auth(await _login(client))
    email = f"nobody-{uuid4().hex[:10]}@example.com"
    invited = await client.post(
        f"{BASE}/users/invite",
        json={"email": email, "display_name": "Nobody"},
        headers=admin,
    )
    assert invited.status_code == 201, invited.text

    password = "nobody-password-1234"
    token = encode_invite_token(UUID(invited.json()["id"]), settings)
    accepted = await client.post(
        f"{BASE}/users/accept-invite", json={"token": token, "password": password}
    )
    assert accepted.status_code == 200, accepted.text

    signed_in = await client.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    assert signed_in.status_code == 200, signed_in.text
    return _auth(dict(signed_in.json()))


class TestMFAPolicy:
    """조직·역할 정책으로 2FA 를 강제한다 (auth.md 3절).

    켜 놓고 등록할 길을 안 열어 주면 계정이 잠긴다. 그 회귀를 여기서 막는다.

    정책을 켠 **뒤에** 로그인하면 그 세션은 미완료 상태라 정책 API 까지
    막힌다. 그래서 스위치는 켜기 **전에** 잡아 둔 세션으로 다룬다 — 실제
    관리자도 화면을 열어 둔 채 켠다.
    """

    @staticmethod
    async def _turn_on(client: httpx.AsyncClient, headers: dict[str, str]) -> httpx.Response:
        return await client.put(
            f"{BASE}/admin/security", json={"require_mfa": True}, headers=headers
        )

    async def test_policy_forces_enrollment_not_a_dead_end(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """켜라고는 하는데 켤 것이 없으면, 확인 화면은 만들 수 없는 코드를
        요구한다. 등록으로 보내야 한다."""
        admin = _auth(await _login(app_client))
        assert (await self._turn_on(app_client, admin)).status_code == 200

        body = await self._relogin(app_client, ADMIN_EMAIL, ADMIN_PASSWORD)
        assert body["mfa_required"] is True
        assert body["mfa_enrollment_required"] is True

        # 보호된 API 도 "등록하라" 고 말한다. "코드를 넣으라" 가 아니다.
        me = await app_client.get(f"{BASE}/auth/me", headers=_auth(body))
        assert me.status_code == 403
        assert me.json()["error"]["code"] == "auth.mfa_enrollment_required"

    async def test_already_open_sessions_keep_working(self, app_client: httpx.AsyncClient) -> None:
        """정책은 **다음 로그인부터** 문다. 켜는 순간 모두가 튕겨 나가면
        관리자 자신도 스위치를 되돌릴 수 없다."""
        admin = _auth(await _login(app_client))
        assert (await self._turn_on(app_client, admin)).status_code == 200
        assert (await app_client.get(f"{BASE}/auth/me", headers=admin)).status_code == 200

    async def test_a_role_can_require_it(
        self, app_client: httpx.AsyncClient, settings: Settings
    ) -> None:
        """관리자·상담원처럼 남의 데이터를 보는 자리에 붙인다. 조직 전체를
        켜지 않고도 강제할 수 있어야 한다."""
        admin = _auth(await _login(app_client))
        invited = await _invited_user(app_client, settings)
        me = (await app_client.get(f"{BASE}/auth/me", headers=invited)).json()

        role = await app_client.post(
            f"{BASE}/roles",
            json={
                "name": f"Auditor {uuid4().hex[:6]}",
                "scope_kind": "global",
                "grants": ["identity.audit.view"],
                "require_mfa": True,
            },
            headers=admin,
        )
        assert role.status_code == 201, role.text
        assert role.json()["require_mfa"] is True

        assigned = await app_client.post(
            f"{BASE}/roles/assignments",
            json={
                "role_id": role.json()["id"],
                "scope_kind": "global",
                "principal_kind": "user",
                "principal_id": me["id"],
            },
            headers=admin,
        )
        assert assigned.status_code == 204, assigned.text

        again = await self._relogin(app_client, me["email"])
        assert again["mfa_enrollment_required"] is True
        # 역할을 안 받은 사람은 그대로다. 정책이 조직 전체로 새면 안 된다.
        untouched = await self._relogin(app_client, ADMIN_EMAIL, ADMIN_PASSWORD)
        assert untouched["mfa_enrollment_required"] is False

    async def test_enrolling_opens_the_session_right_away(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """방금 맞힌 코드가 소지 증명이다. 또 물으면 사람은 "안 되는구나" 로
        읽는다."""
        admin = _auth(await _login(app_client))
        assert (await self._turn_on(app_client, admin)).status_code == 200

        tokens = await self._relogin(app_client, ADMIN_EMAIL, ADMIN_PASSWORD)
        pending = _auth(tokens)
        assert tokens["mfa_enrollment_required"] is True

        enrollment = await app_client.post(f"{BASE}/auth/mfa/totp/enroll", headers=pending)
        assert enrollment.status_code == 200, enrollment.text
        confirmed = await app_client.post(
            f"{BASE}/auth/mfa/totp/{enrollment.json()['credential_id']}/confirm",
            json={"code": pyotp.TOTP(enrollment.json()["secret"]).now()},
            headers=pending,
        )
        assert confirmed.status_code == 204, confirmed.text

        # 등록을 마친 그 세션이 바로 열려야 한다.
        me = await app_client.get(f"{BASE}/auth/me", headers=pending)
        assert me.status_code == 200, me.text

    async def test_the_policy_is_audited(self, app_client: httpx.AsyncClient) -> None:
        admin = _auth(await _login(app_client))
        await self._turn_on(app_client, admin)

        rows = (
            await app_client.get(
                f"{BASE}/audit?action=org.security.mfa_policy_changed", headers=admin
            )
        ).json()["items"]
        assert rows, "보안 정책 변경은 반드시 남는다"
        assert rows[0]["metadata"]["require_mfa"] is True

    async def test_without_the_permission_it_is_refused(
        self, app_client: httpx.AsyncClient, settings: Settings
    ) -> None:
        invited = await _invited_user(app_client, settings)
        read = await app_client.get(f"{BASE}/admin/security", headers=invited)
        assert read.status_code == 403
        write = await app_client.put(
            f"{BASE}/admin/security", json={"require_mfa": True}, headers=invited
        )
        assert write.status_code == 403

    @staticmethod
    async def _relogin(
        client: httpx.AsyncClient, email: str, password: str = INVITED_PASSWORD
    ) -> dict[str, Any]:
        signed_in = await client.post(
            f"{BASE}/auth/login", json={"email": email, "password": password}
        )
        assert signed_in.status_code == 200, signed_in.text
        return dict(signed_in.json())


class TestMFACannotBeRefreshedAway:
    """비밀번호만 통과한 세션은 리프레시해도 미완료여야 한다.

    로테이션이 완료 상태를 새로 만들어 주면, 2FA 를 켜 둔 계정도 비밀번호
    하나로 열린다 — 로그인 직후 `/auth/refresh` 한 번이면 끝이다.
    """

    async def test_rotating_a_pending_session_does_not_satisfy_mfa(
        self, app_client: httpx.AsyncClient
    ) -> None:
        admin = _auth(await _login(app_client))
        assert (
            await app_client.put(
                f"{BASE}/admin/security", json={"require_mfa": True}, headers=admin
            )
        ).status_code == 200

        pending = await app_client.post(
            f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert pending.json()["mfa_required"] is True

        rotated = await app_client.post(
            f"{BASE}/auth/refresh", json={"refresh_token": pending.json()["refresh_token"]}
        )
        assert rotated.status_code == 200, rotated.text
        # 여기가 무너지면 2FA 는 장식이다.
        assert rotated.json()["mfa_required"] is True

        blocked = await app_client.get(f"{BASE}/auth/me", headers=_auth(rotated.json()))
        assert blocked.status_code == 403, blocked.text

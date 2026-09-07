"""WebAuthn/패스키 (auth.md 3절).

**서명을 실제로 만들고 실제로 검증한다.** 가짜 인증기가 ES256 키를 들고
authenticatorData 를 조립해 서명하므로, 검증을 끄면 이 파일이 붉어진다 —
응답만 그럴듯하게 만들어 두면 검증이 통째로 없어도 통과한다.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pyotp
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from fake_authenticator import FakeAuthenticator
from ieum.config import Settings, get_settings
from ieum.core.exceptions import AuthenticationError, ValidationError
from ieum.modules.identity import webauthn as wa

pytestmark = pytest.mark.integration

BASE = "/api/v1"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "seed-admin-password-1234"


@pytest.fixture
def rp() -> wa.RelyingParty:
    settings = get_settings()
    return wa.relying_party(settings.base_url, tuple(settings.cors_origins))


@pytest.fixture
def authenticator(rp: wa.RelyingParty) -> FakeAuthenticator:
    return FakeAuthenticator(rp_id=rp.rp_id, origin=rp.origins[0])


class TestRelyingParty:
    def test_the_rp_id_is_the_host(self) -> None:
        party = wa.relying_party("https://ieum.example.com/app")
        assert party.rp_id == "ieum.example.com"
        assert party.origins == ("https://ieum.example.com",)

    def test_other_hosts_are_not_accepted_as_origins(self) -> None:
        """`rp_id` 가 다른 오리진은 어차피 인증기가 거절한다.

        여기서 받아 두면 "왜 안 되는지" 가 더 늦게, 더 이해하기 어려운 자리에서
        드러난다 — 등록은 됐는데 인증만 안 되는 식이다.
        """
        party = wa.relying_party(
            "http://localhost:5173", ("http://127.0.0.1:5173", "https://localhost")
        )
        assert party.rp_id == "localhost"
        assert party.origins == ("http://localhost:5173", "https://localhost")


class TestVerification:
    """검증 코어. DB 없이 돈다."""

    def _register(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator, **kwargs: Any
    ) -> wa.Registered:
        challenge = wa.registration_challenge(
            rp=rp, user_id=b"x" * 16, user_name="a@b.c", display_name="A"
        )
        return wa.verify_registration(
            response_json=authenticator.register(challenge=challenge.challenge, **kwargs),
            rp=rp,
            challenge=challenge.challenge,
        )

    def test_a_real_registration_passes(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        registered = self._register(rp, authenticator)
        assert registered.credential_id == authenticator.credential_id_b64
        assert registered.public_key
        assert registered.transports == ["internal"]
        assert registered.backed_up is False

    def test_a_passkey_is_marked_as_backed_up(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        """기기에만 있는 키와 계정에 딸린 키는 **잃었을 때 결과가 다르다.**
        사람에게 보여 줄 수 있어야 한다."""
        assert self._register(rp, authenticator, backed_up=True).backed_up is True

    def test_without_user_verification_it_is_refused(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        """2차 요소가 "꽂혀 있음" 만 뜻하면 기기를 집어 든 사람이 통과한다."""
        with pytest.raises(ValidationError) as raised:
            self._register(rp, authenticator, user_verified=False)
        assert raised.value.code == "auth.webauthn_invalid_registration"

    def test_another_origin_is_refused(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        with pytest.raises(ValidationError):
            self._register(rp, authenticator, origin="https://evil.example.com")

    def test_another_rp_id_is_refused(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        with pytest.raises(ValidationError):
            self._register(rp, authenticator, rp_id="evil.example.com")

    def test_another_challenge_is_refused(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        """챌린지를 안 보면 지난 응답을 그대로 다시 쓸 수 있다."""
        issued = wa.registration_challenge(
            rp=rp, user_id=b"x" * 16, user_name="a@b.c", display_name="A"
        )
        other = wa.registration_challenge(
            rp=rp, user_id=b"x" * 16, user_name="a@b.c", display_name="A"
        )
        with pytest.raises(ValidationError):
            wa.verify_registration(
                response_json=authenticator.register(challenge=other.challenge),
                rp=rp,
                challenge=issued.challenge,
            )

    def test_authentication_round_trip(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        registered = self._register(rp, authenticator)
        challenge = wa.authentication_challenge(rp=rp, credential_ids=(registered.credential_id,))
        result = wa.verify_authentication(
            response_json=authenticator.authenticate(challenge=challenge.challenge),
            rp=rp,
            challenge=challenge.challenge,
            public_key=registered.public_key,
            current_sign_count=registered.sign_count,
        )
        assert result.credential_id == registered.credential_id
        assert result.new_sign_count > registered.sign_count

    def test_another_key_cannot_sign(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        """서명 검증이 실제로 도는지 보는 자리다."""
        registered = self._register(rp, authenticator)
        challenge = wa.authentication_challenge(rp=rp, credential_ids=(registered.credential_id,))
        with pytest.raises(AuthenticationError) as raised:
            wa.verify_authentication(
                response_json=authenticator.authenticate(
                    challenge=challenge.challenge,
                    sign_with=ec.generate_private_key(ec.SECP256R1()),
                ),
                rp=rp,
                challenge=challenge.challenge,
                public_key=registered.public_key,
                current_sign_count=registered.sign_count,
            )
        assert raised.value.code == "auth.webauthn_invalid_assertion"

    def test_authentication_without_user_verification_is_refused(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        """등록에서만 요구하고 인증에서 안 보면, 등록 뒤로는 "꽂혀 있음" 만으로
        통과한다 — 기기를 집어 든 사람이 그대로 들어온다."""
        registered = self._register(rp, authenticator)
        challenge = wa.authentication_challenge(rp=rp, credential_ids=(registered.credential_id,))
        with pytest.raises(AuthenticationError):
            wa.verify_authentication(
                response_json=authenticator.authenticate(
                    challenge=challenge.challenge, user_verified=False
                ),
                rp=rp,
                challenge=challenge.challenge,
                public_key=registered.public_key,
                current_sign_count=registered.sign_count,
            )

    def test_a_rewound_counter_is_refused(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        """카운터가 되돌아가면 복제를 의심한다."""
        registered = self._register(rp, authenticator)
        challenge = wa.authentication_challenge(rp=rp, credential_ids=(registered.credential_id,))
        with pytest.raises(AuthenticationError):
            wa.verify_authentication(
                response_json=authenticator.authenticate(
                    challenge=challenge.challenge, sign_count=3
                ),
                rp=rp,
                challenge=challenge.challenge,
                public_key=registered.public_key,
                current_sign_count=10,
            )

    def test_a_counter_that_stays_zero_is_fine(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        """패스키 대부분이 0 을 계속 준다. 0 을 복제로 보면 정상 로그인이 막힌다."""
        registered = self._register(rp, authenticator)
        for _ in range(2):
            challenge = wa.authentication_challenge(
                rp=rp, credential_ids=(registered.credential_id,)
            )
            result = wa.verify_authentication(
                response_json=authenticator.authenticate(
                    challenge=challenge.challenge, sign_count=0
                ),
                rp=rp,
                challenge=challenge.challenge,
                public_key=registered.public_key,
                current_sign_count=0,
            )
            assert result.new_sign_count == 0

    def test_the_credential_id_is_readable_before_verification(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        """어느 공개키로 검증할지 고르려면 먼저 알아야 한다."""
        registered = self._register(rp, authenticator)
        challenge = wa.authentication_challenge(rp=rp, credential_ids=(registered.credential_id,))
        response = authenticator.authenticate(challenge=challenge.challenge)
        assert wa.credential_id_of(response) == registered.credential_id

    def test_garbage_is_refused(self) -> None:
        with pytest.raises(ValidationError) as raised:
            wa.credential_id_of("not json")
        assert raised.value.code == "auth.webauthn_invalid_assertion"


class TestRegistrationOptions:
    def test_already_registered_credentials_are_excluded(
        self, rp: wa.RelyingParty, authenticator: FakeAuthenticator
    ) -> None:
        """안 보내면 같은 인증기를 두 번 등록하게 되고, 사람은 목록에 같은
        것이 둘 있는 이유를 알 수 없다."""
        challenge = wa.registration_challenge(
            rp=rp,
            user_id=b"x" * 16,
            user_name="a@b.c",
            display_name="A",
            already_registered=(authenticator.credential_id_b64,),
        )
        options = json.loads(challenge.options_json)
        assert [c["id"] for c in options["excludeCredentials"]] == [authenticator.credential_id_b64]

    def test_user_verification_is_required(self, rp: wa.RelyingParty) -> None:
        challenge = wa.registration_challenge(
            rp=rp, user_id=b"x" * 16, user_name="a@b.c", display_name="A"
        )
        options = json.loads(challenge.options_json)
        assert options["authenticatorSelection"]["userVerification"] == "required"


async def _invited_user(client: httpx.AsyncClient, settings: Settings) -> dict[str, str]:
    """두 번째 사람. 초대 토큰을 직접 만들어 메일 왕복을 건너뛴다 —
    초대 흐름 자체는 `test_identity_api.py` 가 본다."""
    from ieum.modules.identity.invites import encode_invite_token

    admin = _auth(await _login(client))
    email = f"passkey-{uuid4().hex[:10]}@example.com"
    invited = await client.post(
        f"{BASE}/users/invite",
        json={"email": email, "display_name": "Somebody Else"},
        headers=admin,
    )
    assert invited.status_code == 201, invited.text

    password = "somebody-password-1234"
    token = encode_invite_token(UUID(invited.json()["id"]), settings)
    accepted = await client.post(
        f"{BASE}/users/accept-invite", json={"token": token, "password": password}
    )
    assert accepted.status_code == 200, accepted.text

    signed_in = await client.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    assert signed_in.status_code == 200, signed_in.text
    return _auth(dict(signed_in.json()))


async def _login(client: httpx.AsyncClient) -> dict[str, Any]:
    signed_in = await client.post(
        f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert signed_in.status_code == 200, signed_in.text
    return dict(signed_in.json())


def _auth(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _register_authenticator(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    authenticator: FakeAuthenticator,
    **kwargs: Any,
) -> httpx.Response:
    started = await client.post(f"{BASE}/auth/mfa/webauthn/register/start", headers=headers)
    assert started.status_code == 200, started.text
    challenge = json.loads(started.json()["options"])["challenge"]
    return await client.post(
        f"{BASE}/auth/mfa/webauthn/register/finish",
        headers=headers,
        json={"response": authenticator.register(challenge=challenge, **kwargs)},
    )


class TestRegistrationApi:
    async def test_a_fresh_account_can_register(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        headers = _auth(await _login(app_client))
        created = await _register_authenticator(app_client, headers, authenticator)
        assert created.status_code == 201, created.text
        row = created.json()
        assert row["kind"] == "webauthn"
        assert row["confirmed_at"] is not None
        # 공개키도 자격증명 ID 도 응답에 나오지 않는다. 화면에 쓸 것이 아니다.
        assert "public_key" not in created.text
        assert authenticator.credential_id_b64 not in created.text

    async def test_registering_opens_this_session(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        """방금 인증기를 만진 것이 소지 증명이다.

        세션을 열어 주지 않으면 등록 직후의 백업 코드 발급(MFA 통과 세션
        전용)이 403 으로 죽는다 — 폰을 잃었을 때의 유일한 복구 수단이 사라진다.
        """
        headers = _auth(await _login(app_client))
        assert (
            await _register_authenticator(app_client, headers, authenticator)
        ).status_code == 201

        codes = await app_client.post(f"{BASE}/auth/mfa/backup-codes", headers=headers)
        assert codes.status_code == 200, codes.text
        assert len(codes.json()["codes"]) == 10

    async def test_a_second_authenticator_needs_the_first(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        """이미 2FA 가 있는 계정에 미완료 세션으로 새 인증기를 등록할 수 있으면,
        비밀번호만 아는 공격자가 자기 키를 붙여 2차 요소를 그대로 우회한다."""
        headers = _auth(await _login(app_client))
        assert (
            await _register_authenticator(app_client, headers, authenticator)
        ).status_code == 201

        # 새 세션은 미완료다(등록된 2FA 가 있으므로).
        fresh = _auth(await _login(app_client))
        refused = await app_client.post(f"{BASE}/auth/mfa/webauthn/register/start", headers=fresh)
        assert refused.status_code == 403, refused.text

    async def test_the_same_authenticator_twice_is_a_conflict(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        """같은 자격증명이 두 계정에 붙으면 하나로 다른 계정에 들어갈 수 있다."""
        headers = _auth(await _login(app_client))
        assert (
            await _register_authenticator(app_client, headers, authenticator)
        ).status_code == 201
        again = await _register_authenticator(app_client, headers, authenticator)
        assert again.status_code == 409, again.text
        assert again.json()["error"]["code"] == "auth.webauthn_already_registered"

    async def test_finishing_without_starting_is_refused(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        """챌린지 없이 통과할 길이 있으면 서명을 지어낼 수 있다."""
        headers = _auth(await _login(app_client))
        refused = await app_client.post(
            f"{BASE}/auth/mfa/webauthn/register/finish",
            headers=headers,
            json={"response": authenticator.register(challenge="AAAA")},
        )
        assert refused.status_code == 401, refused.text
        assert refused.json()["error"]["code"] == "auth.webauthn_no_challenge"

    async def test_the_challenge_works_once(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        headers = _auth(await _login(app_client))
        started = await app_client.post(f"{BASE}/auth/mfa/webauthn/register/start", headers=headers)
        challenge = json.loads(started.json()["options"])["challenge"]
        first = await app_client.post(
            f"{BASE}/auth/mfa/webauthn/register/finish",
            headers=headers,
            json={"response": authenticator.register(challenge=challenge)},
        )
        assert first.status_code == 201, first.text

        other = FakeAuthenticator(rp_id=authenticator.rp_id, origin=authenticator.origin)
        again = await app_client.post(
            f"{BASE}/auth/mfa/webauthn/register/finish",
            headers=headers,
            json={"response": other.register(challenge=challenge)},
        )
        assert again.status_code == 401, again.text
        assert again.json()["error"]["code"] == "auth.webauthn_no_challenge"


class TestAuthenticationApi:
    async def _enrolled(
        self, client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> dict[str, str]:
        headers = _auth(await _login(client))
        created = await _register_authenticator(client, headers, authenticator)
        assert created.status_code == 201, created.text
        return headers

    async def test_a_new_session_is_opened_by_the_authenticator(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        await self._enrolled(app_client, authenticator)

        # 새 로그인은 미완료 세션이다.
        pending = _auth(await _login(app_client))
        blocked = await app_client.get(f"{BASE}/auth/me", headers=pending)
        assert blocked.status_code == 403, blocked.text

        started = await app_client.post(f"{BASE}/auth/mfa/webauthn/verify/start", headers=pending)
        assert started.status_code == 200, started.text
        challenge = json.loads(started.json()["options"])["challenge"]
        finished = await app_client.post(
            f"{BASE}/auth/mfa/webauthn/verify/finish",
            headers=pending,
            json={"response": authenticator.authenticate(challenge=challenge)},
        )
        assert finished.status_code == 204, finished.text

        opened = await app_client.get(f"{BASE}/auth/me", headers=pending)
        assert opened.status_code == 200, opened.text

    async def test_it_counts_as_a_real_second_factor(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        """step-up 은 "실제로 통과했는가" 를 본다(#60). 인증기도 그 자리를
        채워야 한다 — 아니면 패스키만 쓰는 사람이 민감한 설정을 못 만진다."""
        await self._enrolled(app_client, authenticator)
        pending = _auth(await _login(app_client))
        started = await app_client.post(f"{BASE}/auth/mfa/webauthn/verify/start", headers=pending)
        challenge = json.loads(started.json()["options"])["challenge"]
        await app_client.post(
            f"{BASE}/auth/mfa/webauthn/verify/finish",
            headers=pending,
            json={"response": authenticator.authenticate(challenge=challenge)},
        )

        # step-up 이 걸린 자리를 지나간다.
        role = await app_client.post(
            f"{BASE}/roles",
            json={"name": "Passkey role", "scope_kind": "global", "grants": []},
            headers=pending,
        )
        assert role.status_code == 201, role.text

    async def test_an_unknown_authenticator_is_refused(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        """서명이 맞아도 이 세션의 사람이 아니면 거절한다.

        안 보면 등록된 아무 키로 남의 세션을 열 수 있다.
        """
        await self._enrolled(app_client, authenticator)
        pending = _auth(await _login(app_client))
        started = await app_client.post(f"{BASE}/auth/mfa/webauthn/verify/start", headers=pending)
        challenge = json.loads(started.json()["options"])["challenge"]

        stranger = FakeAuthenticator(rp_id=authenticator.rp_id, origin=authenticator.origin)
        refused = await app_client.post(
            f"{BASE}/auth/mfa/webauthn/verify/finish",
            headers=pending,
            json={"response": stranger.authenticate(challenge=challenge)},
        )
        assert refused.status_code == 401, refused.text
        assert refused.json()["error"]["code"] == "auth.webauthn_unknown_credential"

    async def test_another_persons_authenticator_is_refused(
        self,
        app_client: httpx.AsyncClient,
        authenticator: FakeAuthenticator,
        settings: Settings,
    ) -> None:
        """**등록된** 자격증명이라도 이 세션의 사람 것이 아니면 거절한다.

        서명은 맞는다 — 진짜 인증기가 진짜로 서명했으니까. 소유자를 안 보면
        자기 인증기로 남의 미완료 세션을 열 수 있다. "서명이 맞다" 와 "이
        사람이 맞다" 는 다른 질문이다.
        """
        other_headers = await _invited_user(app_client, settings)
        other_authenticator = FakeAuthenticator(
            rp_id=authenticator.rp_id, origin=authenticator.origin
        )
        assert (
            await _register_authenticator(app_client, other_headers, other_authenticator)
        ).status_code == 201

        await self._enrolled(app_client, authenticator)
        pending = _auth(await _login(app_client))
        started = await app_client.post(f"{BASE}/auth/mfa/webauthn/verify/start", headers=pending)
        challenge = json.loads(started.json()["options"])["challenge"]

        refused = await app_client.post(
            f"{BASE}/auth/mfa/webauthn/verify/finish",
            headers=pending,
            json={"response": other_authenticator.authenticate(challenge=challenge)},
        )
        assert refused.status_code == 401, refused.text
        assert refused.json()["error"]["code"] == "auth.webauthn_unknown_credential"

    async def test_starting_without_an_authenticator_is_refused(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = _auth(await _login(app_client))
        refused = await app_client.post(f"{BASE}/auth/mfa/webauthn/verify/start", headers=headers)
        assert refused.status_code == 404, refused.text

    async def test_the_counter_moves_forward(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        """카운터를 저장하지 않으면 복제 탐지가 영원히 0 과 비교한다."""
        headers = await self._enrolled(app_client, authenticator)
        for _ in range(2):
            started = await app_client.post(
                f"{BASE}/auth/mfa/webauthn/verify/start", headers=headers
            )
            challenge = json.loads(started.json()["options"])["challenge"]
            done = await app_client.post(
                f"{BASE}/auth/mfa/webauthn/verify/finish",
                headers=headers,
                json={"response": authenticator.authenticate(challenge=challenge)},
            )
            assert done.status_code == 204, done.text

        # 되돌린 카운터는 거절된다 — 저장이 됐다는 뜻이다.
        started = await app_client.post(f"{BASE}/auth/mfa/webauthn/verify/start", headers=headers)
        challenge = json.loads(started.json()["options"])["challenge"]
        refused = await app_client.post(
            f"{BASE}/auth/mfa/webauthn/verify/finish",
            headers=headers,
            json={"response": authenticator.authenticate(challenge=challenge, sign_count=1)},
        )
        assert refused.status_code == 401, refused.text


class TestCredentialList:
    async def test_it_shows_authenticators_and_hides_backup_codes(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        """백업 코드 열 개를 목록에 늘어놓으면 인증기가 무엇인지 안 보인다."""
        headers = _auth(await _login(app_client))
        await _register_authenticator(app_client, headers, authenticator)
        await app_client.post(f"{BASE}/auth/mfa/backup-codes", headers=headers)

        listed = await app_client.get(f"{BASE}/auth/mfa/credentials", headers=headers)
        assert listed.status_code == 200, listed.text
        kinds = [row["kind"] for row in listed.json()]
        assert kinds == ["webauthn"]

    async def test_removing_needs_a_verified_session(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        """비밀번호만 아는 공격자가 남의 인증기를 떼면 그것이 곧 2차 요소 해제다."""
        headers = _auth(await _login(app_client))
        created = await _register_authenticator(app_client, headers, authenticator)
        credential_id = created.json()["id"]

        pending = _auth(await _login(app_client))
        refused = await app_client.delete(
            f"{BASE}/auth/mfa/credentials/{credential_id}", headers=pending
        )
        assert refused.status_code == 403, refused.text

        # 통과한 세션에서는 뗀다.
        removed = await app_client.delete(
            f"{BASE}/auth/mfa/credentials/{credential_id}", headers=headers
        )
        assert removed.status_code == 204, removed.text

    async def test_backup_codes_are_not_removed_one_by_one(
        self, app_client: httpx.AsyncClient
    ) -> None:
        headers = _auth(await _login(app_client))
        enrollment = await app_client.post(f"{BASE}/auth/mfa/totp/enroll", headers=headers)
        body = enrollment.json()
        await app_client.post(
            f"{BASE}/auth/mfa/totp/{body['credential_id']}/confirm",
            headers=headers,
            json={"code": pyotp.TOTP(body["secret"]).now()},
        )
        await app_client.post(f"{BASE}/auth/mfa/backup-codes", headers=headers)

        listed = await app_client.get(f"{BASE}/auth/mfa/credentials", headers=headers)
        totp_id = next(row["id"] for row in listed.json() if row["kind"] == "totp")
        # TOTP 는 뗄 수 있다.
        assert (
            await app_client.delete(f"{BASE}/auth/mfa/credentials/{totp_id}", headers=headers)
        ).status_code == 204

    async def test_someone_elses_credential_is_not_revealed(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        """없는 것과 남의 것을 구분해 주면 id 를 넣어 보며 존재를 확인할 수 있다."""
        headers = _auth(await _login(app_client))
        await _register_authenticator(app_client, headers, authenticator)
        refused = await app_client.delete(
            f"{BASE}/auth/mfa/credentials/00000000-0000-7000-8000-000000000000",
            headers=headers,
        )
        assert refused.status_code == 404, refused.text


class TestNoServerSideCopy:
    """**서버가 화면 글자를 지어내지 않는다** (i18n.md).

    서버는 화면의 언어를 알 방법이 없다. 여기서 이름을 채우면 영어 화면에
    한국어가 섞이고, 그 반대도 된다 — 실제로 브라우저에서 그렇게 보였다.
    """

    async def test_an_unnamed_authenticator_has_no_label(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        headers = _auth(await _login(app_client))
        created = await _register_authenticator(app_client, headers, authenticator)
        assert created.status_code == 201, created.text
        assert created.json()["label"] is None

    async def test_a_name_the_person_gave_is_kept(
        self, app_client: httpx.AsyncClient, authenticator: FakeAuthenticator
    ) -> None:
        headers = _auth(await _login(app_client))
        started = await app_client.post(f"{BASE}/auth/mfa/webauthn/register/start", headers=headers)
        challenge = json.loads(started.json()["options"])["challenge"]
        created = await app_client.post(
            f"{BASE}/auth/mfa/webauthn/register/finish",
            headers=headers,
            json={
                "response": authenticator.register(challenge=challenge),
                "label": "회사 노트북",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["label"] == "회사 노트북"

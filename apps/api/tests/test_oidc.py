"""OIDC 흐름의 검증 단계 (auth.md 4절).

**음성 경로가 본체다.** 통과하는 경우는 IdP 가 알아서 맞춰 주지만, 거절해야
하는 경우는 우리가 안 보면 아무도 안 본다. 서명·발급자·수신자·만료·nonce
가운데 하나라도 빠지면 그건 검증이 아니다.

서명 키를 테스트가 직접 들고 진짜 JWT 를 만든다. 라이브러리를 흉내 내면
"우리가 무엇을 넘겼는가" 만 보게 되고, 정작 "그 조합이 실제로 거절되는가" 는
못 본다.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from ieum.config import Settings
from ieum.core.exceptions import AuthenticationError
from ieum.core.time import utcnow
from ieum.modules.identity import oidc

ISSUER = "https://idp.example.com"
CLIENT_ID = "ieum-test-client"
NONCE = "n-0S6_WzA2Mj"


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _StubJwks:
    """`PyJWKClient` 자리. 키 하나만 들고 있으면 검증 경로는 똑같이 돈다."""

    def __init__(self, key: object) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return type("Key", (), {"key": self._key})()


def _token(
    signing_key: rsa.RSAPrivateKey,
    *,
    issuer: str = ISSUER,
    audience: str = CLIENT_ID,
    nonce: str | None = NONCE,
    expires_in: timedelta = timedelta(minutes=5),
    algorithm: str = "RS256",
    **extra: Any,
) -> str:
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": "idp-subject-1",
        "exp": utcnow() + expires_in,
        "iat": utcnow(),
        **extra,
    }
    if nonce is not None:
        claims["nonce"] = nonce
    return jwt.encode(claims, signing_key, algorithm=algorithm)


def _verify(token: str, key: rsa.RSAPrivateKey, *, nonce: str = NONCE) -> dict[str, Any]:
    return oidc.verify_id_token(
        token,
        jwks_client=_StubJwks(key.public_key()),  # type: ignore[arg-type]
        issuer=ISSUER,
        client_id=CLIENT_ID,
        nonce=nonce,
    )


class TestIdTokenVerification:
    def test_a_good_token_passes(self, signing_key: rsa.RSAPrivateKey) -> None:
        claims = _verify(_token(signing_key), signing_key)
        assert claims["sub"] == "idp-subject-1"

    def test_another_key_is_rejected(self, signing_key: rsa.RSAPrivateKey) -> None:
        """서명이 맞아야 한다. 이게 없으면 나머지 검사는 장식이다."""
        attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(AuthenticationError) as exc:
            _verify(_token(attacker), signing_key)
        assert exc.value.code == "auth.oidc_invalid_id_token"

    def test_another_issuer_is_rejected(self, signing_key: rsa.RSAPrivateKey) -> None:
        """다른 IdP 가 발급한 토큰을 이 IdP 것으로 받아들이면 안 된다."""
        with pytest.raises(AuthenticationError):
            _verify(_token(signing_key, issuer="https://evil.example.com"), signing_key)

    def test_another_audience_is_rejected(self, signing_key: rsa.RSAPrivateKey) -> None:
        """같은 IdP 의 **다른 앱**용 토큰을 우리 로그인에 쓰면 안 된다."""
        with pytest.raises(AuthenticationError):
            _verify(_token(signing_key, audience="some-other-app"), signing_key)

    def test_expired_is_rejected(self, signing_key: rsa.RSAPrivateKey) -> None:
        with pytest.raises(AuthenticationError):
            _verify(_token(signing_key, expires_in=timedelta(minutes=-1)), signing_key)

    def test_nonce_mismatch_is_rejected(self, signing_key: rsa.RSAPrivateKey) -> None:
        """라이브러리가 안 봐 주는 자리다. 안 보면 다른 세션의 ID 토큰을
        이 로그인에 끼워 넣을 수 있다."""
        with pytest.raises(AuthenticationError) as exc:
            _verify(_token(signing_key, nonce="someone-elses"), signing_key)
        assert exc.value.code == "auth.oidc_nonce_mismatch"

    def test_missing_nonce_is_rejected(self, signing_key: rsa.RSAPrivateKey) -> None:
        with pytest.raises(AuthenticationError):
            _verify(_token(signing_key, nonce=None), signing_key)

    def test_symmetric_algorithms_are_not_accepted(self) -> None:
        """HS256 을 허용하면 **공개** JWKS 를 키로 써서 토큰을 위조할 수 있다.

        고전적인 알고리즘 혼동 공격이다. 목록에 들어가 있는지 직접 본다.
        """
        assert not any(a.startswith("HS") for a in oidc.ALLOWED_ALGORITHMS)
        assert "none" not in oidc.ALLOWED_ALGORITHMS


class TestState:
    """`state` 는 우리가 만든 것만 열려야 한다. 그게 CSRF 방어다."""

    def _settings(self) -> Settings:
        from conftest import TEST_SECRET

        return Settings(env="test", secret_key=TEST_SECRET)  # type: ignore[arg-type]

    def test_round_trip(self) -> None:
        settings = self._settings()
        flow = oidc.Flow(
            provider_id=uuid4(),
            nonce=NONCE,
            code_verifier=oidc.new_verifier(),
            redirect_uri="https://app.example.com/auth/callback",
        )
        opened = oidc.open_state(oidc.seal_state(flow, settings), settings)
        assert opened == flow

    def test_a_forged_state_does_not_open(self) -> None:
        with pytest.raises(AuthenticationError) as exc:
            oidc.open_state("not-a-real-state", self._settings())
        assert exc.value.code == "auth.oidc_invalid_state"

    def test_an_expired_state_is_refused(self) -> None:
        """사람이 IdP 화면을 열어 둔 채 하루를 보낼 수 있다."""
        settings = self._settings()
        stale = oidc._box(settings).encrypt(
            json.dumps(
                {
                    "provider_id": str(uuid4()),
                    "nonce": NONCE,
                    "code_verifier": "v",
                    "redirect_uri": "https://app.example.com/auth/callback",
                    "expires_at": (utcnow() - timedelta(seconds=1)).isoformat(),
                }
            )
        )
        with pytest.raises(AuthenticationError) as exc:
            oidc.open_state(stale, settings)
        assert exc.value.code == "auth.oidc_state_expired"

    def test_the_verifier_is_not_readable(self) -> None:
        """봉인 안에 든 code_verifier 가 평문으로 새면 PKCE 가 무의미하다."""
        settings = self._settings()
        verifier = oidc.new_verifier()
        sealed = oidc.seal_state(
            oidc.Flow(
                provider_id=uuid4(),
                nonce=NONCE,
                code_verifier=verifier,
                redirect_uri="https://app.example.com/auth/callback",
            ),
            settings,
        )
        assert verifier not in sealed


class TestPkce:
    def test_challenge_is_s256_of_the_verifier(self) -> None:
        import base64
        import hashlib

        verifier = oidc.new_verifier()
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert oidc.challenge_for(verifier) == expected

    def test_verifier_is_long_enough(self) -> None:
        # RFC 7636 은 43-128자를 요구한다.
        assert 43 <= len(oidc.new_verifier()) <= 128


class TestClaimReading:
    def _read(self, claims: dict[str, Any], **kwargs: Any) -> oidc.Claims:
        return oidc.read_claims(
            claims,
            email_claim=kwargs.get("email_claim", "email"),
            name_claim=kwargs.get("name_claim", "name"),
            groups_claim=kwargs.get("groups_claim", "groups"),
            trust_idp_mfa=kwargs.get("trust_idp_mfa", False),
        )

    def test_reads_the_configured_claim_names(self) -> None:
        got = self._read(
            {"sub": "s", "mail": "A@Example.com", "email_verified": True, "cn": "Kim"},
            email_claim="mail",
            name_claim="cn",
        )
        assert got.email == "a@example.com"
        assert got.name == "Kim"

    def test_unverified_email_is_dropped(self) -> None:
        """검증 안 된 주소로 기존 계정에 이으면 남의 계정을 가져갈 수 있다."""
        got = self._read({"sub": "s", "email": "victim@example.com", "email_verified": False})
        assert got.email is None

    def test_groups_default_to_empty(self) -> None:
        assert self._read({"sub": "s"}).groups == []
        # 문자열 하나로 오는 IdP 도 있다. 글자를 쪼개 그룹으로 만들면 안 된다.
        assert self._read({"sub": "s", "groups": "admins"}).groups == []

    def test_idp_mfa_is_not_assumed(self) -> None:
        """위임을 켰다고 무조건 참으로 두면, IdP 에서 비밀번호만으로 들어온
        사람이 우리 쪽 민감 작업까지 통과한다."""
        assert self._read({"sub": "s"}, trust_idp_mfa=True).mfa_satisfied is False
        assert self._read({"sub": "s", "amr": ["pwd"]}, trust_idp_mfa=True).mfa_satisfied is False
        assert self._read({"sub": "s", "amr": ["pwd", "otp"]}, trust_idp_mfa=True).mfa_satisfied

    def test_without_the_policy_idp_mfa_is_ignored(self) -> None:
        assert self._read({"sub": "s", "amr": ["mfa"]}, trust_idp_mfa=False).mfa_satisfied is False

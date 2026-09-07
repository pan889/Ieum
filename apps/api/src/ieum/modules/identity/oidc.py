"""OIDC Authorization Code + PKCE (auth.md 4절).

흐름을 직접 들고 있다. 서명 검증만 pyjwt 에 맡긴다 — 여기가 이 제품의 인증
경계라, 무엇을 검사하는지 읽히지 않으면 검사하지 않는 것과 같다.

**전이 상태는 서버에 두지 않는다.** `state` 에 AES-GCM 으로 봉한 값을 싣는다
(초대 토큰과 같은 방식, `invites.py`). 위조가 불가능하고, Redis 를 앱 쪽으로
끌어들이지 않아도 된다. 안에 든 `code_verifier` 는 봉해져 있어 브라우저도
읽지 못한다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
import jwt
from jwt import PyJWKClient

from ieum.config import Settings
from ieum.core.crypto import SecretBox
from ieum.core.exceptions import AuthenticationError, ValidationError
from ieum.core.logging import get_logger
from ieum.core.time import in_seconds, utcnow

#: OIDC 와 SAML 이 같은 것을 낸다. 검증 뒤의 처리를 한 벌로 두기 위한 것이다.
from ieum.modules.identity.sso import Claims

log = get_logger(__name__)

STATE_PURPOSE = "identity.oidc.state"

#: 사람이 IdP 화면에서 머무는 시간. 넉넉하되 무한하지 않게.
STATE_TTL_SECONDS = 15 * 60

#: IdP 왕복 상한. 느린 IdP 하나가 워커를 붙잡으면 안 된다.
HTTP_TIMEOUT_SECONDS = 10.0

#: 우리가 받아들이는 서명 알고리즘. `none` 과 대칭키(HS*)를 **반드시** 뺀다 —
#: HS256 을 허용하면 공개 JWKS 를 키로 써서 토큰을 위조할 수 있다.
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")


@dataclass(frozen=True, slots=True)
class Flow:
    """시작할 때 만들어 `state` 에 봉인하는 값."""

    provider_id: UUID
    nonce: str
    code_verifier: str
    redirect_uri: str


def _box(settings: Settings) -> SecretBox:
    return SecretBox(settings.secret_key.get_secret_value(), purpose=STATE_PURPOSE)


def new_verifier() -> str:
    """PKCE code_verifier. RFC 7636 은 43-128자를 요구한다."""
    return secrets.token_urlsafe(64)


def challenge_for(verifier: str) -> str:
    """S256 챌린지. `plain` 은 쓰지 않는다 — 가로챈 쪽이 그대로 되쓸 수 있다."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def seal_state(flow: Flow, settings: Settings) -> str:
    payload = {
        "provider_id": str(flow.provider_id),
        "nonce": flow.nonce,
        "code_verifier": flow.code_verifier,
        "redirect_uri": flow.redirect_uri,
        "expires_at": in_seconds(STATE_TTL_SECONDS).isoformat(),
    }
    return _box(settings).encrypt(json.dumps(payload, separators=(",", ":")))


def open_state(state: str, settings: Settings) -> Flow:
    """봉인을 연다. 우리가 만든 값이 아니면 열리지 않는다 (= CSRF 방어)."""
    try:
        payload = json.loads(_box(settings).decrypt(state))
    except Exception as exc:
        raise AuthenticationError(
            "로그인 요청을 확인할 수 없다.", code="auth.oidc_invalid_state"
        ) from exc

    from datetime import datetime

    if datetime.fromisoformat(payload["expires_at"]) <= utcnow():
        raise AuthenticationError(
            "로그인 요청이 만료됐다. 다시 시도해 달라.", code="auth.oidc_state_expired"
        )
    return Flow(
        provider_id=UUID(payload["provider_id"]),
        nonce=payload["nonce"],
        code_verifier=payload["code_verifier"],
        redirect_uri=payload["redirect_uri"],
    )


def authorization_url(
    *,
    endpoint: str,
    client_id: str,
    scopes: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}{query}"


async def exchange_code(
    *,
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    """인가 코드를 토큰으로. 실패 본문은 로그에 남기지 않는다 — 코드가 들어 있다."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
        )
    if response.status_code != 200:
        log.warning("auth.oidc_token_exchange_failed", status=response.status_code)
        raise AuthenticationError(
            "IdP 와 토큰을 교환하지 못했다.", code="auth.oidc_exchange_failed"
        )
    body: dict[str, Any] = response.json()
    if "id_token" not in body:
        raise AuthenticationError("IdP 가 ID 토큰을 주지 않았다.", code="auth.oidc_no_id_token")
    return body


def verify_id_token(
    id_token: str,
    *,
    jwks_client: PyJWKClient,
    issuer: str,
    client_id: str,
    nonce: str,
) -> dict[str, Any]:
    """서명·발급자·수신자·만료·nonce 를 본다. 하나라도 빠지면 검증이 아니다.

    `nonce` 는 라이브러리가 안 봐 준다. 안 보면 다른 세션에서 받은 ID 토큰을
    이 로그인에 끼워 넣을 수 있다(재생 공격).
    """
    try:
        key = jwks_client.get_signing_key_from_jwt(id_token)
        claims: dict[str, Any] = jwt.decode(
            id_token,
            key.key,
            algorithms=list(ALLOWED_ALGORITHMS),
            issuer=issuer,
            audience=client_id,
            options={"require": ["iss", "aud", "exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        log.warning("auth.oidc_id_token_rejected", error=type(exc).__name__)
        raise AuthenticationError(
            "ID 토큰을 확인할 수 없다.", code="auth.oidc_invalid_id_token"
        ) from exc

    if claims.get("nonce") != nonce:
        # 라이브러리가 안 보는 자리다. 안 보면 재생이 통한다.
        log.warning("auth.oidc_nonce_mismatch")
        raise AuthenticationError(
            "로그인 요청과 응답이 맞지 않는다.", code="auth.oidc_nonce_mismatch"
        )
    return claims


def read_claims(
    claims: dict[str, Any],
    *,
    email_claim: str,
    name_claim: str,
    groups_claim: str | None,
    trust_idp_mfa: bool,
) -> Claims:
    """설정된 이름으로 클레임을 꺼낸다. IdP 마다 이름이 다르다."""
    subject = str(claims["sub"])
    email = claims.get(email_claim)
    groups_raw = claims.get(groups_claim) if groups_claim else None
    groups = [str(g) for g in groups_raw] if isinstance(groups_raw, list) else []

    return Claims(
        subject=subject,
        # 이메일이 검증되지 않았으면 **없는 것으로 본다.** 검증 안 된 주소로
        # 기존 계정에 이으면 남의 계정을 가져갈 수 있다.
        email=str(email).strip().lower()
        if email and claims.get("email_verified", False) is not False
        else None,
        name=str(claims[name_claim]) if claims.get(name_claim) else None,
        groups=groups,
        mfa_satisfied=trust_idp_mfa and _idp_did_mfa(claims),
    )


def _idp_did_mfa(claims: dict[str, Any]) -> bool:
    """IdP 가 2차 요소를 실제로 요구했는가.

    위임 정책을 켰다고 무조건 참으로 두면, IdP 에서 비밀번호만으로 들어온
    사람이 우리 쪽 민감 작업까지 통과한다 (auth.md 3절 step-up).
    """
    amr = claims.get("amr")
    if isinstance(amr, list) and any(str(m) in {"mfa", "otp", "hwk", "swk", "pop"} for m in amr):
        return True
    acr = claims.get("acr")
    # 널리 쓰이는 두 값. 그 밖의 값은 조직마다 달라 함부로 인정하지 않는다.
    return isinstance(acr, str) and acr in {
        "http://schemas.openid.net/pape/policies/2007/06/multi-factor",
        "urn:mace:incommon:iap:silver",
    }


def domain_of(email: str) -> str:
    _, _, domain = email.rpartition("@")
    if not domain:
        raise ValidationError("이메일 주소가 아니다.", code="identity.invalid_email")
    return domain.lower()


__all__ = [
    "ALLOWED_ALGORITHMS",
    "STATE_TTL_SECONDS",
    "Claims",
    "Flow",
    "authorization_url",
    "challenge_for",
    "domain_of",
    "exchange_code",
    "new_verifier",
    "open_state",
    "read_claims",
    "seal_state",
    "verify_id_token",
]

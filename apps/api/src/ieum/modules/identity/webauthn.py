"""WebAuthn/패스키 (auth.md 3절).

**어테스테이션 검증과 서명 검증은 직접 만들지 않는다.** COSE 공개키 파싱,
서명 형식(ES256/RS256/EdDSA), 어테스테이션 문장 종류가 넓고, 한 군데만 틀려도
"서명을 확인했다" 가 거짓이 된다. `py_webauthn` 에 맡기고 이 모듈이 타입
경계다 — 라이브러리와 닿는 자리를 여기 안에 몰아 두고, 밖으로는 우리
dataclass 만 내보낸다.

여기서 하는 일은 셋이다: RP 정보를 설정에서 만들어 주고, 라이브러리가 결정하지
않는 정책(무엇을 요구하고 무엇을 거절하는가)을 정하고, 통과한 결과를 우리
모델이 저장할 꼴로 옮긴다.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import webauthn as wan
from webauthn.helpers import options_to_json
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ieum.core.exceptions import AuthenticationError, ValidationError
from ieum.core.logging import get_logger

log = get_logger(__name__)

#: 사람이 인증기를 만지는 시간. 넉넉하되 무한하지 않게.
CHALLENGE_TTL_SECONDS = 5 * 60

#: 화면에 보이는 이름. 인증기가 자격증명 목록에 이걸 적어 둔다.
RP_NAME = "Ieum"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def from_b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True, slots=True)
class RelyingParty:
    """우리가 누구인지. **서버가 정한다.**

    `rp_id` 는 자격증명이 묶이는 도메인이다. 클라이언트가 고르게 하면 자기
    도메인으로 등록해 두고 그 키로 우리 계정에 들어올 수 있다.
    """

    rp_id: str
    origins: tuple[str, ...]


def relying_party(base_url: str, extra_origins: tuple[str, ...] = ()) -> RelyingParty:
    """앱 주소에서 RP 를 만든다.

    `rp_id` 는 호스트(포트 없음)다. 인증기는 이 값에 자격증명을 묶으므로,
    **같은 앱을 다른 호스트로 열면 등록한 키가 보이지 않는다** — 개발에서
    `localhost` 와 `127.0.0.1` 을 섞어 쓰면 그 자리에서 걸린다. 그래서 오리진은
    `rp_id` 와 호스트가 같은 것만 받아들인다: 나머지는 어차피 인증기가 거절하고,
    여기서 받아 두면 "왜 안 되는지" 가 더 늦게 드러난다.
    """
    parsed = urlsplit(base_url)
    rp_id = parsed.hostname or "localhost"
    origins = [f"{parsed.scheme}://{parsed.netloc}"]
    for origin in extra_origins:
        other = urlsplit(origin)
        if other.hostname == rp_id:
            candidate = f"{other.scheme}://{other.netloc}"
            if candidate not in origins:
                origins.append(candidate)
    return RelyingParty(rp_id=rp_id, origins=tuple(origins))


@dataclass(frozen=True, slots=True)
class Challenge:
    """발급한 챌린지와 화면에 넘길 옵션(JSON 문자열)."""

    challenge: str
    options_json: str


def registration_challenge(
    *,
    rp: RelyingParty,
    user_id: bytes,
    user_name: str,
    display_name: str,
    already_registered: tuple[str, ...] = (),
) -> Challenge:
    """등록 옵션. 이미 등록된 자격증명은 **제외 목록**으로 보낸다.

    안 보내면 같은 인증기를 두 번 등록하게 되고, 사람은 목록에 같은 것이 둘
    있는 이유를 알 수 없다. 인증기가 스스로 거절해 주도록 알려 준다.
    """
    options = wan.generate_registration_options(
        rp_id=rp.rp_id,
        rp_name=RP_NAME,
        user_id=user_id,
        user_name=user_name,
        user_display_name=display_name,
        # 사용자 확인(PIN·생체)을 **요구**한다. 2차 요소가 "꽂혀 있음" 만
        # 뜻하면, 기기를 집어 든 사람이 그대로 통과한다.
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED,
            # 패스키를 선호한다: 기기를 잃어도 계정에 남는다. 강제하지는
            # 않는다 — 보안 키만 쓰는 조직을 막을 이유가 없다.
            resident_key=ResidentKeyRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=from_b64url(cid)) for cid in already_registered
        ],
    )
    return Challenge(challenge=b64url(options.challenge), options_json=options_to_json(options))


def authentication_challenge(*, rp: RelyingParty, credential_ids: tuple[str, ...]) -> Challenge:
    """인증 옵션. 이 사람이 가진 자격증명만 후보로 준다."""
    options = wan.generate_authentication_options(
        rp_id=rp.rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=from_b64url(cid)) for cid in credential_ids
        ],
    )
    return Challenge(challenge=b64url(options.challenge), options_json=options_to_json(options))


@dataclass(frozen=True, slots=True)
class Registered:
    """검증을 통과한 등록. 이 값들을 그대로 저장한다."""

    credential_id: str
    public_key: str
    sign_count: int
    transports: list[str]
    backed_up: bool


def verify_registration(*, response_json: str, rp: RelyingParty, challenge: str) -> Registered:
    """등록 응답을 검증한다. 통과하지 못하면 예외다."""
    try:
        verified = wan.verify_registration_response(
            credential=response_json,
            expected_challenge=from_b64url(challenge),
            expected_rp_id=rp.rp_id,
            expected_origin=list(rp.origins),
            # 사용자 확인을 **요구했으니 확인한다.** 요구만 하고 결과를 안 보면
            # 요구하지 않은 것과 같다.
            require_user_verification=True,
        )
    except (InvalidRegistrationResponse, ValueError) as exc:
        log.warning("auth.webauthn_registration_rejected", error=type(exc).__name__)
        raise ValidationError(
            "인증기 등록을 확인할 수 없다.", code="auth.webauthn_invalid_registration"
        ) from exc

    return Registered(
        credential_id=b64url(verified.credential_id),
        public_key=b64url(verified.credential_public_key),
        sign_count=verified.sign_count,
        transports=_transports(response_json),
        backed_up=bool(verified.credential_backed_up),
    )


def _transports(response_json: str) -> list[str]:
    """응답이 알려 준 전송 방식. 화면 표시용이라 없으면 비운다."""
    try:
        raw = json.loads(response_json).get("response", {}).get("transports") or []
    except (ValueError, AttributeError):
        return []
    allowed = {t.value for t in AuthenticatorTransport}
    return [str(t) for t in raw if str(t) in allowed]


@dataclass(frozen=True, slots=True)
class Authenticated:
    """검증을 통과한 인증. 카운터를 저장하는 쪽이 쓴다."""

    credential_id: str
    new_sign_count: int


def verify_authentication(
    *,
    response_json: str,
    rp: RelyingParty,
    challenge: str,
    public_key: str,
    current_sign_count: int,
) -> Authenticated:
    """인증 응답을 검증한다.

    카운터가 **되돌아가면** 복제를 의심한다. 다만 0 을 계속 주는 인증기가
    있어서(패스키 대부분) 0 은 검사에서 빼야 한다 — 안 빼면 정상 로그인이
    복제로 걸린다. 라이브러리가 그 규칙을 들고 있으므로 맡긴다.
    """
    try:
        verified = wan.verify_authentication_response(
            credential=response_json,
            expected_challenge=from_b64url(challenge),
            expected_rp_id=rp.rp_id,
            expected_origin=list(rp.origins),
            credential_public_key=from_b64url(public_key),
            credential_current_sign_count=current_sign_count,
            require_user_verification=True,
        )
    except (InvalidAuthenticationResponse, ValueError) as exc:
        log.warning("auth.webauthn_authentication_rejected", error=type(exc).__name__)
        raise AuthenticationError(
            "인증기 응답을 확인할 수 없다.", code="auth.webauthn_invalid_assertion"
        ) from exc

    return Authenticated(
        credential_id=b64url(verified.credential_id), new_sign_count=verified.new_sign_count
    )


def credential_id_of(response_json: str) -> str:
    """응답이 말하는 자격증명 ID. **검증 전**이므로 찾는 데만 쓴다.

    어느 공개키로 검증할지 고르려면 먼저 알아야 한다. 값을 믿는 것이 아니다:
    고른 키가 틀렸다면 서명 검증이 실패한다.
    """
    try:
        raw = json.loads(response_json)["id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValidationError(
            "인증기 응답을 읽을 수 없다.", code="auth.webauthn_invalid_assertion"
        ) from exc
    return str(raw)

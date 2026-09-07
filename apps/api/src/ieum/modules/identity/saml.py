"""SAML 2.0 SP (auth.md 4절).

**XML 서명 검증은 직접 만들지 않는다.** 정규화(c14n)와 서명 래핑 공격(XSW)이
얽혀 있어, 손으로 쓰면 "서명이 붙어 있다" 와 "이 문서가 서명됐다" 를 구분하지
못한다 — 서명은 진짜인데 우리가 읽는 어설션은 공격자가 끼운 것일 수 있다.
어설션 암호화(xmlenc)도 같다. `python3-saml`(xmlsec) 에 맡긴다.

여기서 하는 일은 셋이다: 설정을 IdP 행에서 만들어 주고, 라이브러리가 안 보는
것(재생 방지)을 채우고, 통과한 결과를 `Claims` 로 옮긴다.

OIDC 와 같은 `Claims` 를 낸다. 그래야 검증 뒤의 처리(사람 찾기·계정 만들기·
그룹 맞추기)가 한 벌로 남는다 — 두 벌이 되면 한쪽만 고치는 날이 온다.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.constants import OneLogin_Saml2_Constants
from onelogin.saml2.settings import OneLogin_Saml2_Settings
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from onelogin.saml2.xml_utils import OneLogin_Saml2_XML

from ieum.core.exceptions import AuthenticationError, ValidationError
from ieum.modules.identity.sso import Claims

#: 허용 시계 오차. 라이브러리 기본값은 300초인데 auth.md 4절은 60초로 정한다 —
#: 넓히면 만료된 어설션이 그만큼 더 오래 통한다. 모듈 상수라 여기서 바꾼다.
CLOCK_SKEW_SECONDS = 60
OneLogin_Saml2_Constants.ALLOWED_CLOCK_DRIFT = CLOCK_SKEW_SECONDS

POST_BINDING = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
REDIRECT_BINDING = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"

#: 서명·요약 알고리즘. SHA-1 은 넣지 않는다.
SIGNATURE_ALGORITHM = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
DIGEST_ALGORITHM = "http://www.w3.org/2001/04/xmlenc#sha256"

#: PEM 껍데기와 공백을 벗긴 순수 base64. 라이브러리가 이 꼴을 원한다.
_PEM_BODY = re.compile(r"-----BEGIN [^-]+-----(.*?)-----END [^-]+-----", re.DOTALL)


def normalize_certificate(value: str) -> str:
    """PEM 이든 base64 덩어리든 한 줄 base64 로 만든다.

    관리자는 IdP 화면에서 복사해 붙인다 — PEM 헤더가 붙은 것, 줄바꿈만 있는 것,
    공백이 섞인 것이 다 온다. 여기서 한 꼴로 만들지 않으면 "인증서가 틀렸다" 가
    실제로는 "줄바꿈이 있었다" 인 경우를 구분할 수 없다.
    """
    match = _PEM_BODY.search(value)
    body = match.group(1) if match else value
    stripped = "".join(body.split())
    if not stripped:
        raise ValidationError("인증서가 비어 있다.", code="identity.saml_certificate_invalid")
    try:
        base64.b64decode(stripped, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValidationError(
            "인증서를 base64 로 읽을 수 없다.", code="identity.saml_certificate_invalid"
        ) from exc
    return stripped


@dataclass(frozen=True, slots=True)
class SpEndpoints:
    """우리(SP) 쪽 주소. **서버가 정한다.**

    클라이언트가 준 값을 쓰면 공격자가 자기 ACS 를 넣고 흐름을 시작해 어설션을
    가져간다. IdP 도 등록된 ACS 만 쓰지만, 느슨한 IdP 가 있어 여기서도 막는다.
    """

    entity_id: str
    acs_url: str


def sp_endpoints(api_base_url: str) -> SpEndpoints:
    base = api_base_url.rstrip("/")
    return SpEndpoints(entity_id=f"{base}/saml/metadata", acs_url=f"{base}/api/v1/auth/saml/acs")


@dataclass(frozen=True, slots=True)
class IdpConfig:
    """IdP 행에서 뽑은 SAML 설정. 모델을 여기까지 들고 오지 않는다."""

    entity_id: str
    sso_url: str
    #: 서명 검증용. 여럿 받는다 — 회전 중에는 두 개가 동시에 유효하다.
    certificates: tuple[str, ...]
    #: 암호화된 어설션을 열 우리 키. 없으면 암호화를 요구하지 않는다.
    sp_private_key: str | None = None
    sp_certificate: str | None = None
    want_assertions_encrypted: bool = False
    email_attribute: str = "email"
    name_attribute: str = "name"
    groups_attribute: str | None = None


def settings_for(idp: IdpConfig, sp: SpEndpoints) -> dict[str, Any]:
    """라이브러리 설정. **켜는 것보다 끄지 않는 것이 중요하다.**

    `strict` 를 끄면 검증이 통째로 느슨해진다. `wantAssertionsSigned` 를 끄면
    서명 없는 어설션이 통과한다 — auth.md 4절이 "서명 검증 필수" 라고 적은 자리다.
    """
    return {
        # 검증을 느슨하게 하지 않는다. 끄면 Destination·Conditions 를 안 본다.
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": sp.entity_id,
            "assertionConsumerService": {"url": sp.acs_url, "binding": POST_BINDING},
            "x509cert": idp.sp_certificate or "",
            "privateKey": idp.sp_private_key or "",
        },
        "idp": {
            "entityId": idp.entity_id,
            "singleSignOnService": {"url": idp.sso_url, "binding": REDIRECT_BINDING},
            # 회전 중에는 둘 다 유효하다. 하나만 받으면 교체하는 날 로그인이 끊긴다.
            "x509certMulti": {"signing": list(idp.certificates)},
        },
        "security": {
            # 어설션 서명은 **반드시** 받는다.
            "wantAssertionsSigned": True,
            "wantAssertionsEncrypted": idp.want_assertions_encrypted,
            "wantNameId": True,
            # 응답 전체 서명은 IdP 마다 다르다. 어설션이 서명돼 있으면 충분하다.
            "wantMessagesSigned": False,
            "wantNameIdEncrypted": False,
            "requestedAuthnContext": False,
            "signMetadata": False,
            "signatureAlgorithm": SIGNATURE_ALGORITHM,
            "digestAlgorithm": DIGEST_ALGORITHM,
            # 같은 이름의 속성이 여러 번 오면 거절한다. 허용하면 뒤에 온 값이
            # 앞을 덮어, 속성 주입으로 이메일을 갈아 끼울 수 있다.
            "allowRepeatAttributeName": False,
        },
    }


def _request_data(url: str, post: dict[str, str]) -> dict[str, Any]:
    """라이브러리가 원하는 요청 모양. **주소는 우리가 아는 ACS 로 준다.**

    프록시 뒤에서는 Host 헤더가 내부 이름일 수 있다. 그걸 그대로 넘기면
    Destination 검증이 우리 잘못으로 실패한다 — 설정에 적힌 ACS 로 맞춘다.
    """
    parsed = urlparse(url)
    return {
        "https": "on" if parsed.scheme == "https" else "off",
        # 포트는 호스트에 붙여 준다. `server_port` 로 따로 주는 길은 라이브러리가
        # 걷어내는 중이고, 둘로 나누면 Destination 비교에서 어긋난다.
        "http_host": parsed.netloc,
        "script_name": parsed.path,
        "get_data": {},
        "post_data": post,
    }


@dataclass(frozen=True, slots=True)
class Verified:
    """검증을 통과한 어설션에서 꺼낸 것."""

    claims: Claims
    #: 재생 방지에 쓴다. 같은 어설션을 두 번 받으면 두 번째를 거절한다.
    assertion_id: str
    expires_at: datetime | None
    #: SP-initiated 였다면 우리가 보낸 AuthnRequest 의 ID.
    in_response_to: str | None


def _with_relay_state(url: str, relay_state: str) -> str:
    """`RelayState` 를 **갈아 끼운다.**

    라이브러리는 요청 ID 를 만든 뒤에야 알려 주므로 미리 넘길 수 없고, 그
    사이에 자기 값(우리 ACS 주소)을 `RelayState` 에 이미 채워 둔다. 그래서
    뒤에 하나 더 붙이면 파라미터가 둘이 되고, **어느 것을 읽는지가 IdP 마다
    다르다** — 첫 값을 읽는 IdP 에서는 우리 값이 무시되고, 그러면 돌아온
    응답을 우리가 시작한 흐름과 묶을 수 없다.
    """
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "RelayState"]
    query.append(("RelayState", relay_state))
    return urlunsplit(parts._replace(query=urlencode(query)))


def authn_request(idp: IdpConfig, sp: SpEndpoints) -> tuple[str, str]:
    """(리다이렉트 URL, 요청 ID). 요청 ID 는 `InResponseTo` 로 돌아온다.

    `RelayState` 에 요청 ID 를 싣는다. 규격이 80바이트로 제한하므로 봉인한
    상태를 넣을 수 없고, 넣을 필요도 없다 — 이 ID 는 우리 DB 의 행을 가리키는
    열쇠일 뿐이고, 위조해도 그 행이 없으면 걸린다.
    """
    auth = OneLogin_Saml2_Auth(_request_data(sp.acs_url, {}), old_settings=settings_for(idp, sp))
    url = auth.login()
    request_id = auth.get_last_request_id()
    if not request_id:
        raise AuthenticationError("AuthnRequest 를 만들지 못했다.", code="auth.saml_request_failed")
    return _with_relay_state(url, request_id), request_id


def metadata_xml(idp: IdpConfig, sp: SpEndpoints) -> str:
    """IdP 에 등록할 SP 메타데이터. 손으로 값을 옮겨 적게 하지 않는다."""
    settings = OneLogin_Saml2_Settings(settings_for(idp, sp), sp_validation_only=True)
    xml = settings.get_sp_metadata()
    errors = settings.validate_metadata(xml)
    if errors:
        raise ValidationError(
            f"SP 메타데이터가 규격에 맞지 않는다: {errors}", code="identity.saml_metadata_invalid"
        )
    return xml.decode() if isinstance(xml, bytes) else str(xml)


def verify_response(
    *,
    saml_response: str,
    idp: IdpConfig,
    sp: SpEndpoints,
    request_id: str | None,
) -> Verified:
    """어설션을 검증하고 클레임을 꺼낸다. 통과하지 못하면 예외다.

    `request_id` 가 있으면 SP-initiated 다 — 우리가 보낸 요청에 대한 답인지
    (`InResponseTo`) 까지 본다. 없으면 IdP-initiated 이고, 그건 호출하는 쪽이
    허용한 경우에만 온다.
    """
    auth = OneLogin_Saml2_Auth(
        _request_data(sp.acs_url, {"SAMLResponse": saml_response}),
        old_settings=settings_for(idp, sp),
    )
    auth.process_response(request_id=request_id)

    errors = auth.get_errors()
    if errors:
        # 이유는 로그로만 남긴다. 응답에 실으면 검증기를 더듬을 수 있다.
        raise AuthenticationError(
            "SAML 응답을 신뢰할 수 없다.",
            code="auth.saml_invalid_response",
            details={"reason": auth.get_last_error_reason() or ",".join(errors)},
        )
    if not auth.is_authenticated():
        raise AuthenticationError("SAML 인증이 성립하지 않았다.", code="auth.saml_invalid_response")

    assertion_id = auth.get_last_assertion_id()
    if not assertion_id:
        # ID 가 없으면 재생을 막을 방법이 없다. 통과시키지 않는다.
        raise AuthenticationError("어설션에 ID 가 없다.", code="auth.saml_invalid_response")

    return Verified(
        claims=read_attributes(
            name_id=auth.get_nameid(),
            attributes=auth.get_attributes(),
            idp=idp,
        ),
        assertion_id=str(assertion_id),
        expires_at=_not_on_or_after(auth),
        in_response_to=auth.get_last_response_in_response_to(),
    )


def _not_on_or_after(auth: OneLogin_Saml2_Auth) -> datetime | None:
    raw = auth.get_last_assertion_not_on_or_after()
    if raw is None:
        return None
    # 라이브러리는 epoch 초를 준다.
    return datetime.fromtimestamp(float(raw), tz=UTC)


def _first(values: Any) -> str | None:
    """SAML 속성은 항상 리스트다. 첫 값만 쓴다 — 여럿이면 IdP 설정 문제다."""
    if isinstance(values, list):
        return str(values[0]).strip() if values and values[0] is not None else None
    return str(values).strip() if values is not None else None


def read_attributes(*, name_id: str | None, attributes: dict[str, Any], idp: IdpConfig) -> Claims:
    """속성을 우리 클레임으로 옮긴다.

    `NameID` 가 곧 주체다. OIDC 의 `sub` 와 같은 자리 — IdP 안에서 바뀌지 않는
    값이어야 하고, 우리는 이걸로 사람을 찾는다.

    SAML 에는 `email_verified` 가 없다. IdP 가 어설션에 실어 서명한 이메일은
    **IdP 가 보증한 것**이므로 그대로 쓴다 — OIDC 에서 검증 안 된 이메일을
    버리는 것과 모순이 아니다: 거기서 버리는 것은 IdP 가 "확인 안 했다" 고
    스스로 말한 값이다.
    """
    if not name_id:
        raise AuthenticationError(
            "IdP 가 NameID 를 주지 않았다.", code="auth.saml_name_id_required"
        )

    email = _first(attributes.get(idp.email_attribute))
    if email is None and "@" in name_id:
        # NameID 가 이메일 꼴이면 그것을 쓴다. 많은 IdP 의 기본 설정이다.
        email = name_id
    name = _first(attributes.get(idp.name_attribute))

    groups: list[str] = []
    if idp.groups_attribute:
        raw = attributes.get(idp.groups_attribute) or []
        values = raw if isinstance(raw, list) else [raw]
        groups = [str(v).strip() for v in values if str(v).strip()]

    return Claims(
        subject=name_id,
        email=email.lower() if email else None,
        name=name,
        groups=groups,
        # SAML 의 2차 요소 위임은 AuthnContext 로 판단한다. 지금은 위임하지
        # 않는다 — 켜는 정책이 생길 때 여기서 `AuthnContextClassRef` 를 본다.
        mfa_satisfied=False,
    )


def peek_issuer(saml_response: str) -> str | None:
    """**검증하지 않은** 응답에서 발급자만 꺼낸다.

    IdP 화면에서 시작한 로그인은 `InResponseTo` 가 없어 우리 흐름과 묶을 수
    없다. 그러면 어느 IdP 의 응답인지도 모르는데, 검증에는 그 IdP 의 인증서가
    필요하다 — 닭과 알이다.

    그래서 발급자만 먼저 읽어 IdP 를 고르고, **그 IdP 의 인증서로 검증한다.**
    여기서 읽은 값은 어떤 판단에도 쓰지 않는다: 고른 IdP 가 틀렸다면 검증이
    실패하고, 맞았다면 검증이 그 사실을 확인해 준다. 값을 그대로 믿는 자리는
    없다.
    """
    try:
        decoded = OneLogin_Saml2_Utils.b64decode(saml_response)
        # 라이브러리의 안전한 파서를 쓴다. 외부 엔티티·DTD 를 타지 않는다.
        root = OneLogin_Saml2_XML.to_etree(decoded)
        found = OneLogin_Saml2_XML.query(root, "/samlp:Response/saml:Issuer")
    except Exception:
        # 못 읽으면 IdP 를 못 고르는 것뿐이다. 여기서 터뜨릴 이유가 없다.
        return None
    text = found[0].text if found else None
    return str(text).strip() if text else None


@dataclass(frozen=True, slots=True)
class IdpMetadata:
    """IdP 메타데이터 XML 에서 읽은 것."""

    entity_id: str
    sso_url: str
    certificates: tuple[str, ...]


def read_idp_metadata(xml: str) -> IdpMetadata:
    """IdP 메타데이터를 읽는다. **손으로 옮겨 적게 하지 않으려는 것이다.**

    발급자·SSO 주소·서명 인증서를 사람이 세 칸에 붙여 넣는 동안 한 글자가
    틀리면, 로그인이 안 되는 이유가 "인증서가 틀렸다" 로만 보인다. IdP 가
    주는 XML 하나로 셋을 다 채운다.

    여기서 읽은 값은 **설정**이다. 관리자가 step-up 을 통과해 넣은 것이므로
    신뢰 결정에 쓰이는 인증서의 출처는 관리자다 — 어설션 검증은 이 인증서로
    한다. 아무 XML 이나 받아 자동 등록하는 길은 없다.
    """
    try:
        root = OneLogin_Saml2_XML.to_etree(xml.encode() if isinstance(xml, str) else xml)
    except Exception as exc:
        raise ValidationError(
            "메타데이터 XML 을 읽을 수 없다.", code="identity.saml_metadata_invalid"
        ) from exc

    entity_id = str(root.get("entityID") or "").strip()
    sso = OneLogin_Saml2_XML.query(
        root,
        "//md:IDPSSODescriptor/md:SingleSignOnService"
        "[@Binding='urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect']",
    )
    if not sso:
        # POST 바인딩만 주는 IdP 도 있다. 우리는 리다이렉트로 보내지만
        # 주소는 같은 곳을 가리키는 경우가 많아 받아 둔다.
        sso = OneLogin_Saml2_XML.query(root, "//md:IDPSSODescriptor/md:SingleSignOnService")
    sso_url = str(sso[0].get("Location") or "").strip() if sso else ""

    certificates = tuple(
        normalize_certificate(str(node.text))
        for node in OneLogin_Saml2_XML.query(root, "//md:IDPSSODescriptor//ds:X509Certificate")
        if node.text and str(node.text).strip()
    )

    if not (entity_id and sso_url and certificates):
        raise ValidationError(
            "메타데이터에 발급자·SSO 주소·서명 인증서가 모두 있어야 한다.",
            code="identity.saml_metadata_incomplete",
        )
    # 같은 인증서가 signing·encryption 양쪽에 적혀 오는 IdP 가 있다.
    return IdpMetadata(
        entity_id=entity_id, sso_url=sso_url, certificates=tuple(dict.fromkeys(certificates))
    )

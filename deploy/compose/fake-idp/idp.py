"""개발·시험용 가짜 IdP (OIDC + SAML 2.0).

**개발 스택에만 있다.** mailpit·MinIO 와 같은 자리의 도구다 — 실제 IdP 없이
SSO 경로 전체(인가 리다이렉트 → 코드 교환 → ID 토큰 검증)를 돌리기 위한
것이고, 그래서 서명 키를 기동할 때마다 새로 만든다. 운영 오버라이드
(deploy/compose/) 에는 들어가지 않는다.

브라우저와 서버가 **다른 주소로** 이 IdP 를 본다. 브라우저는 공개된 포트로
(`localhost:9099`), API 는 컴포즈 네트워크 안에서 서비스 이름으로
(`fake-idp:9099`). `iss` 는 하나여야 하므로 브라우저가 보는 주소로 고정한다 —
MinIO 의 presigned 주소와 같은 문제다.

SAML 은 **어설션에 실제로 XML 서명을 붙인다.** 흉내 내면 검증을 통째로 꺼도
브라우저 테스트가 통과한다 — 서명 경로가 실제로 실행돼야 값이 있다.
"""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from onelogin.saml2.xml_utils import OneLogin_Saml2_XML

PORT = int(os.environ.get("FAKE_IDP_PORT", "9099"))
#: ID 토큰의 `iss`. 브라우저가 보는 주소여야 한다.
ISSUER = os.environ.get("FAKE_IDP_ISSUER", f"http://localhost:{PORT}")
CLIENT_ID = os.environ.get("FAKE_IDP_CLIENT_ID", "ieum-dev")
#: SAML 의 발급자. OIDC 의 `iss` 와 별개 값이다(규격이 다르다).
SAML_ENTITY_ID = os.environ.get("FAKE_IDP_SAML_ENTITY_ID", f"{ISSUER}/saml/metadata")

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_numbers = KEY.public_key().public_numbers()


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


JWKS = {
    "keys": [
        {
            "kty": "RSA",
            "kid": "fake-1",
            "use": "sig",
            "alg": "RS256",
            "n": _b64(_numbers.n),
            "e": _b64(_numbers.e),
        }
    ]
}

#: 발급한 코드 → (nonce, 사람). 코드는 1회용이다.
PENDING: dict[str, tuple[str, dict[str, Any]]] = {}

#: SAML 서명용 인증서. 키는 OIDC 와 같은 것을 쓴다 — 개발용이다.
_NAME = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-idp")])
CERTIFICATE_PEM = (
    x509.CertificateBuilder()
    .subject_name(_NAME)
    .issuer_name(_NAME)
    .public_key(KEY.public_key())
    .serial_number(1)
    .not_valid_before(datetime.now(UTC) - timedelta(days=1))
    .not_valid_after(datetime.now(UTC) + timedelta(days=365))
    .sign(KEY, hashes.SHA256())
    .public_bytes(serialization.Encoding.PEM)
    .decode()
)
KEY_PEM = KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
).decode()
#: 메타데이터에 실을 base64 본문.
CERTIFICATE_BODY = "".join(CERTIFICATE_PEM.strip().splitlines()[1:-1])

SAML_NS = (
    'xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
    'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"'
)
#: 어설션 ID 는 매번 새로 만든다. 같은 것을 두 번 내면 재생으로 걸린다.
_saml_serial = 0

#: 로그인할 사람. 쿼리로 갈아 끼울 수 있어 여러 시나리오를 돌린다.
DEFAULT_PERSON = {
    "sub": "fake-person-1",
    "email": "sso.person@corp.example.com",
    "email_verified": True,
    "name": "SSO Person",
    "groups": ["engineering"],
}


#: SAML 로 들어오는 사람. OIDC 와 **다른 사람**이다 — 같은 이메일로 두면
#: 검증된 이메일 연결 정책 때문에 한 계정에 두 신분이 붙고, 그러면 브라우저
#: 테스트가 JIT 프로비저닝을 지나가면서도 지나간 줄 모른다.
#:
#: `sub` 가 곧 NameID 다. Format 을 emailAddress 로 적으므로 값도 그 꼴이어야
#: 한다 — 규격과 값이 어긋나면 까다로운 IdP·SP 조합에서 걸린다.
DEFAULT_SAML_PERSON = {
    "sub": "saml.person@corp.example.com",
    "email": "saml.person@corp.example.com",
    "email_verified": True,
    "name": "SAML Person",
    "groups": ["engineering"],
}


def _person_from(query: dict[str, list[str]], *, saml: bool = False) -> dict[str, Any]:
    """시나리오를 쿼리로 갈아 끼운다: 다른 사람, 검증 안 된 메일, 다른 그룹."""
    person = dict(DEFAULT_SAML_PERSON if saml else DEFAULT_PERSON)
    for key in ("sub", "email", "name"):
        if key in query:
            person[key] = query[key][0]
    if "groups" in query:
        person["groups"] = [g for g in query["groups"][0].split(",") if g]
    if query.get("email_verified", [""])[0] == "false":
        person["email_verified"] = False
    return person


def _read_authn_request(encoded: str) -> tuple[str, str, str | None]:
    """(ACS 주소, 수신자, 요청 ID). SP 가 보낸 요청에서 읽는다.

    **SP 가 준 주소를 그대로 쓴다.** 진짜 IdP 는 등록된 ACS 만 쓰지만, 여기서
    그렇게 하면 개발 스택 주소를 IdP 설정에 또 적어 두어야 한다 — 한쪽만
    고쳐 두는 자리를 하나 더 만드는 셈이다.
    """
    try:
        inflated = OneLogin_Saml2_Utils.decode_base64_and_inflate(encoded)
        # 라이브러리의 안전한 파서를 쓴다. 외부 엔티티·DTD 를 타지 않는다.
        root = OneLogin_Saml2_XML.to_etree(inflated)
    except Exception:
        return "", "", None
    return (
        root.get("AssertionConsumerServiceURL") or "",
        next(
            (
                node.text or ""
                for node in root.iter("{urn:oasis:names:tc:SAML:2.0:assertion}Issuer")
            ),
            "",
        ),
        root.get("ID"),
    )


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def saml_metadata() -> str:
    """IdP 메타데이터. 관리 화면에 그대로 붙여 넣을 수 있다."""
    return (
        '<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"'
        ' xmlns:ds="http://www.w3.org/2000/09/xmldsig#"'
        f' entityID="{SAML_ENTITY_ID}">'
        "<md:IDPSSODescriptor protocolSupportEnumeration="
        '"urn:oasis:names:tc:SAML:2.0:protocol">'
        '<md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data>'
        f"<ds:X509Certificate>{CERTIFICATE_BODY}</ds:X509Certificate>"
        "</ds:X509Data></ds:KeyInfo></md:KeyDescriptor>"
        "<md:SingleSignOnService"
        ' Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"'
        f' Location="{ISSUER}/saml/sso"/>'
        "</md:IDPSSODescriptor></md:EntityDescriptor>"
    )


def saml_response(
    *, acs_url: str, audience: str, in_response_to: str | None, person: dict[str, Any]
) -> str:
    """서명한 어설션을 감싼 `SAMLResponse`(base64).

    **어설션에 서명한다** — 응답 껍데기만 서명하면 안의 어설션을 갈아 끼울 수
    있고, 우리 SP 는 그걸 거절한다(그게 맞다).
    """
    global _saml_serial
    _saml_serial += 1
    assertion_id = f"_assertion-{int(time.time() * 1_000_000)}-{_saml_serial}"

    now = datetime.now(UTC)
    until = _stamp(now + timedelta(minutes=5))
    confirm_in = f' InResponseTo="{in_response_to}"' if in_response_to else ""

    groups = "".join(
        f"<saml:AttributeValue>{g}</saml:AttributeValue>" for g in person.get("groups", [])
    )
    attributes = (
        f'<saml:Attribute Name="email"><saml:AttributeValue>{person["email"]}'
        "</saml:AttributeValue></saml:Attribute>"
        f'<saml:Attribute Name="name"><saml:AttributeValue>{person["name"]}'
        "</saml:AttributeValue></saml:Attribute>"
        + (f'<saml:Attribute Name="groups">{groups}</saml:Attribute>' if groups else "")
    )

    assertion = (
        f'<saml:Assertion {SAML_NS} ID="{assertion_id}" Version="2.0"'
        f' IssueInstant="{_stamp(now)}">'
        f"<saml:Issuer>{SAML_ENTITY_ID}</saml:Issuer>"
        "<saml:Subject>"
        '<saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">'
        f"{person['sub']}</saml:NameID>"
        '<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
        f'<saml:SubjectConfirmationData NotOnOrAfter="{until}"'
        f' Recipient="{acs_url}"{confirm_in}/>'
        "</saml:SubjectConfirmation></saml:Subject>"
        f'<saml:Conditions NotBefore="{_stamp(now)}" NotOnOrAfter="{until}">'
        f"<saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience>"
        "</saml:AudienceRestriction></saml:Conditions>"
        f'<saml:AuthnStatement AuthnInstant="{_stamp(now)}" SessionIndex="_s{assertion_id}">'
        "<saml:AuthnContext><saml:AuthnContextClassRef>"
        "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"
        "</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>"
        f"<saml:AttributeStatement>{attributes}</saml:AttributeStatement>"
        "</saml:Assertion>"
    )
    signed = OneLogin_Saml2_Utils.add_sign(
        assertion,
        KEY_PEM,
        CERTIFICATE_PEM,
        sign_algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
        digest_algorithm="http://www.w3.org/2001/04/xmlenc#sha256",
    )
    inner = signed.decode() if isinstance(signed, bytes) else str(signed)
    if inner.startswith("<?"):
        inner = inner.split("?>", 1)[-1].strip()

    response_in = f' InResponseTo="{in_response_to}"' if in_response_to else ""
    xml = (
        f'<samlp:Response {SAML_NS} ID="_response{assertion_id}" Version="2.0"'
        f' IssueInstant="{_stamp(now)}" Destination="{acs_url}"{response_in}>'
        f"<saml:Issuer>{SAML_ENTITY_ID}</saml:Issuer>"
        "<samlp:Status><samlp:StatusCode"
        ' Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
        f"{inner}</samlp:Response>"
    )
    return base64.b64encode(xml.encode()).decode()


def post_form(*, acs_url: str, saml: str, relay_state: str | None) -> bytes:
    """스스로 보내는 폼. SAML 의 HTTP-POST 바인딩은 이렇게 생겼다."""
    relay = f'<input type="hidden" name="RelayState" value="{relay_state}"/>' if relay_state else ""
    return (
        '<!doctype html><html><body onload="document.forms[0].submit()">'
        f'<form method="post" action="{acs_url}">'
        f'<input type="hidden" name="SAMLResponse" value="{saml}"/>'
        f"{relay}"
        '<noscript><button type="submit">Continue</button></noscript>'
        "</form></body></html>"
    ).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        return  # 컴포즈 로그를 요청마다 채우지 않는다

    def _send(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # 이름은 BaseHTTPRequestHandler 규약이다. 바꾸면 호출되지 않는다.
    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/jwks":
            self._send(json.dumps(JWKS).encode())
            return
        if url.path == "/.well-known/openid-configuration":
            self._send(
                json.dumps(
                    {
                        "issuer": ISSUER,
                        "authorization_endpoint": f"{ISSUER}/authorize",
                        "token_endpoint": f"{ISSUER}/token",
                        "jwks_uri": f"{ISSUER}/jwks",
                    }
                ).encode()
            )
            return
        if url.path == "/saml/metadata":
            body = saml_metadata().encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/samlmetadata+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/saml/sso":
            # SP 가 리다이렉트 바인딩으로 보낸 AuthnRequest. 여기서는 요청을
            # 검증하지 않는다 — 개발용이고, 검증되는 쪽은 우리 SP 다.
            query = parse_qs(url.query)
            request = query.get("SAMLRequest", [""])[0]
            relay_state = query.get("RelayState", [""])[0] or None
            person = _person_from(query, saml=True)
            acs, audience, request_id = _read_authn_request(request)
            body = post_form(
                acs_url=acs,
                saml=saml_response(
                    acs_url=acs,
                    audience=audience,
                    in_response_to=request_id,
                    person=person,
                ),
                relay_state=relay_state,
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/authorize":
            query = parse_qs(url.query)
            person = _person_from(query)
            code = f"fake-code-{int(time.time() * 1_000_000)}"
            PENDING[code] = (query.get("nonce", [""])[0], person)
            target = f"{query['redirect_uri'][0]}?code={code}&state={query['state'][0]}"
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        self._send(b'{"error":"not_found"}', 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode())
        code = form.get("code", [""])[0]
        # 1회용이다. 두 번째는 실패해야 진짜 IdP 처럼 굴러간다.
        if code not in PENDING:
            self._send(b'{"error":"invalid_grant"}', 400)
            return
        nonce, person = PENDING.pop(code)

        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "exp": now + 300,
            "iat": now,
            "nonce": nonce,
            **person,
        }
        token = jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "fake-1"})
        self._send(
            json.dumps({"access_token": "fake", "token_type": "Bearer", "id_token": token}).encode()
        )


if __name__ == "__main__":
    print(f"fake IdP listening on :{PORT}, iss={ISSUER}", flush=True)  # noqa: T201
    # 컴포즈 네트워크 안에서만 뜬다. 브라우저와 api 가 서로 다른 이름으로
    # 오므로 한 인터페이스에 묶을 수 없다. 개발 스택 전용이다.
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104  # nosec B104

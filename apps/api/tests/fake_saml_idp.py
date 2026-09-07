"""시험용 가짜 SAML IdP.

**흉내 내지 않는다.** 실제로 키를 만들어 어설션에 XML 서명을 붙인다 — 검증
경로가 정말 실행돼야 "서명을 확인한다" 는 주장에 값이 있다. 응답만 그럴듯하게
만들어 두면 검증을 통째로 껐을 때도 테스트가 통과한다.

같은 이유로 시나리오를 여기서 만든다: 서명 없는 응답, 다른 키로 서명한 응답,
다른 발급자, 다른 요청에 대한 응답, 만료된 어설션.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from onelogin.saml2.utils import OneLogin_Saml2_Utils

from ieum.modules.identity import saml

NS = (
    'xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
    'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"'
)


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FakeIdp:
    """키 한 벌과 발급자 이름. 테스트마다 새로 만든다."""

    entity_id: str = "https://fake-idp.example.com/metadata"
    sso_url: str = "https://fake-idp.example.com/sso"
    key: rsa.RSAPrivateKey = field(
        default_factory=lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )

    @property
    def certificate_pem(self) -> str:
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-idp")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.key.public_key())
            .serial_number(1)
            .not_valid_before(datetime.now(UTC) - timedelta(days=1))
            .not_valid_after(datetime.now(UTC) + timedelta(days=365))
            .sign(self.key, hashes.SHA256())
        )
        return cert.public_bytes(serialization.Encoding.PEM).decode()

    @property
    def key_pem(self) -> str:
        return self.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode()

    def config(self, **overrides: object) -> saml.IdpConfig:
        base = {
            "entity_id": self.entity_id,
            "sso_url": self.sso_url,
            "certificates": (saml.normalize_certificate(self.certificate_pem),),
            "groups_attribute": "groups",
        }
        base.update(overrides)
        return saml.IdpConfig(**base)  # type: ignore[arg-type]

    def metadata_xml(self) -> str:
        body = saml.normalize_certificate(self.certificate_pem)
        return (
            '<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"'
            ' xmlns:ds="http://www.w3.org/2000/09/xmldsig#"'
            f' entityID="{self.entity_id}">'
            "<md:IDPSSODescriptor protocolSupportEnumeration="
            '"urn:oasis:names:tc:SAML:2.0:protocol">'
            '<md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data>'
            f"<ds:X509Certificate>{body}</ds:X509Certificate>"
            "</ds:X509Data></ds:KeyInfo></md:KeyDescriptor>"
            "<md:SingleSignOnService"
            ' Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"'
            f' Location="{self.sso_url}"/>'
            "</md:IDPSSODescriptor></md:EntityDescriptor>"
        )

    def sign(self, xml: str, *, key_pem: str | None = None) -> str:
        signed = OneLogin_Saml2_Utils.add_sign(
            xml,
            key_pem or self.key_pem,
            self.certificate_pem,
            sign_algorithm=saml.SIGNATURE_ALGORITHM,
            digest_algorithm=saml.DIGEST_ALGORITHM,
        )
        out = signed.decode() if isinstance(signed, bytes) else str(signed)
        # 서명 뒤 XML 선언이 남으면 응답 안에 감쌀 수 없다.
        return out.split("?>", 1)[-1].strip() if out.startswith("<?") else out

    def response(
        self,
        *,
        acs_url: str,
        audience: str,
        in_response_to: str | None,
        assertion_id: str = "_assertion-1",
        name_id: str = "saml.person@corp.example.com",
        email: str | None = "saml.person@corp.example.com",
        display_name: str | None = "SAML Person",
        groups: tuple[str, ...] = ("engineering",),
        issuer: str | None = None,
        signed: bool = True,
        #: 응답 **전체**만 서명한다. 어설션은 서명하지 않는다.
        sign_response_only: bool = False,
        sign_with: str | None = None,
        valid_for: timedelta = timedelta(minutes=5),
        not_before: timedelta = timedelta(0),
    ) -> str:
        """base64 로 감싼 `SAMLResponse`. 폼에 그대로 실을 수 있다."""
        now = datetime.now(UTC)
        issuer = issuer or self.entity_id
        until = _stamp(now + valid_for)
        confirm_in = f' InResponseTo="{in_response_to}"' if in_response_to else ""

        attributes = []
        if email is not None:
            attributes.append(
                f'<saml:Attribute Name="email">'
                f"<saml:AttributeValue>{email}</saml:AttributeValue></saml:Attribute>"
            )
        if display_name is not None:
            attributes.append(
                f'<saml:Attribute Name="name">'
                f"<saml:AttributeValue>{display_name}</saml:AttributeValue></saml:Attribute>"
            )
        if groups:
            values = "".join(f"<saml:AttributeValue>{g}</saml:AttributeValue>" for g in groups)
            attributes.append(f'<saml:Attribute Name="groups">{values}</saml:Attribute>')

        assertion = (
            f'<saml:Assertion {NS} ID="{assertion_id}" Version="2.0"'
            f' IssueInstant="{_stamp(now)}">'
            f"<saml:Issuer>{issuer}</saml:Issuer>"
            "<saml:Subject>"
            '<saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">'
            f"{name_id}</saml:NameID>"
            '<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
            f'<saml:SubjectConfirmationData NotOnOrAfter="{until}"'
            f' Recipient="{acs_url}"{confirm_in}/>'
            "</saml:SubjectConfirmation></saml:Subject>"
            f'<saml:Conditions NotBefore="{_stamp(now + not_before)}" NotOnOrAfter="{until}">'
            f"<saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience>"
            "</saml:AudienceRestriction></saml:Conditions>"
            f'<saml:AuthnStatement AuthnInstant="{_stamp(now)}" SessionIndex="_s{assertion_id}">'
            "<saml:AuthnContext><saml:AuthnContextClassRef>"
            "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"
            "</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>"
            f"<saml:AttributeStatement>{''.join(attributes)}</saml:AttributeStatement>"
            "</saml:Assertion>"
        )
        if signed and not sign_response_only:
            assertion = self.sign(assertion, key_pem=sign_with)

        response_in = f' InResponseTo="{in_response_to}"' if in_response_to else ""
        xml = (
            f'<samlp:Response {NS} ID="_response{assertion_id}" Version="2.0"'
            f' IssueInstant="{_stamp(now)}" Destination="{acs_url}"{response_in}>'
            f"<saml:Issuer>{issuer}</saml:Issuer>"
            "<samlp:Status><samlp:StatusCode"
            ' Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
            f"{assertion}</samlp:Response>"
        )
        if sign_response_only:
            xml = self.sign(xml, key_pem=sign_with)
        return base64.b64encode(xml.encode()).decode()

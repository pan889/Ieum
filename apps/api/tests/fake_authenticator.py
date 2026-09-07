"""시험용 소프트웨어 인증기 (WebAuthn).

**흉내 내지 않는다.** 실제로 ES256 키를 만들고, 실제 authenticatorData 를
조립하고, 실제로 서명한다 — 검증 경로가 정말 실행돼야 "서명을 확인한다" 는
주장에 값이 있다. 응답만 그럴듯하게 만들어 두면 검증을 통째로 껐을 때도
테스트가 통과한다.

같은 이유로 시나리오를 여기서 만든다: 다른 키로 서명, 다른 오리진, 다른
챌린지, 사용자 확인 없이, 카운터를 되돌린 응답.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from dataclasses import dataclass, field

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

#: authenticatorData 의 플래그 비트.
FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04
FLAG_BACKUP_ELIGIBLE = 0x08
FLAG_BACKED_UP = 0x10
FLAG_ATTESTED_CREDENTIAL = 0x40

#: 소프트웨어 인증기라 AAGUID 는 0 이다(규격이 그렇게 하라고 한다).
AAGUID = b"\x00" * 16


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def from_b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass
class FakeAuthenticator:
    """키 한 벌과 카운터. 테스트마다 새로 만든다."""

    rp_id: str = "localhost"
    origin: str = "http://localhost:5173"
    credential_id: bytes = field(default_factory=lambda: os.urandom(32))
    key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    sign_count: int = 0

    @property
    def credential_id_b64(self) -> str:
        return b64url(self.credential_id)

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def _authenticator_data(
        self, *, flags: int, include_credential: bool, rp_id: str | None = None
    ) -> bytes:
        data = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        data += struct.pack(">BI", flags, self.sign_count)
        if include_credential:
            cose = self._cose_key()
            data += AAGUID + struct.pack(">H", len(self.credential_id))
            data += self.credential_id + cose
        return data

    def _client_data(self, *, kind: str, challenge: str, origin: str | None = None) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": origin or self.origin,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode()

    def register(
        self,
        *,
        challenge: str,
        origin: str | None = None,
        rp_id: str | None = None,
        user_verified: bool = True,
        backed_up: bool = False,
    ) -> str:
        """등록 응답(JSON 문자열). 어테스테이션은 `none` 이다.

        `none` 은 규격이 정한 정상 형식이다 — 어테스테이션 문장 없이 공개키만
        준다. 조직이 특정 인증기 모델만 허용하려면 그때 다른 형식이 필요하고,
        그건 지금 하지 않는다.
        """
        flags = FLAG_USER_PRESENT | FLAG_ATTESTED_CREDENTIAL
        if user_verified:
            flags |= FLAG_USER_VERIFIED
        if backed_up:
            flags |= FLAG_BACKUP_ELIGIBLE | FLAG_BACKED_UP

        authenticator_data = self._authenticator_data(
            flags=flags, include_credential=True, rp_id=rp_id
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": authenticator_data})
        client_data = self._client_data(kind="webauthn.create", challenge=challenge, origin=origin)
        return json.dumps(
            {
                "id": self.credential_id_b64,
                "rawId": self.credential_id_b64,
                "type": "public-key",
                "response": {
                    "clientDataJSON": b64url(client_data),
                    "attestationObject": b64url(attestation),
                    "transports": ["internal"],
                },
                "clientExtensionResults": {},
            }
        )

    def authenticate(
        self,
        *,
        challenge: str,
        origin: str | None = None,
        rp_id: str | None = None,
        user_verified: bool = True,
        sign_count: int | None = None,
        sign_with: ec.EllipticCurvePrivateKey | None = None,
        credential_id: bytes | None = None,
    ) -> str:
        """인증 응답(JSON 문자열). 실제로 서명한다."""
        if sign_count is not None:
            self.sign_count = sign_count
        else:
            self.sign_count += 1

        flags = FLAG_USER_PRESENT | (FLAG_USER_VERIFIED if user_verified else 0)
        authenticator_data = self._authenticator_data(
            flags=flags, include_credential=False, rp_id=rp_id
        )
        client_data = self._client_data(kind="webauthn.get", challenge=challenge, origin=origin)
        digest = hashlib.sha256(authenticator_data + hashlib.sha256(client_data).digest())
        signature = (sign_with or self.key).sign(
            digest.digest(), ec.ECDSA(Prehashed(hashes.SHA256()))
        )
        shown = b64url(credential_id or self.credential_id)
        return json.dumps(
            {
                "id": shown,
                "rawId": shown,
                "type": "public-key",
                "response": {
                    "clientDataJSON": b64url(client_data),
                    "authenticatorData": b64url(authenticator_data),
                    "signature": b64url(signature),
                    "userHandle": None,
                },
                "clientExtensionResults": {},
            }
        )

"""애플리케이션 레벨 암호화·해싱 (D-15).

- 대칭 암호화: AES-256-GCM. 키는 `IEUM_SECRET_KEY` 에서 HKDF 로 용도별 파생한다.
  같은 마스터 키라도 용도(info)가 다르면 다른 키가 나오므로 교차 복호화가 안 된다.
- 비밀번호: argon2id (docs/architecture/auth.md 2절).
- 토큰: 원문을 저장하지 않고 SHA-256 해시만 저장한다. 조회는 해시로 한다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_NONCE_BYTES = 12


def derive_key(master_secret: str, *, purpose: str) -> bytes:
    """마스터 시크릿에서 용도별 32바이트 키를 파생한다."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"ieum.v1",
        info=purpose.encode("utf-8"),
    )
    return hkdf.derive(master_secret.encode("utf-8"))


class SecretBox:
    """용도별로 분리된 AES-GCM 암복호화기.

    MFA 시크릿, IdP 클라이언트 시크릿, 웹훅 시크릿처럼
    '읽어서 다시 써야 하는' 값에만 쓴다. 비밀번호에는 쓰지 않는다.
    """

    def __init__(self, master_secret: str, *, purpose: str) -> None:
        self._aead = AESGCM(derive_key(master_secret, purpose=purpose))

    def encrypt(self, plaintext: str) -> str:
        """base64(nonce || ciphertext) 를 돌려준다."""
        nonce = os.urandom(_NONCE_BYTES)
        ct = self._aead.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.urlsafe_b64encode(nonce + ct).decode("ascii")

    def decrypt(self, token: str) -> str:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        nonce, ct = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        return self._aead.decrypt(nonce, ct, None).decode("utf-8")


class PasswordHashingService:
    """argon2id 해싱. 파라미터를 올리면 로그인 시 자동 리해시된다."""

    def __init__(self, *, memory_cost: int, time_cost: int, parallelism: int) -> None:
        self._hasher = PasswordHasher(
            memory_cost=memory_cost, time_cost=time_cost, parallelism=parallelism
        )

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True


def hash_token(raw_token: str) -> str:
    """세션 리프레시 토큰·PAT 저장용 해시.

    토큰은 이미 고엔트로피 무작위 값이므로 KDF 가 아니라 SHA-256 이면 충분하고,
    조회 시 인덱스를 탈 수 있어야 하므로 솔트를 쓰지 않는다.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    """타이밍 공격을 피하는 비교."""
    return hmac.compare_digest(a, b)

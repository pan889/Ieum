"""core.crypto — 암호화·해싱."""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from ieum.core.crypto import (
    PasswordHashingService,
    SecretBox,
    constant_time_equals,
    derive_key,
    hash_token,
)

MASTER = "a" * 48


class TestSecretBox:
    def test_roundtrip(self) -> None:
        box = SecretBox(MASTER, purpose="mfa")
        assert box.decrypt(box.encrypt("JBSWY3DPEHPK3PXP")) == "JBSWY3DPEHPK3PXP"

    def test_nonce_is_random(self) -> None:
        """같은 평문이라도 매번 다른 암호문이 나와야 한다."""
        box = SecretBox(MASTER, purpose="mfa")
        assert box.encrypt("same") != box.encrypt("same")

    def test_purpose_isolation(self) -> None:
        """용도가 다르면 교차 복호화가 불가능하다.

        MFA 시크릿 키가 새도 웹훅 시크릿까지 열리지 않는다.
        """
        mfa = SecretBox(MASTER, purpose="mfa")
        webhook = SecretBox(MASTER, purpose="webhook")
        with pytest.raises(InvalidTag):
            webhook.decrypt(mfa.encrypt("secret"))

    def test_tampering_detected(self) -> None:
        """AEAD 이므로 변조된 암호문은 복호화가 실패한다."""
        box = SecretBox(MASTER, purpose="mfa")
        token = box.encrypt("secret")
        tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
        with pytest.raises(Exception):  # noqa: B017 - InvalidTag 또는 디코딩 오류
            box.decrypt(tampered)


class TestDeriveKey:
    def test_purpose_changes_key(self) -> None:
        assert derive_key(MASTER, purpose="a") != derive_key(MASTER, purpose="b")

    def test_deterministic(self) -> None:
        assert derive_key(MASTER, purpose="a") == derive_key(MASTER, purpose="a")

    def test_key_length_is_256_bits(self) -> None:
        assert len(derive_key(MASTER, purpose="a")) == 32


class TestPasswordHashing:
    @pytest.fixture
    def hasher(self) -> PasswordHashingService:
        return PasswordHashingService(memory_cost=8192, time_cost=1, parallelism=1)

    def test_uses_argon2id(self, hasher: PasswordHashingService) -> None:
        assert hasher.hash("password-1234").startswith("$argon2id$")

    def test_verify_roundtrip(self, hasher: PasswordHashingService) -> None:
        h = hasher.hash("correct-horse-battery")
        assert hasher.verify(h, "correct-horse-battery")
        assert not hasher.verify(h, "wrong-password-here")

    def test_salted(self, hasher: PasswordHashingService) -> None:
        """같은 비밀번호라도 해시가 달라야 한다(레인보우 테이블 방어)."""
        assert hasher.hash("same-password") != hasher.hash("same-password")

    def test_malformed_hash_is_rejected_not_raised(self, hasher: PasswordHashingService) -> None:
        """깨진 해시는 예외가 아니라 False 여야 한다. 로그인 경로가 500 나면 안 된다."""
        assert not hasher.verify("not-a-hash", "anything")

    def test_needs_rehash_when_params_raised(self) -> None:
        """파라미터를 올리면 기존 해시는 리해시 대상이 된다."""
        weak = PasswordHashingService(memory_cost=8192, time_cost=1, parallelism=1)
        strong = PasswordHashingService(memory_cost=65536, time_cost=3, parallelism=4)
        assert strong.needs_rehash(weak.hash("password-1234"))
        assert not weak.needs_rehash(weak.hash("password-1234"))


class TestTokenHashing:
    def test_deterministic_for_lookup(self) -> None:
        """토큰은 해시로 조회하므로 결정적이어야 한다(솔트 없음)."""
        assert hash_token("abc") == hash_token("abc")

    def test_sha256_hex_length(self) -> None:
        assert len(hash_token("abc")) == 64

    def test_constant_time_equals(self) -> None:
        assert constant_time_equals("a", "a")
        assert not constant_time_equals("a", "b")

"""초대 토큰.

DB 에 토큰 행을 따로 두지 않고, 만료 시각을 담아 암호화한 값을 그대로 준다.
AES-GCM 이라 위조가 불가능하고, 서버는 상태를 갖지 않는다.

**상태가 없다는 것은 토큰이 만료 전까지 몇 번이든 통한다는 뜻이다.** 한 번만
쓰이게 만드는 것은 이 파일이 아니라 `UserService.activate_with_password` 의
`status != "invited"` 검사다 — 그 한 줄이 없으면 오래된 초대 메일 한 통이
이미 활성화된 계정의 비밀번호를 다시 정하는 열쇠가 된다. 고칠 때 같이 본다.
"""

from __future__ import annotations

import json
from uuid import UUID

from ieum.config import Settings
from ieum.core.crypto import SecretBox
from ieum.core.exceptions import ValidationError
from ieum.core.time import in_seconds, utcnow

INVITE_PURPOSE = "identity.invite"
DEFAULT_TTL_SECONDS = 7 * 24 * 3600


def _box(settings: Settings) -> SecretBox:
    return SecretBox(settings.secret_key.get_secret_value(), purpose=INVITE_PURPOSE)


def encode_invite_token(
    user_id: UUID, settings: Settings, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> str:
    payload = {
        "user_id": str(user_id),
        "expires_at": in_seconds(ttl_seconds).isoformat(),
    }
    return _box(settings).encrypt(json.dumps(payload, separators=(",", ":")))


def decode_invite_token(token: str, settings: Settings) -> UUID:
    try:
        payload = json.loads(_box(settings).decrypt(token))
    except Exception as exc:
        raise ValidationError("초대 토큰이 유효하지 않다.", code="identity.invite_invalid") from exc

    from datetime import datetime

    if datetime.fromisoformat(payload["expires_at"]) <= utcnow():
        raise ValidationError("초대 토큰이 만료됐다.", code="identity.invite_expired")
    return UUID(payload["user_id"])

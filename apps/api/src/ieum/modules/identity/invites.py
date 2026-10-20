"""초대 토큰.

DB 에 토큰 행을 따로 두지 않고, 만료 시각을 담아 암호화한 값을 그대로 준다.
AES-GCM 이라 위조가 불가능하고, 서버는 상태를 갖지 않는다.

**상태가 없다는 것은 토큰이 만료 전까지 몇 번이든 통한다는 뜻이다.** 한 번만
쓰이게 만드는 것은 이 파일이 아니라 `UserService.activate_with_password` 의
`status != "invited"` 검사다 — 그 한 줄이 없으면 오래된 초대 메일 한 통이
이미 활성화된 계정의 비밀번호를 다시 정하는 열쇠가 된다. 고칠 때 같이 본다.

**그 검사만으로는 다시 초대할 때 뚫린다.** 다시 초대하면 계정이 `invited` 로
돌아가 옛 토큰도 상태 검사를 통과한다. 그래서 토큰에 사용자의 초대 세대
(`User.invite_generation`)를 싣고, 받을 때 지금 세대와 맞는지 본다. 세대가
없는 토큰(이 칸이 생기기 전에 나간 것)은 0 으로 읽는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from ieum.config import Settings
from ieum.core.crypto import SecretBox
from ieum.core.exceptions import ValidationError
from ieum.core.time import in_seconds, utcnow

INVITE_PURPOSE = "identity.invite"
DEFAULT_TTL_SECONDS = 7 * 24 * 3600


@dataclass(frozen=True, slots=True)
class InviteToken:
    user_id: UUID
    #: 발급할 때의 `User.invite_generation`. 다르면 다시 초대되기 전의 토큰이다.
    generation: int


def _box(settings: Settings) -> SecretBox:
    return SecretBox(settings.secret_key.get_secret_value(), purpose=INVITE_PURPOSE)


def encode_invite_token(
    user_id: UUID,
    settings: Settings,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    generation: int = 0,
) -> str:
    payload = {
        "user_id": str(user_id),
        "expires_at": in_seconds(ttl_seconds).isoformat(),
        "gen": generation,
    }
    return _box(settings).encrypt(json.dumps(payload, separators=(",", ":")))


def decode_invite(token: str, settings: Settings) -> InviteToken:
    try:
        payload = json.loads(_box(settings).decrypt(token))
        user_id = UUID(payload["user_id"])
        expires_at = datetime.fromisoformat(payload["expires_at"])
        generation = int(payload.get("gen", 0))
    except Exception as exc:
        raise ValidationError("초대 토큰이 유효하지 않다.", code="identity.invite_invalid") from exc

    if expires_at <= utcnow():
        raise ValidationError("초대 토큰이 만료됐다.", code="identity.invite_expired")
    return InviteToken(user_id=user_id, generation=generation)


def decode_invite_token(token: str, settings: Settings) -> UUID:
    """사용자 id 만. 세대까지 봐야 하는 자리(초대 수락)는 `decode_invite` 를 쓴다."""
    return decode_invite(token, settings).user_id

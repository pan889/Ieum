"""만족도 조사 토큰과 보낼지 말지의 판단 (feature-map C11).

`email.py`·`automation.py` 와 같은 층 나누기다: 여기는 값만 다루므로 손으로
적어 시험할 수 있고, 행을 쓰는 것은 `survey.py` 다.

## 링크에 상태를 두지 않는다

초대 토큰(`identity/invites.py`)과 같은 모양이다: DB 에 토큰 행을 두지 않고
만료 시각을 담아 암호화한 값을 그대로 준다. AES-GCM 이라 위조가 불가능하고
서버는 상태를 갖지 않는다.

**한 번만 받는 것은 토큰이 아니라 티켓이 보증한다.** `csat_score` 가 이미
차 있으면 거절한다 — 토큰을 한 번 쓰고 태우려면 토큰 행이 필요하고, 그러면
"메일을 두 번 열면 두 번째는 안 된다" 같은 이상한 규칙이 생긴다.

## 왜 로그인을 안 시키는가

게스트 요청(C1)에는 계정이 없다. 계정이 있는 고객에게도 로그인부터 시키면
응답률이 떨어지고, 그러면 모은 점수는 "로그인할 의지가 있는 사람들" 의
점수다 — 그건 만족도가 아니다.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from ieum.config import Settings
from ieum.core.crypto import SecretBox
from ieum.core.exceptions import ValidationError
from ieum.core.time import in_seconds, utcnow

SURVEY_PURPOSE = "desk.csat"

#: 링크의 수명. 한 달이 지나면 그때의 기분을 지금 묻는 셈이다.
DEFAULT_TTL_SECONDS = 30 * 24 * 3600

#: 고를 수 있는 점수. 5점 척도다 — `ticket_ext` 의 CHECK 와 같다.
SCORES = (1, 2, 3, 4, 5)

#: 한마디의 길이 상한. 티켓 본문이 아니다.
MAX_COMMENT = 1000


def _box(settings: Settings) -> SecretBox:
    return SecretBox(settings.secret_key.get_secret_value(), purpose=SURVEY_PURPOSE)


def encode_survey_token(
    issue_id: UUID, settings: Settings, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> str:
    payload = {
        "issue_id": str(issue_id),
        "expires_at": in_seconds(ttl_seconds).isoformat(),
    }
    return _box(settings).encrypt(json.dumps(payload, separators=(",", ":")))


def decode_survey_token(token: str, settings: Settings) -> UUID:
    try:
        payload = json.loads(_box(settings).decrypt(token))
    except Exception as exc:
        raise ValidationError("조사 링크가 유효하지 않다.", code="desk.csat_token_invalid") from exc

    if datetime.fromisoformat(payload["expires_at"]) <= utcnow():
        raise ValidationError("조사 링크가 만료됐다.", code="desk.csat_token_expired")
    return UUID(payload["issue_id"])


def clean_answer(score: object, comment: object) -> tuple[int, str | None]:
    """고객이 보낸 답을 다듬는다. 못 쓰는 값이면 거절한다.

    점수는 **필수다.** 한마디만 남기는 답을 받으면 "만족도 3.8" 을 낼 때
    분모가 무엇인지 말할 수 없다.
    """
    # `True` 는 파이썬에서 `int` 다. 체크박스 하나가 1점이 되게 두지 않는다.
    if isinstance(score, bool) or not isinstance(score, int) or score not in SCORES:
        raise ValidationError("1에서 5 사이의 점수여야 한다.", code="desk.csat_score_invalid")
    if comment is None:
        return score, None
    if not isinstance(comment, str):
        raise ValidationError("한마디는 글자여야 한다.", code="desk.csat_comment_invalid")
    text = comment.strip()
    if not text:
        return score, None
    if len(text) > MAX_COMMENT:
        raise ValidationError(f"한마디는 {MAX_COMMENT}자까지다.", code="desk.csat_comment_too_long")
    return score, text

"""웹훅 전송.

HMAC-SHA256 서명을 붙여 POST 하고, 실패하면 지수 백오프로 재시도한다.
서명은 수신자가 "정말 우리가 보낸 게 맞는지" 확인하는 유일한 수단이다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import timedelta

import httpx

from ieum.core.logging import get_logger
from ieum.core.time import utcnow
from ieum.modules.notify.models import Webhook, WebhookDelivery

log = get_logger(__name__)

MAX_ATTEMPTS = 6
TIMEOUT_SECONDS = 10.0
#: 응답 본문은 앞부분만 남긴다. 전부 담으면 로그 테이블이 폭발한다.
EXCERPT_LIMIT = 1000
#: 연속 실패가 이만큼 쌓이면 웹훅을 끈다. 죽은 엔드포인트를 영원히 두드리지 않는다.
DISABLE_AFTER_FAILURES = 20

SIGNATURE_HEADER = "X-Ieum-Signature"
EVENT_HEADER = "X-Ieum-Event"
DELIVERY_HEADER = "X-Ieum-Delivery"
TIMESTAMP_HEADER = "X-Ieum-Timestamp"


def backoff(attempt: int) -> timedelta:
    """1분 → 2 → 4 → 8 → 16 → 32분. 상한을 둬서 무한정 늘어나지 않게 한다."""
    return timedelta(seconds=min(60 * (2 ** max(0, attempt - 1)), 1800))


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """`v1=<hex>` 형태.

    타임스탬프를 서명에 포함해 재전송 공격을 막는다. 수신자는 타임스탬프가
    너무 오래됐으면 거절하면 된다.
    """
    message = timestamp.encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"v1={digest}"


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    ok: bool
    status_code: int | None = None
    excerpt: str | None = None
    error: str | None = None


async def post(
    webhook: Webhook, delivery: WebhookDelivery, secret: str, *, client: httpx.AsyncClient
) -> DeliveryOutcome:
    timestamp = str(int(utcnow().timestamp()))
    body = json.dumps(
        {
            "id": str(delivery.id),
            "event": delivery.event_type,
            "created_at": delivery.created_at.isoformat(),
            "data": delivery.payload,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        EVENT_HEADER: delivery.event_type,
        DELIVERY_HEADER: str(delivery.id),
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: sign(secret, timestamp, body),
        "User-Agent": "Ieum-Webhook/1",
    }

    try:
        response = await client.post(
            webhook.url, content=body, headers=headers, timeout=TIMEOUT_SECONDS
        )
    except Exception as exc:
        return DeliveryOutcome(ok=False, error=f"{type(exc).__name__}: {exc}"[:500])

    excerpt = response.text[:EXCERPT_LIMIT] if response.text else None
    # 2xx 만 성공. 3xx 는 리다이렉트를 따라가지 않는다 — 웹훅 대상이 조용히
    # 바뀌는 경로가 되면 안 된다.
    return DeliveryOutcome(
        ok=200 <= response.status_code < 300,
        status_code=response.status_code,
        excerpt=excerpt,
    )


def apply_outcome(webhook: Webhook, delivery: WebhookDelivery, outcome: DeliveryOutcome) -> None:
    """전송 결과를 행에 반영한다. 상태 전이는 여기 한 곳에서만 한다.

    카운터는 `or 0` 으로 받는다. SQLAlchemy 의 `default=0` 은 INSERT 시점에
    적용되므로, 아직 flush 되지 않은 행에서는 None 이다.
    """
    delivery.attempts = (delivery.attempts or 0) + 1
    delivery.response_code = outcome.status_code
    delivery.response_excerpt = outcome.excerpt
    delivery.error = outcome.error

    if outcome.ok:
        delivery.status = "delivered"
        delivery.delivered_at = utcnow()
        delivery.next_retry_at = None
        webhook.consecutive_failures = 0
        return

    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = "abandoned"
        delivery.next_retry_at = None
    else:
        delivery.status = "pending"
        delivery.next_retry_at = utcnow() + backoff(delivery.attempts)

    webhook.consecutive_failures = (webhook.consecutive_failures or 0) + 1
    if webhook.consecutive_failures >= DISABLE_AFTER_FAILURES:
        # 이미 꺼진 웹훅을 또 끄는 건 무해하다. 로그만 한 번 남긴다.
        was_enabled = webhook.enabled is not False
        webhook.enabled = False
        webhook.disabled_reason = f"연속 {webhook.consecutive_failures}회 실패"
        if was_enabled:
            log.warning(
                "webhook.auto_disabled",
                webhook_id=str(webhook.id),
                failures=webhook.consecutive_failures,
            )

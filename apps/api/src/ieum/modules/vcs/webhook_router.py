"""코드 호스트가 두드리는 문 (A22, M6).

**이 라우터는 인증이 없다.** 그래서 파일을 따로 뒀다 — 인증 있는 경로들
사이에 섞어 두면 다음에 라우트를 더하는 사람이 "이 파일은 로그인해야
들어온다" 로 읽는다.

인증이 없는 대신 **서명이 있다.** 순서가 중요하다:

1. 저장소를 id 로 찾는다. 없으면 404 — 서명을 견줄 키가 없다.
2. 경로의 호스트 이름과 저장한 것이 같은지 본다. 다르면 404 — 우리가 발급한
   주소가 아니다. 이 검사가 **경로로 확인 방식을 고르지 못하게** 막는다.
3. 몸의 서명을 확인한다. 틀리면 401. 여기까지 통과하지 못한 요청은
   아무것도 남기지 않는다.
4. 그 다음에야 몸을 읽는다.

## 다시 온 것을 막지 않는다

GitHub 의 HMAC 에는 시각이 없다. 그래서 한 번 새어 나간 전송은 영원히
재생할 수 있고, 우리는 그걸 서명으로 구별할 수 없다. 대신 **결과가 같게**
둔다 — `(issue, repo, kind, ref)` 유니크가 같은 커밋을 두 줄로 쌓지 않는다
(`models.py`). 재생은 제목을 다시 쓰는 것으로 끝난다.

## 못 붙인 것을 오류로 만들지 않는다

키가 없는 커밋, 없는 이슈, 우리 프로젝트가 아닌 키 — 전부 정상이다. 그때
4xx 를 주면 코드 호스트는 재전송을 반복하다 웹훅을 빨간색으로 칠하고,
사람은 그걸 끈다. 200 에 **몇 개를 붙였는지**를 담아 돌려준다.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.deps import DbSession
from ieum.core.exceptions import AuthenticationError, NotFoundError, ValidationError
from ieum.core.logging import get_logger
from ieum.core.time import utcnow
from ieum.modules.vcs.models import PROVIDERS, Repository
from ieum.modules.vcs.service import record, secret_box
from ieum.modules.vcs.webhooks import (
    Change,
    parse_github,
    parse_gitlab,
    verify_github,
    verify_gitlab,
)

log = get_logger(__name__)

vcs_webhooks_router = APIRouter(prefix="/vcs", tags=["vcs"])

#: 받아들일 몸의 크기. 인증 없는 문이므로 상한이 있어야 한다 — 없으면 몸
#: 하나로 프로세스의 메모리를 채울 수 있다. GitHub 자신이 25MB 에서 자르고,
#: 커밋 100개(`MAX_CHANGES`)를 담은 푸시는 이 안에 한참 들어온다.
MAX_BODY_BYTES = 8 * 1024 * 1024


class DeliveryResponse(BaseModel):
    """전송 하나의 결과. **코드 호스트의 전송 로그에 남는 몸이다** — 사람이
    그 화면에서 "왜 안 붙었나" 를 읽는다."""

    #: 이 몸에서 읽어낸 변경 수(커밋·PR). 0 이면 우리가 보는 종류가 아니다.
    received: int
    #: 이슈에 새로 붙은 링크 수. `received` 가 있는데 0 이면 커밋 메시지에
    #: 이 저장소의 프로젝트 키가 없었거나, 이미 붙어 있던 것이다.
    linked: int


@vcs_webhooks_router.post("/{provider}/{repository_id}", response_model=DeliveryResponse)
async def receive_delivery(
    request: Request,
    session: DbSession,
    provider: str,
    repository_id: UUID,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_gitlab_token: str | None = Header(default=None),
    x_gitlab_event: str | None = Header(default=None),
) -> DeliveryResponse:
    """푸시·PR 을 받아 이슈에 잇는다. **로그인하지 않는다** — 서명으로 본다."""
    repository = await _repository(session, provider, repository_id)
    body = await _body(request)
    _verify(repository, body=body, signature=x_hub_signature_256, token=x_gitlab_token)
    # 이벤트 이름의 헤더는 호스트마다 다르다. 저장한 호스트로 고른다 —
    # 경로를 믿지 않는 이유는 `_repository` 에 적어 뒀다.
    event = x_github_event if repository.provider == "github" else x_gitlab_event

    # **여기서 시각을 남긴다.** `record` 가 아니라 이 자리인 이유: GitHub 은
    # 웹훅을 만들 때 `ping` 을 먼저 보낸다. 링크를 만들 때만 적으면, 배선을
    # 옳게 끝낸 사람의 화면이 "아무것도 못 받았다" 로 보인다 — 그게 이 값을
    # 둔 이유였는데 정반대로 말하게 된다.
    #
    # 몸을 못 읽어 아래에서 거절하면(422) 이 값도 안 남는다 — 커밋에 닿지
    # 않기 때문이고, 그게 맞다: 그때 코드 호스트의 전송 로그에는 우리 422 가
    # 남으므로 양쪽 화면이 같은 이야기를 한다.
    repository.last_event_at = utcnow()

    if not repository.is_enabled:
        # 꺼 둔 저장소다. 받은 것은 적어 두고(위) 붙이지는 않는다 — 끄고
        # 나서도 전송이 오는지는 운영자가 알아야 한다.
        await session.commit()
        # `event=` 는 structlog 이 메시지 이름으로 쓰는 열이다. 그래서
        # 호스트가 보낸 이벤트 이름은 `hook_event` 로 적는다.
        log.info("vcs.delivery_ignored", repository=str(repository.id), hook_event=event or "")
        return DeliveryResponse(received=0, linked=0)

    changes = _parse(repository.provider, event, body)
    linked = await record(session, repository, changes)
    await session.commit()
    log.info(
        "vcs.delivery",
        repository=str(repository.id),
        provider=repository.provider,
        hook_event=event or "",
        received=len(changes),
        linked=linked,
    )
    return DeliveryResponse(received=len(changes), linked=linked)


async def _repository(session: AsyncSession, provider: str, repository_id: UUID) -> Repository:
    """주소가 가리키는 저장소. 없거나 호스트가 다르면 404.

    호스트 이름을 견주는 이유: 확인 방식이 호스트마다 다른데(`webhooks.py`),
    경로를 그대로 믿으면 보내는 쪽이 **어느 방식으로 확인받을지 고를 수
    있다.** 저장한 값으로만 고른다.
    """
    if provider not in PROVIDERS:
        raise NotFoundError("그런 웹훅 주소는 없다.")
    row = await session.get(Repository, repository_id)
    if row is None or row.provider != provider:
        raise NotFoundError("그런 웹훅 주소는 없다.")
    return row


async def _body(request: Request) -> bytes:
    """몸을 **날바이트로** 읽는다. GitHub 의 HMAC 은 이 바이트열에 붙는다 —
    JSON 으로 풀어 다시 직렬화하면 한 글자가 달라져도 서명이 어긋난다."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise ValidationError("웹훅 몸이 너무 크다.", code="vcs.payload_too_large")
    body = await request.body()
    # 위의 검사는 `Content-Length` 를 보내는 요청만 걸러낸다. chunked 전송에는
    # 그 헤더가 없으므로 **읽은 뒤 한 번 더** 본다 — 실제로 막는 것은 이쪽이다.
    if len(body) > MAX_BODY_BYTES:
        raise ValidationError("웹훅 몸이 너무 크다.", code="vcs.payload_too_large")
    return body


def _verify(
    repository: Repository, *, body: bytes, signature: str | None, token: str | None
) -> None:
    """서명을 확인한다. 통과하면 아무 말도 하지 않고, 틀리면 401 을 던진다."""
    secret = secret_box().decrypt(repository.secret_enc)
    if repository.provider == "github":
        ok = verify_github(body=body, secret=secret, header=signature)
    else:
        ok = verify_gitlab(secret=secret, header=token)
    if not ok:
        # **경고로 남긴다.** 연동이 조용할 때 운영자가 찾는 줄이 이것이다:
        # 아무것도 안 오는 것과, 오고 있는데 키가 틀린 것은 다른 고장이다.
        log.warning(
            "vcs.bad_signature",
            repository=str(repository.id),
            provider=repository.provider,
        )
        raise AuthenticationError("웹훅 서명이 맞지 않는다.", code="vcs.bad_signature")


def _parse(provider: str, event: str | None, body: bytes) -> list[Change]:
    if not event:
        # 이벤트 이름이 없으면 무엇이 왔는지 모른다. 몸을 읽지 않고 지나간다 —
        # 오류로 만들지 않는 이유는 파일 머리 3절에 있다.
        return []
    payload = _payload(body)
    if provider == "github":
        return parse_github(event, payload)
    return parse_gitlab(event, payload)


def _payload(body: bytes) -> dict[str, Any]:
    """JSON 으로 읽는다. GitHub 의 폼 인코딩도 받는다.

    폼 인코딩을 받는 이유: GitHub 의 웹훅 설정 화면은 content type 을 고르게
    하고, `application/x-www-form-urlencoded` 를 고르면 몸이
    `payload=<URL 인코딩된 JSON>` 으로 온다. 안 받으면 그 설치는 전송마다
    422 를 받는데, 화면에는 "커밋이 안 붙는다" 로만 보인다.
    """
    raw = body
    if raw[:8] == b"payload=":
        found = parse_qs(raw.decode("utf-8", errors="replace")).get("payload")
        if not found:
            raise ValidationError("웹훅 몸을 읽을 수 없다.", code="vcs.invalid_payload")
        raw = found[0].encode("utf-8")
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationError("웹훅 몸을 읽을 수 없다.", code="vcs.invalid_payload") from exc
    if not isinstance(parsed, dict):
        raise ValidationError("웹훅 몸을 읽을 수 없다.", code="vcs.invalid_payload")
    return parsed


__all__ = ["MAX_BODY_BYTES", "vcs_webhooks_router"]

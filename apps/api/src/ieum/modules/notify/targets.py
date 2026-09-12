"""웹훅이 **어디로** 나갈 수 있는가.

웹훅은 우리 서버가 남의 주소로 대신 요청을 보내 주는 기능이다. 그래서 주소를
정하는 사람은 우리 서버가 닿는 곳이면 어디든 두드릴 수 있다 — 클라우드
메타데이터(`169.254.169.254`), 같은 망의 Redis·Postgres, 우리 자신의 내부 API.

여기에 전송 기록이 **응답 본문 앞부분을 남긴다**는 점이 겹친다. 그 기록은
웹훅을 볼 수 있는 사람에게 화면에 그대로 보인다. 둘을 합치면 프로젝트 관리자
권한 하나로 "내부 아무 주소나 열어서 읽는" 도구가 된다. 메타데이터 엔드포인트
에서는 그게 곧 클라우드 자격 증명이다.

그래서 이름이 아니라 **실제 IP** 를 본다. `evil.example.com` 이 `127.0.0.1` 을
가리키면 이름은 아무 문제가 없어 보인다.

그리고 확인한 IP 로 **바로 붙는다.** 확인만 하고 이름으로 다시 붙으면, 그
사이에 DNS 가 답을 바꿔치기할 수 있다(DNS rebinding) — 확인한 주소와 실제로
붙는 주소가 달라지면 확인한 뜻이 없다.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from ieum.core.exceptions import ValidationError

#: 사설이 아니지만 내보내면 안 되는 것들. `ipaddress` 의 `is_private` 이
#: 안 잡는 자리를 여기서 메운다.
_EXTRA_BLOCKED = (
    # 통신사 NAT(RFC 6598). 파이썬은 이걸 사설로 치지 않는다.
    ipaddress.ip_network("100.64.0.0/10"),
    # IPv6 에서 IPv4 를 가리키는 다른 표기들. `::ffff:` 는 파이썬이 잡지만
    # 이 둘은 안 잡는다.
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("2002::/16"),
)

MAX_URL_LENGTH = 2000


class BlockedTargetError(Exception):
    """보낼 수 없는 주소다. 전송 기록에 사유로 남는다."""


@dataclass(frozen=True, slots=True)
class PinnedTarget:
    """확인이 끝난 목적지.

    `url` 은 호스트 자리가 **IP 로 바뀐** 주소다. 이름으로 다시 붙지 않으려고
    그렇게 한다. 대신 `host_header` 로 원래 이름을 알려 주고, TLS 는
    `sni_hostname` 으로 원래 이름에 대고 증명서를 검사한다 — 둘 다 원래
    이름이어야 받는 쪽도 우리도 맞는 것을 본다.
    """

    url: str
    host_header: str
    sni_hostname: str


def check_shape(url: str) -> None:
    """사람이 주소를 넣는 자리에서 부르는 검사. 모양만 본다.

    실제로 어디로 가는지는 보낼 때 봐야 한다(`pin`) — 지금 공개 주소를
    가리키는 이름이 내일 내부 주소를 가리킬 수 있다.
    """
    if not url.startswith(("http://", "https://")):
        raise ValidationError("http 또는 https URL 이어야 한다.", code="notify.invalid_webhook_url")
    if len(url) > MAX_URL_LENGTH:
        raise ValidationError("URL 이 너무 길다.", code="notify.invalid_webhook_url")
    parts = urlsplit(url)
    if not parts.hostname:
        raise ValidationError("URL 에 호스트가 없다.", code="notify.invalid_webhook_url")
    try:
        _ = parts.port
    except ValueError as exc:  # "http://a:포트" 같은 것
        raise ValidationError(
            "URL 의 포트가 올바르지 않다.", code="notify.invalid_webhook_url"
        ) from exc


def is_public(raw: str) -> bool:
    """이 IP 로 내보내도 되는가."""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return False
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    return not any(ip in net for net in _EXTRA_BLOCKED if ip.version == net.version)


async def pin(url: str, *, allow_private: bool) -> PinnedTarget:
    """이름을 풀어 확인하고, 확인한 IP 로 붙을 주소를 만든다.

    이름이 여러 주소를 가리키면 **하나라도** 막힌 것이 있으면 거절한다.
    공개 주소 하나에 내부 주소 하나를 섞어 두고 돌려 가며 주는 수법이 있다.
    """
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise BlockedTargetError("URL 에 호스트가 없다")
    port = parts.port or (443 if parts.scheme == "https" else 80)

    if allow_private:
        # 사내망에만 있는 수신처로 보내야 하는 설치본이 있다. 끄는 것은
        # **설정으로만** 되고, 기본은 막는 쪽이다.
        return PinnedTarget(url=url, host_header=parts.netloc, sni_hostname=host)

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=1)  # SOCK_STREAM
    except OSError as exc:
        raise BlockedTargetError(f"주소를 풀 수 없다: {exc}") from exc
    if not infos:
        raise BlockedTargetError("주소를 풀 수 없다")

    addresses = [str(info[4][0]) for info in infos]
    blocked = [a for a in addresses if not is_public(a)]
    if blocked:
        raise BlockedTargetError(f"내부 주소로는 보내지 않는다: {', '.join(sorted(set(blocked)))}")

    chosen = addresses[0]
    literal = f"[{chosen}]" if ":" in chosen else chosen
    pinned = urlunsplit((parts.scheme, f"{literal}:{port}", parts.path, parts.query, ""))
    return PinnedTarget(url=pinned, host_header=parts.netloc, sni_hostname=host)


__all__ = [
    "MAX_URL_LENGTH",
    "BlockedTargetError",
    "PinnedTarget",
    "check_shape",
    "is_public",
    "pin",
]

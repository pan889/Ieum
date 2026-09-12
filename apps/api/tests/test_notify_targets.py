"""웹훅이 내부 주소로 나가지 않는가.

**왜 이게 심각한가.** 웹훅은 우리 서버가 대신 요청을 보내 주는 기능이고,
전송 기록에는 응답 본문 앞부분(`response_excerpt`)이 남아 화면에 그대로
보인다. 둘을 합치면 프로젝트 관리자 권한 하나로 "내부 주소를 아무거나 열어서
읽는" 도구가 된다 — `169.254.169.254` 를 가리키면 그게 곧 클라우드 자격
증명이다.

그래서 여기서 지키는 것은 두 가지다.

1. 막힌 주소로는 **요청 자체가 안 나간다.**
2. 그때 응답 발췌가 **남지 않는다.**
"""

from __future__ import annotations

import re
from uuid import uuid4

import httpx
import pytest

from ieum.core.exceptions import ValidationError
from ieum.core.time import utcnow
from ieum.modules.notify import delivery as wd
from ieum.modules.notify.models import Webhook, WebhookDelivery
from ieum.modules.notify.targets import BlockedTargetError, check_shape, is_public, pin


class TestWhichAddressesAreAllowed:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",  # 우리 자신
            "0.0.0.0",  # noqa: S104 — 지정 안 함. 리눅스에서는 로컬로 붙는다
            "10.0.0.5",  # 사내망
            "172.17.0.2",  # 도커 브리지 — 옆 컨테이너의 Redis·Postgres
            "192.168.1.1",
            "169.254.169.254",  # 클라우드 메타데이터. 제일 나쁜 경우다
            "100.64.0.1",  # 통신사 NAT. 파이썬은 이걸 사설로 안 친다
            "240.0.0.1",  # 예약
            "224.0.0.1",  # 멀티캐스트
            "::1",
            "fe80::1",
            "fc00::1",
            "::ffff:127.0.0.1",  # IPv6 로 적은 루프백
            "::ffff:169.254.169.254",  # IPv6 로 적은 메타데이터
            "64:ff9b::7f00:1",  # NAT64 로 적은 루프백
            "2002:7f00:1::",  # 6to4 로 적은 루프백
            "그런 주소 없다",
        ],
    )
    def test_these_never_go_out(self, address: str) -> None:
        assert is_public(address) is False

    @pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700::1111"])
    def test_these_are_fine(self, address: str) -> None:
        assert is_public(address) is True


class TestTheShapeCheck:
    """사람이 주소를 넣는 자리. **모양만** 본다."""

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "gopher://x/_%0d%0a",  # 예전에 Redis 를 때리던 통로다
            "ftp://example.com/",
            "//example.com/",
            "http://",  # 호스트가 없다
            "http://example.com:포트/",
        ],
    )
    def test_it_refuses_what_is_not_a_web_address(self, url: str) -> None:
        with pytest.raises(ValidationError):
            check_shape(url)

    def test_it_refuses_a_very_long_one(self) -> None:
        with pytest.raises(ValidationError):
            check_shape("https://example.com/" + "x" * 3000)

    def test_it_lets_a_normal_one_through(self) -> None:
        check_shape("https://hooks.example.com/ieum?token=abc")

    def test_it_does_not_resolve_the_name(self) -> None:
        """여기서 이름을 풀어 봐야 소용없다 — 오늘 공개 주소여도 내일 바뀐다.

        그래서 **등록은 통과하고**, 실제 검사는 보낼 때 한다. 이걸 뒤집어
        놓으면(등록할 때만 보면) 검사가 있다는 착각만 남는다.
        """
        check_shape("http://localhost:6379/")


class TestPinning:
    async def test_it_refuses_a_name_that_points_inside(self) -> None:
        with pytest.raises(BlockedTargetError, match="내부 주소"):
            await pin("http://localhost:6379/", allow_private=False)

    async def test_it_refuses_the_metadata_address(self) -> None:
        with pytest.raises(BlockedTargetError, match=re.escape("169.254.169.254")):
            await pin("http://169.254.169.254/latest/meta-data/", allow_private=False)

    async def test_it_connects_to_the_address_it_checked(self) -> None:
        """확인한 IP 로 **바로** 붙는다.

        확인만 하고 이름으로 다시 붙으면 그 사이에 DNS 가 답을 바꿀 수 있다.
        그러면 확인한 주소와 실제로 붙는 주소가 달라진다 — 확인한 뜻이 없다.
        """
        target = await pin("https://93.184.216.34:8443/hook?a=1", allow_private=False)
        assert target.url == "https://93.184.216.34:8443/hook?a=1"
        # 받는 쪽이 어느 이름으로 온 요청인지 알아야 하고, 증명서도 그 이름에
        # 대고 검사해야 한다.
        assert target.host_header == "93.184.216.34:8443"
        assert target.sni_hostname == "93.184.216.34"

    async def test_the_setting_can_open_it_for_an_inside_receiver(self) -> None:
        target = await pin("http://localhost:6379/", allow_private=True)
        assert target.url == "http://localhost:6379/"


class TestNothingLeaksThroughTheDeliveryLog:
    @staticmethod
    def _rows(url: str) -> tuple[Webhook, WebhookDelivery]:
        webhook = Webhook(
            id=uuid4(), name="테스트", scope="global", url=url, secret_enc=b"x", events=["x"]
        )
        delivery = WebhookDelivery(
            id=uuid4(),
            webhook_id=webhook.id,
            event_id=uuid4(),
            event_type="issue.created",
            payload={},
            created_at=utcnow(),
        )
        return webhook, delivery

    async def test_a_blocked_target_never_gets_a_request(self) -> None:
        """**요청 자체가 안 나가야 한다.** 나갔다가 결과를 안 적는 게 아니다."""
        asked: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            asked.append(request)
            return httpx.Response(200, text="비밀")

        webhook, delivery = self._rows("http://169.254.169.254/latest/meta-data/iam/")
        async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
            outcome = await wd.post(webhook, delivery, "시크릿", client=client)

        assert asked == [], "막힌 주소인데 요청이 나갔다"
        assert outcome.ok is False
        assert outcome.excerpt is None, "응답 발췌가 남으면 그게 곧 읽기 통로다"
        assert outcome.error is not None
        assert "169.254.169.254" in outcome.error

    async def test_the_failure_is_recorded_where_people_can_see_it(self) -> None:
        webhook, delivery = self._rows("http://127.0.0.1:6379/")
        async with httpx.AsyncClient() as client:
            outcome = await wd.post(webhook, delivery, "시크릿", client=client)
        wd.apply_outcome(webhook, delivery, outcome)

        assert delivery.status == "pending"  # 재시도는 하되 계속 막힌다
        assert delivery.response_excerpt is None
        assert delivery.error is not None
        assert "보낼 수 없는 주소" in delivery.error

    async def test_an_allowed_target_still_works(self) -> None:
        """막는 쪽만 고치고 보내는 쪽을 망가뜨리면 기능이 죽은 것이다."""
        seen: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(202, text="받았다")

        webhook, delivery = self._rows("https://93.184.216.34/hook")
        async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
            outcome = await wd.post(webhook, delivery, "시크릿", client=client)

        assert outcome.ok is True
        assert outcome.status_code == 202
        assert outcome.excerpt == "받았다"
        assert len(seen) == 1
        assert seen[0].headers["Host"] == "93.184.216.34"
        assert seen[0].headers[wd.SIGNATURE_HEADER].startswith("v1=")

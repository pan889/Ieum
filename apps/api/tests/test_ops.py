"""운영 표면: 살아 있는가, 일할 수 있는가, 무엇이 도는가 (M4).

붙잡는 것 — 첫째가 이 파일의 이유다:

- **준비 상태는 상태 코드로 말한다.** 로드밸런서는 본문을 안 읽는다. 200 에
  `"degraded"` 를 담으면 죽은 인스턴스로 트래픽이 계속 온다.
- **liveness 는 의존 서비스를 안 본다.** 여기서 DB 를 보면 DB 가 흔들릴 때
  오케스트레이터가 앱을 재시작한다 — 살아 있음과 일할 수 있음은 다른 질문이다.
- **`/metrics` 가 이 앱에 대해 말한다.** 한동안 기본 프로세스 지표만 나왔다.
  손잡이가 있는데 아무 것도 안 잡고 있는 것이, 없는 것보다 나쁘다.
- **경로는 템플릿으로 묶는다.** 실제 주소를 라벨로 두면 이슈 하나가 시계열
  하나가 된다.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.events import DomainEvent, events
from ieum.core.heartbeat import all_beats, beat
from ieum.core.ids import new_id
from ieum.core.outbox import outbox_backlog, publish
from ieum.core.time import utcnow


@events.register_event
class _Pinged(DomainEvent):
    event_type = "ops.pinged"
    aggregate_type = "ops"


class TestSayingWhetherWeCanWork:
    async def test_liveness_does_not_touch_anything(self, app_client: httpx.AsyncClient) -> None:
        found = await app_client.get("/healthz")
        assert found.status_code == 200
        assert found.json() == {"status": "ok"}

    async def test_readiness_checks_all_three(self, app_client: httpx.AsyncClient) -> None:
        """DB 만 보면 Redis 가 죽어도 "준비됐다" 고 답한다 — 워커가 아무것도
        못 하는 상태인데.

        **여기서 "ready" 를 요구하지 않는다.** 이 검사는 진짜 인프라를
        찌르는데, 단위 시험 환경에는 그것이 없다. 대신 세 가지를 다 보는지와
        **몸과 코드가 어긋나지 않는지**를 본다.
        """
        found = await app_client.get("/readyz")
        body = found.json()
        assert set(body["checks"]) == {"database", "redis", "storage"}
        ready = all(value == "ok" for value in body["checks"].values())
        assert body["status"] == ("ready" if ready else "degraded")
        assert found.status_code == (200 if ready else 503)

    async def test_a_broken_dependency_gives_503(
        self, app_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**이 시험이 이 파일의 이유다.** 200 에 담은 "degraded" 는 아무도
        안 읽는다."""
        import ieum.main as main

        async def _broken(_settings: object) -> dict[str, str]:
            return {"database": "ok", "redis": "error: ConnectionError", "storage": "ok"}

        monkeypatch.setattr(main, "_probe", _broken)
        found = await app_client.get("/readyz")
        assert found.status_code == 503
        assert found.json()["status"] == "degraded"
        assert found.json()["checks"]["redis"].startswith("error")


class TestSayingWhatIsGoingOn:
    async def test_metrics_talk_about_this_app(self, app_client: httpx.AsyncClient) -> None:
        # 무언가 부르고 나서 긁는다 — 카운터는 요청이 있어야 생긴다.
        await app_client.get("/healthz")
        found = await app_client.get("/metrics")
        assert found.status_code == 200
        body = found.text
        for name in (
            "ieum_http_requests_total",
            "ieum_http_request_duration_seconds",
            "ieum_outbox_pending",
            "ieum_outbox_oldest_age_seconds",
        ):
            assert name in body, f"{name} 이 없다"

    async def test_the_route_label_is_a_template(self, app_client: httpx.AsyncClient) -> None:
        """이슈 하나가 시계열 하나가 되면 며칠 만에 프로메테우스가 무릎을 꿇는다."""
        missing = new_id()
        await app_client.get(f"/api/v1/issues/{missing}")
        body = (await app_client.get("/metrics")).text
        assert str(missing) not in body

    async def test_an_unmatched_path_is_one_series(self, app_client: httpx.AsyncClient) -> None:
        # 스캐너가 시계열을 만들게 두지 않는다.
        await app_client.get("/wp-login.php")
        await app_client.get("/.env")
        body = (await app_client.get("/metrics")).text
        assert 'route="unmatched"' in body
        assert "wp-login" not in body


class TestTheBacklog:
    async def test_an_empty_outbox_is_zero(self, session: AsyncSession) -> None:
        pending, oldest = await outbox_backlog(session)
        assert (pending, oldest) == (0, 0.0)

    async def test_it_reports_the_age_not_just_the_count(self, session: AsyncSession) -> None:
        """백 건이 방금 들어온 것은 정상이고, 한 건이 열 분째 남아 있는 것은
        고장이다. 개수로만 보면 구별되지 않는다."""
        row = publish(session, _Pinged(aggregate_id=new_id()))
        await session.flush()
        row.created_at = utcnow() - __import__("datetime").timedelta(minutes=10)
        await session.flush()

        pending, oldest = await outbox_backlog(session)
        assert pending == 1
        assert oldest > 500


class TestTheHeartbeat:
    async def test_it_keeps_one_row_per_task(self, session: AsyncSession) -> None:
        """이력이 아니라 지금 상태를 담는 표다 — 쌓이면 청소할 사람이 필요해진다."""
        await beat(session, "sweep", duration_seconds=1.5)
        await beat(session, "sweep", duration_seconds=2.5)
        rows = await all_beats(session)
        assert [(r.task, r.duration_seconds) for r in rows] == [("sweep", 2.5)]

    async def test_a_success_clears_the_last_error(self, session: AsyncSession) -> None:
        # 지난 실패가 남아 있으면 지금 실패한 것처럼 보인다.
        await beat(session, "sweep", duration_seconds=1.0, error="RuntimeError: 터졌다")
        await beat(session, "sweep", duration_seconds=1.0)
        [row] = await all_beats(session)
        assert row.last_error is None

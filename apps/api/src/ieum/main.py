"""FastAPI 애플리케이션 조립.

여기서 하는 일은 배선뿐이다. 비즈니스 로직은 모듈의 service 에 있다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis
from redis.asyncio import from_url as redis_from_url
from sqlalchemy import text
from starlette.responses import Response

# 이벤트 카탈로그를 로드해 레지스트리를 채운다 (웹훅 검증이 이걸 본다).
import ieum.event_catalog  # noqa: F401
from ieum.config import Settings, get_settings
from ieum.core.attachment_router import attachments_router
from ieum.core.errors import install_exception_handlers
from ieum.core.heartbeat import all_beats
from ieum.core.logging import configure_logging, get_logger
from ieum.core.metrics import (
    OUTBOX_OLDEST_AGE,
    OUTBOX_PENDING,
    WORKER_FAILING,
    WORKER_LAST_DURATION,
    WORKER_LAST_RUN,
)
from ieum.core.middleware import TraceMiddleware
from ieum.core.outbox import outbox_backlog
from ieum.core.storage import ObjectStore
from ieum.core.time import utcnow
from ieum.db.session import dispose_engine, get_session_factory, init_engine
from ieum.modules.desk.report_router import desk_reports_router
from ieum.modules.desk.router import (
    automation_router,
    calendars_router,
    canned_router,
    customers_router,
    email_router,
    portal_router,
    portals_router,
    queues_router,
    sla_router,
    tickets_router,
)
from ieum.modules.identity.router import (
    audit_router,
    auth_router,
    groups_router,
    sso_admin_router,
    tokens_router,
    users_router,
)
from ieum.modules.identity.scim import ScimError
from ieum.modules.identity.scim_router import scim_router
from ieum.modules.issues.board_router import boards_router
from ieum.modules.issues.calendar_router import calendar_router
from ieum.modules.issues.gantt_router import gantt_router
from ieum.modules.issues.recurring_router import recurrences_router
from ieum.modules.issues.report_router import reports_router
from ieum.modules.issues.router import fields_router, issues_router, workflows_router
from ieum.modules.issues.search_router import filters_router, search_router
from ieum.modules.issues.sprint_router import sprints_router
from ieum.modules.notify.router import (
    notifications_router,
    watches_router,
    webhooks_router,
)
from ieum.modules.org.router import projects_router, roles_router, security_router
from ieum.modules.search.router import router as unified_search_router
from ieum.modules.vcs.router import repositories_router
from ieum.modules.vcs.webhook_router import vcs_webhooks_router
from ieum.modules.wiki.collab_router import collab_router
from ieum.modules.wiki.router import pages_router, spaces_router
from ieum.wiring import install_permissions

log = get_logger(__name__)

API_PREFIX = "/api/v1"


def _build_ops_router(settings: Settings) -> APIRouter:
    """운영 엔드포인트. API 버전 접두사를 붙이지 않는다."""
    router = APIRouter(include_in_schema=False)

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        """liveness. 의존 서비스를 확인하지 않는다.

        여기서 DB 를 보면 **DB 가 흔들릴 때 앱이 재시작된다** — 오케스트레이터는
        liveness 실패를 "프로세스가 망가졌다" 로 읽고 죽인다. 살아 있음과
        일할 수 있음은 다른 질문이고, 뒤쪽이 `/readyz` 다.
        """
        return {"status": "ok"}

    @router.get("/readyz")
    async def readyz(response: Response) -> dict[str, Any]:
        """readiness. 의존 서비스를 **실제로 찔러본다.**

        **몸이 아니라 상태 코드로 말한다.** 로드밸런서는 본문을 안 읽는다 —
        200 에 `"degraded"` 를 담으면 죽은 인스턴스로 트래픽이 계속 온다.
        한동안 그렇게 되어 있었다.

        DB 만 보지 않는다. Redis 가 죽으면 워커가 아무것도 못 하고, S3 가
        죽으면 첨부가 통째로 안 된다 — 둘 다 "일할 수 있다" 가 아니다.
        """
        checks = await _probe(settings)
        ready = all(value == "ok" for value in checks.values())
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if ready else "degraded", "checks": checks}

    @router.get("/metrics")
    async def metrics() -> Response:
        # 긁을 때 채우는 값들. 카운터는 요청마다 늘지만, "지금 몇 개 밀려
        # 있나" 는 물어봐야 안다.
        await _sample_gauges()
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return router


async def _probe(settings: Settings) -> dict[str, str]:
    """의존 서비스 셋을 찔러본다. 하나가 죽어도 나머지를 마저 본다 —
    운영자는 "무엇이" 죽었는지를 알아야 한다."""
    checks: dict[str, str] = {}

    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"

    try:
        # `redis.asyncio.from_url` 에는 타입이 없다. 여기서만 좁혀 쓴다.
        client: Redis = redis_from_url(settings.redis_url)  # type: ignore[no-untyped-call]
        try:
            await client.ping()
            checks["redis"] = "ok"
        finally:
            await client.aclose()
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"

    try:
        checks["storage"] = "ok" if await ObjectStore(settings).reachable() else "error: bucket"
    except Exception as exc:
        checks["storage"] = f"error: {type(exc).__name__}"

    return checks


async def _sample_gauges() -> None:
    """긁는 순간의 값들. **실패해도 응답은 준다** — 지표를 못 읽는 것과
    앱이 죽는 것은 다르다."""
    try:
        async with get_session_factory()() as session:
            pending, oldest = await outbox_backlog(session)
            OUTBOX_PENDING.set(pending)
            OUTBOX_OLDEST_AGE.set(oldest)
            now = utcnow()
            for row in await all_beats(session):
                WORKER_LAST_RUN.labels(row.task).set(row.finished_at.timestamp())
                WORKER_LAST_DURATION.labels(row.task).set(row.duration_seconds)
                WORKER_FAILING.labels(row.task).set(1 if row.last_error else 0)
                # 나이는 프로메테우스가 계산한다(`time() - last_run`). 여기서
                # 미리 빼 두면 긁는 간격만큼 어긋난다.
                del now
    except Exception as exc:
        log.warning("metrics.sample_failed", error=f"{type(exc).__name__}: {exc}")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(debug=settings.debug, json_output=settings.is_production)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_engine(settings)
        log.info("api.startup", env=settings.env)
        try:
            yield
        finally:
            await dispose_engine()
            log.info("api.shutdown")

    app = FastAPI(
        title="Ieum API",
        version="0.1.0",
        description="셀프호스팅 팀 협업 플랫폼 — 이슈·위키·서비스데스크",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(TraceMiddleware, slow_request_ms=settings.slow_query_ms * 5)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,  # 세션 쿠키를 쓴다
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )

    install_exception_handlers(app)
    app.include_router(_build_ops_router(settings))

    # 권한 리졸버 배선. core 는 org 를 import 하지 않으므로 여기서 꽂아 넣는다.
    # **워커도 같은 함수를 부른다** — 두 곳에서 각자 하면 관문 등록이 한쪽에만
    # 있는 날이 온다 (`wiring.py`).
    install_permissions(settings)

    for router in (
        auth_router,
        users_router,
        groups_router,
        tokens_router,
        audit_router,
        sso_admin_router,
        projects_router,
        roles_router,
        security_router,
        issues_router,
        workflows_router,
        fields_router,
        boards_router,
        sprints_router,
        recurrences_router,
        calendar_router,
        gantt_router,
        reports_router,
        desk_reports_router,
        attachments_router,
        search_router,
        filters_router,
        spaces_router,
        pages_router,
        # 동시 편집 (B16). `pages_router` 와 같은 접두사를 쓰지만 표 하나와
        # 소켓 하나뿐이라 파일을 따로 둔다 — WebSocket 은 인증이 다른 길이다.
        collab_router,
        unified_search_router,
        notifications_router,
        watches_router,
        webhooks_router,
        portals_router,
        customers_router,
        tickets_router,
        queues_router,
        canned_router,
        calendars_router,
        sla_router,
        email_router,
        automation_router,
        portal_router,
        repositories_router,
        # 코드 호스트가 두드리는 문 (A22). **인증이 없다** — 액세스 토큰
        # 대신 서명으로 확인한다. 그래서 파일도 라우터도 따로다
        # (`vcs/webhook_router.py`).
        vcs_webhooks_router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    # SCIM 은 **접두사 없이** 붙는다. `/scim/v2` 는 IdP 가 기대하는 경로이고,
    # `/api/v1` 아래로 옮기면 Okta·Entra 의 설정 화면이 받아 주지 않는다.
    app.include_router(scim_router)

    # 그리고 오류도 SCIM 봉투로 나가야 한다. 우리 봉투를 주면 IdP 는 인증
    # 실패인지 서버 오류인지 구별하지 못하고, 대개 재시도 고리에 빠진다.
    @app.exception_handler(ScimError)
    async def _scim_error(_: Request, exc: ScimError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status, media_type="application/scim+json")

    return app


app = create_app()

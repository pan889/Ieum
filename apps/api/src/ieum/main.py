"""FastAPI 애플리케이션 조립.

여기서 하는 일은 배선뿐이다. 비즈니스 로직은 모듈의 service 에 있다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.responses import Response

# 이벤트 카탈로그를 로드해 레지스트리를 채운다 (웹훅 검증이 이걸 본다).
import ieum.event_catalog  # noqa: F401
from ieum.config import Settings, get_settings
from ieum.core.attachment_router import attachments_router
from ieum.core.errors import install_exception_handlers
from ieum.core.logging import configure_logging, get_logger
from ieum.core.middleware import TraceMiddleware
from ieum.core.permissions import PermissionService, set_permission_service
from ieum.db.session import dispose_engine, get_session_factory, init_engine
from ieum.modules.identity.router import (
    audit_router,
    auth_router,
    tokens_router,
    users_router,
)
from ieum.modules.issues import attachments as issue_attachments
from ieum.modules.issues.board_router import boards_router
from ieum.modules.issues.contracts import issue_model
from ieum.modules.issues.router import issues_router
from ieum.modules.issues.search_router import filters_router, search_router
from ieum.modules.issues.service import SecurityLevelGuard
from ieum.modules.notify.router import (
    notifications_router,
    watches_router,
    webhooks_router,
)
from ieum.modules.org.repository import OrgPermissionResolver
from ieum.modules.org.router import projects_router, roles_router
from ieum.modules.search.router import router as unified_search_router
from ieum.modules.wiki import attachments as wiki_attachments
from ieum.modules.wiki.contracts import page_model as wiki_page_model
from ieum.modules.wiki.router import pages_router, spaces_router
from ieum.modules.wiki.service import PageRestrictionGuard

log = get_logger(__name__)

API_PREFIX = "/api/v1"


def _build_ops_router() -> APIRouter:
    """운영 엔드포인트. API 버전 접두사를 붙이지 않는다."""
    router = APIRouter(include_in_schema=False)

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        """liveness. 의존 서비스를 확인하지 않는다."""
        return {"status": "ok"}

    @router.get("/readyz")
    async def readyz() -> dict[str, Any]:
        """readiness. DB 를 실제로 찔러본다."""
        checks: dict[str, str] = {}
        try:
            async with get_session_factory()() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {type(exc).__name__}"

        ready = all(v == "ok" for v in checks.values())
        return {"status": "ready" if ready else "degraded", "checks": checks}

    @router.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return router


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
    app.include_router(_build_ops_router())

    # 권한 리졸버 배선. core 는 org 를 import 하지 않으므로 여기서 꽂아 넣는다.
    permissions = PermissionService(
        resolver=OrgPermissionResolver(),
        step_up_window_seconds=settings.step_up_window_seconds,
    )
    # 객체 수준 제한: 이슈 보안 레벨. 스코프 권한을 통과한 뒤 한 번 더 거른다.
    permissions.register_guard(issue_model(), SecurityLevelGuard())
    # 문서 열람·편집 제한. 스코프 권한을 통과한 뒤 한 번 더 거른다.
    permissions.register_guard(wiki_page_model(), PageRestrictionGuard())
    set_permission_service(permissions)
    # 첨부 소유자별 권한 리졸버. core 는 어떤 모듈이 첨부를 쓰는지 모른다.
    issue_attachments.install()
    wiki_attachments.install()

    for router in (
        auth_router,
        users_router,
        tokens_router,
        audit_router,
        projects_router,
        roles_router,
        issues_router,
        boards_router,
        attachments_router,
        search_router,
        filters_router,
        spaces_router,
        pages_router,
        unified_search_router,
        notifications_router,
        watches_router,
        webhooks_router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    return app


app = create_app()

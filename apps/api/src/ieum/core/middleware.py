"""요청 미들웨어: trace_id 발급·전파, 접근 로그, 느린 요청 경고, 지표."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ieum.core.context import set_trace_id
from ieum.core.logging import get_logger
from ieum.core.metrics import DURATION, IN_FLIGHT, REQUESTS

log = get_logger(__name__)

TRACE_HEADER = "X-Trace-Id"


class TraceMiddleware(BaseHTTPMiddleware):
    """요청마다 trace_id 를 만들어 로그와 에러 응답에 전파한다.

    클라이언트가 보낸 헤더가 있으면 이어받는다 (분산 추적).
    """

    def __init__(self, app: Callable[..., object], *, slow_request_ms: int = 1000) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._slow_request_ms = slow_request_ms

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id = request.headers.get(TRACE_HEADER) or uuid4().hex
        set_trace_id(trace_id)

        started = time.perf_counter()
        IN_FLIGHT.inc()
        try:
            response = await call_next(request)
        finally:
            IN_FLIGHT.dec()
        elapsed_ms = (time.perf_counter() - started) * 1000

        # **경로는 템플릿으로 묶는다.** 실제 주소를 라벨로 두면 이슈 하나가
        # 시계열 하나가 되고, 며칠이면 프로메테우스가 무릎을 꿇는다.
        # 라우트를 못 찾은 요청(404)은 하나로 모은다 — 스캐너가 시계열을
        # 만들게 두지 않는다.
        route = _route_of(request)
        REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        DURATION.labels(request.method, route).observe(elapsed_ms / 1000)

        response.headers[TRACE_HEADER] = trace_id

        # /healthz 같은 폴링 경로는 로그를 채우기만 한다.
        if request.url.path not in ("/healthz", "/readyz", "/metrics"):
            event = "request.slow" if elapsed_ms > self._slow_request_ms else "request"
            log.info(
                event,
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round(elapsed_ms, 1),
            )
        return response


def _route_of(request: Request) -> str:
    """라벨에 쓸 경로. 매칭된 라우트의 **틀**이다.

    Starlette 은 매칭이 끝난 뒤 `request.scope["route"]` 에 라우트를 넣는다.
    없으면(404, 미들웨어 단계 오류) `unmatched` 하나로 모은다.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if isinstance(path, str) else "unmatched"

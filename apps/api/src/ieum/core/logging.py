"""구조화 로깅. JSON 출력 + trace_id 전파 (docs/architecture/overview.md 관측성)."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from ieum.core.context import get_trace_id


def _add_trace_id(
    _logger: Any, _name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """요청 컨텍스트의 trace_id 를 모든 로그에 붙인다."""
    trace_id = get_trace_id()
    if trace_id is not None:
        event_dict["trace_id"] = trace_id
    return event_dict


def configure_logging(*, debug: bool = False, json_output: bool = True) -> None:
    """프로세스 시작 시 1회 호출한다."""
    level = logging.DEBUG if debug else logging.INFO

    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        _add_trace_id,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # 표준 logging 도 같은 포맷으로 흘려보낸다 (uvicorn, sqlalchemy).
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)
    for noisy in ("uvicorn.access", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).handlers.clear()

    # **디버그에서도 조용히 둔다.** `markdown_it` 은 블록 규칙마다 한 줄씩
    # 찍는다 — 문서 하나를 파싱할 때 수십 줄이다. `IEUM_DEBUG=true` 인
    # 설치에서 `ieum reindex` 를 돌리면 그 줄들이 명령의 출력("색인 완료:
    # …")을 통째로 파묻는다. 우리 코드의 디버그 줄을 보려고 켠 스위치가
    # 남의 파서 내부를 보여 주는 스위치가 되면, 결국 아무도 켜지 않는다.
    for chatty in ("markdown_it",):
        logging.getLogger(chatty).setLevel(logging.INFO)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """모듈 로거. 이름을 직접 바인딩한다.

    structlog.stdlib.add_logger_name 은 stdlib 로거의 `.name` 을 읽으므로
    PrintLoggerFactory 와 함께 쓸 수 없다. 여기서 바인딩하면 팩토리와 무관하게
    모든 레코드에 logger 필드가 붙는다.
    """
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name).bind(logger=name)
    return logger

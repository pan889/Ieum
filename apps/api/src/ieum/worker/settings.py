"""arq 워커 설정. `arq ieum.worker.settings.WorkerSettings` 로 띄운다."""

from __future__ import annotations

from typing import Any, ClassVar, cast

from arq.connections import RedisSettings
from arq.cron import cron
from arq.typing import WorkerCoroutine

from ieum.config import get_settings
from ieum.core.logging import configure_logging, get_logger
from ieum.db.session import dispose_engine, init_engine
from ieum.worker.tasks import task_deliver_webhooks, task_drain_outbox, task_sweep

log = get_logger(__name__)


async def startup(_ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(debug=settings.debug, json_output=settings.is_production)
    init_engine(settings)
    log.info("worker.startup", env=settings.env)


async def shutdown(_ctx: dict[str, Any]) -> None:
    await dispose_engine()
    log.info("worker.shutdown")


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        task_drain_outbox,
        task_deliver_webhooks,
        task_sweep,
    ]
    # 아웃박스 지연은 사용자가 체감한다. 15초마다 훑는다. API 가 이벤트를
    # 넣을 때 큐에 바로 밀어 넣어 즉시 처리하는 건 M2 과제로 남긴다.
    # arq 의 WorkerCoroutine 은 Protocol 인데 mypy 가 평범한 async 함수를
    # 그 프로토콜로 인정하지 못한다. 런타임은 정상이라 여기서만 좁힌다.
    cron_jobs: ClassVar[list[Any]] = [
        cron(
            cast("WorkerCoroutine", task_sweep),
            second={0, 15, 30, 45},
            run_at_startup=True,
        )
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    job_timeout = 120

    # arq 는 이걸 **클래스 `__dict__` 에서 그대로** 꺼낸다(`get_kwargs`).
    # 그래서 값이어야 한다 — 메서드로 두면 함수 객체가 그대로 넘어가고
    # arq 가 `.host` 를 찾다가 죽는다. 디스크립터도 안 통한다: `__dict__`
    # 를 직접 읽으므로 `__get__` 이 호출되지 않는다.
    #
    # import 시점에 계산된다. 설정은 전부 기본값이 있어서 환경변수가 비어도
    # 터지지 않고, 워커는 어차피 설정 없이는 할 일이 없다.
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)

"""arq 워커 설정. `arq ieum.worker.settings.WorkerSettings` 로 띄운다."""

from __future__ import annotations

from typing import Any, ClassVar, cast

from arq.connections import RedisSettings
from arq.cron import cron
from arq.typing import WorkerCoroutine

from ieum.config import get_settings
from ieum.core.logging import configure_logging, get_logger
from ieum.db.session import dispose_engine, init_engine
from ieum.wiring import install_permissions
from ieum.worker.tasks import (
    task_deliver_webhooks,
    task_drain_outbox,
    task_mirror_search,
    task_poll_email,
    task_send_digests,
    task_sweep,
)

log = get_logger(__name__)


async def startup(_ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(debug=settings.debug, json_output=settings.is_production)
    init_engine(settings)
    # **앱과 같은 배선을 쓴다.** 워커도 이슈를 만들고(반복 이슈, A27) 알림을
    # 내므로 권한 관문이 꽂혀 있어야 한다 — 없으면 그 경로만 제한을 안 본다.
    install_permissions(settings)
    log.info("worker.startup", env=settings.env)


async def shutdown(_ctx: dict[str, Any]) -> None:
    await dispose_engine()
    log.info("worker.shutdown")


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        task_drain_outbox,
        task_deliver_webhooks,
        task_sweep,
        task_send_digests,
        # 스윕이 이미 부르지만 따로도 등록한다. 관리자가 "지금 메일함을
        # 훑어라" 를 시킬 자리가 있어야 한다 — 채널 설정을 고친 직후에
        # 15초를 기다리며 되는지 아닌지 모르는 상태로 두지 않는다.
        task_poll_email,
        # 검색 미러 (ADR-0015). 백엔드가 Postgres 면 즉시 0 을 돌려준다.
        task_mirror_search,
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
        ),
        # 다이제스트는 매시 정각에 훑는다. **누구에게 보낼지는 받는 사람의
        # 지역 시각**이 정한다 — 전 세계 한 시각에 몰아 보내면 절반에게는
        # 한밤중이다 (notify/digest.py).
        cron(cast("WorkerCoroutine", task_send_digests), minute={0}, second={5}),
        # **검색 미러는 스윕과 따로, 더 자주 돈다** (ADR-0015).
        #
        # 따로 두는 이유: 스윕은 IMAP 왕복을 포함하고, 그 뒤에 서면 "방금
        # 만든 이슈가 검색에 없다" 가 남의 메일 서버 상태에 묶인다.
        #
        # 더 자주 도는 이유: 사람이 기다리는 것이다. 15초 주기에 태우면
        # 실측 지연이 14초였다 — Postgres 백엔드는 같은 트랜잭션이라 0초인
        # 자리이므로, 백엔드를 바꾼 대가가 그만큼 눈에 보인다. 5초로 두면
        # OpenSearch 의 리프레시(약 1초)를 합쳐 대략 6초 안쪽이다.
        #
        # 큐가 비어 있으면 인덱스를 탄 COUNT 한 번이다. 그 값으로 5초는 싸다.
        cron(
            cast("WorkerCoroutine", task_mirror_search),
            second=set(range(0, 60, 5)),
            run_at_startup=True,
        ),
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    # **IMAP 왕복이 이 안에 들어가야 한다.** 메일 채널 하나가 응답하지 않으면
    # 연결 타임아웃(20초)을 쓰고, 채널이 여러 개면 그만큼 쌓인다. 120초로
    # 두면 채널 넷이 동시에 죽은 날 스윕이 통째로 잘리고 — 잘린 자리가
    # 아웃박스 드레인 뒤라 알림도 함께 멈춘다.
    job_timeout = 300

    # **헬스체크가 뜻을 가지려면 자주 갱신돼야 한다.**
    #
    # arq 는 Redis 에 살아 있다는 기록을 남기고 `arq --check` 가 그것을 읽는다.
    # 기록의 수명은 이 주기 + 1초라, 기본값(1시간)이면 워커가 죽고 나서도
    # **한 시간 동안 "정상"** 이라고 답한다. 컨테이너가 통째로 사라지는 것은
    # `restart: unless-stopped` 가 잡지만, 멈춰 선 워커(교착·무한 대기)는
    # 프로세스가 살아 있어서 그것으로 안 잡힌다 — 그 자리를 이 값이 잡는다.
    #
    # 30초마다 작은 키 하나를 쓴다. 그 값으로 이 신호는 싸다.
    health_check_interval = 30

    # arq 는 이걸 **클래스 `__dict__` 에서 그대로** 꺼낸다(`get_kwargs`).
    # 그래서 값이어야 한다 — 메서드로 두면 함수 객체가 그대로 넘어가고
    # arq 가 `.host` 를 찾다가 죽는다. 디스크립터도 안 통한다: `__dict__`
    # 를 직접 읽으므로 `__get__` 이 호출되지 않는다.
    #
    # import 시점에 계산된다. 설정은 전부 기본값이 있어서 환경변수가 비어도
    # 터지지 않고, 워커는 어차피 설정 없이는 할 일이 없다.
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)

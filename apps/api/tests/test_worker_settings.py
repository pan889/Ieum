"""워커가 실제로 부팅되는 모양인지.

태스크 함수를 직접 부르는 테스트는 arq 가 설정을 어떻게 읽는지 확인하지
않는다. 그 틈으로 `redis_settings` 를 메서드로 둔 실수가 빠져나갔고,
워커는 컨테이너에서 부팅조차 못 했다(`'staticmethod' object has no
attribute 'host'`).
"""

from __future__ import annotations

from arq.connections import RedisSettings
from arq.worker import create_worker, get_kwargs

from ieum.worker.settings import WorkerSettings


def test_redis_settings_is_a_value_not_a_callable() -> None:
    """arq 는 속성으로 읽는다. 함수가 오면 `.host` 를 찾다가 죽는다."""
    settings = WorkerSettings.redis_settings
    assert isinstance(settings, RedisSettings)
    assert isinstance(settings.host, str)


def test_arq_can_build_a_worker_from_our_settings() -> None:
    """CLI 가 하는 일을 그대로 한다 — 붙지는 않고 조립까지만."""
    worker = create_worker(WorkerSettings)
    assert worker.max_jobs == WorkerSettings.max_jobs
    assert worker.job_timeout_s == WorkerSettings.job_timeout
    # 등록한 태스크가 전부 실려 있어야 한다.
    registered = set(worker.functions)
    for task in WorkerSettings.functions:
        assert task.__qualname__ in registered or task.__name__ in registered


def test_settings_expose_the_keys_arq_reads() -> None:
    kwargs = get_kwargs(WorkerSettings)
    assert "redis_settings" in kwargs
    assert isinstance(kwargs["redis_settings"], RedisSettings)
    # 크론이 빠지면 아웃박스가 영원히 안 비워진다.
    assert kwargs["cron_jobs"]

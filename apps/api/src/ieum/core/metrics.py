"""무엇이 도는지 밖에서 볼 수 있게 한다.

`/metrics` 는 처음부터 있었지만 **아무것도 등록돼 있지 않았다** — 기본 프로세스
지표만 나왔고, 이 앱에 대해서는 한 줄도 말하지 않았다. 손잡이가 있는데 아무
것도 잡고 있지 않은 모양이라, 있는 줄 알고 안 보는 것이 가장 나쁘다.

## 무엇을 재는가

운영자가 실제로 묻는 것만 잰다. 라벨을 늘리면 시계열이 곱셈으로 늘어나므로
(카디널리티), **사용자 id·이슈 키 같은 것은 절대 라벨로 두지 않는다.**

- **요청**: 몇 건이 어떤 상태로 끝났고 얼마나 걸렸나. 경로는 **템플릿**으로
  묶는다(`/api/v1/issues/{issue_id}`) — 실제 주소로 두면 이슈 하나가 시계열
  하나가 된다.
- **아웃박스**: 밀려 있나. 밀린다는 것은 알림·메일·웹훅·SLA 가 전부 늦는다는
  뜻이고, 로그를 읽어야만 알 수 있으면 아무도 모른다.
- **워커**: 마지막으로 언제 돌았나. 워커는 다른 프로세스라 자기 메모리의
  지표를 API 의 `/metrics` 에 실을 수 없다 — 그래서 DB 에 심장박동을 남기고
  API 가 그것을 읽어 낸다.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter(
    "ieum_http_requests_total",
    "처리한 HTTP 요청 수",
    labelnames=("method", "route", "status"),
)

#: 구간을 손으로 정한다. 기본값은 초 단위 웹 요청에 맞춰져 있지 않다.
DURATION = Histogram(
    "ieum_http_request_duration_seconds",
    "HTTP 요청 처리 시간",
    labelnames=("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

IN_FLIGHT = Gauge("ieum_http_requests_in_flight", "지금 처리 중인 요청 수")

OUTBOX_PENDING = Gauge("ieum_outbox_pending", "아직 처리 안 된 아웃박스 이벤트 수")

OUTBOX_OLDEST_AGE = Gauge(
    "ieum_outbox_oldest_age_seconds",
    "가장 오래된 미처리 아웃박스 이벤트의 나이. 밀리는 것은 개수보다 나이로 드러난다",
)

WORKER_LAST_RUN = Gauge(
    "ieum_worker_last_run_timestamp_seconds",
    "워커 작업이 마지막으로 끝난 시각 (epoch)",
    labelnames=("task",),
)

WORKER_LAST_DURATION = Gauge(
    "ieum_worker_last_duration_seconds",
    "워커 작업의 마지막 소요 시간",
    labelnames=("task",),
)

WORKER_FAILING = Gauge(
    "ieum_worker_last_failed",
    "워커 작업의 마지막 실행이 실패했는가 (1/0)",
    labelnames=("task",),
)

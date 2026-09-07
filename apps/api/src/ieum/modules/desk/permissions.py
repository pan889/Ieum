"""desk 가 정의하는 권한 상수."""

from __future__ import annotations

from ieum.core.permissions import PermissionDef, ScopeKind, registry

PROJECT = frozenset({ScopeKind.PROJECT})
GLOBAL = frozenset({ScopeKind.GLOBAL})

#: 포털과 요청 유형 정의. 프로젝트 단위다 — 포털이 프로젝트에 붙어 있다.
PORTAL_MANAGE = "desk.portal.manage"
#: 큐를 보고 티켓을 다룬다. data-model.md 의 권한 예시에 있는 이름이다.
QUEUE_WORK = "desk.queue.work"
#: 큐·정형 응답 정의 변경. 프로젝트 단위다.
#:
#: `queue.work`(일하는 것)와 나누는 이유: 큐의 IQL 은 상담원 전원이 무엇을
#: 보는지 정한다. 조건을 잘못 적으면 티켓이 아무 큐에도 안 걸려 조용히
#: 방치되므로, 일하는 권한과 같은 손잡이에 두지 않는다.
QUEUE_MANAGE = "desk.queue.manage"
#: SLA 정책과 업무 달력. **전역 + step-up 이다.**
#:
#: 전역인 이유: 업무 달력은 설치 전체에서 공유한다(업무 시간은 회사의
#: 성질이다). 정책은 프로젝트 단위지만 같은 손잡이에 둔다 — 목표는 정할 수
#: 있는데 그것을 재는 달력은 볼 수 없는 상태가 되면, "4시간" 이 실제로 몇
#: 시간인지 모르고 정하는 셈이다.
#:
#: step-up 인 이유: SLA 는 조직이 고객에게 한 약속이고, 목표를 늘리면 지표가
#: 좋아진다. 자기가 평가받는 숫자를 자기가 고치는 자리다.
SLA_MANAGE = "desk.sla.manage"
#: 고객 조직 목록·소속 편집. 전역이다 — 조직은 프로젝트에 속하지 않는다.
CUSTOMER_MANAGE = "desk.customer.manage"
#: 메일 채널 정의. **프로젝트 단위 + step-up 이다.**
#:
#: 프로젝트 단위인 이유: 채널은 프로젝트에 붙는다(포털과 같다).
#:
#: step-up 인 이유: 이 손잡이는 **메일함의 비밀번호를 받는다.** 그리고 받는
#: 주소를 바꾸면 그 뒤로 오는 고객의 메일이 다른 프로젝트의 티켓이 된다 —
#: 세션을 훔친 사람이 조용히 할 수 있는 일 중 가장 큰 것에 가깝다.
EMAIL_MANAGE = "desk.email.manage"
#: 자동화 규칙 정의. **프로젝트 단위 + step-up 이다.**
#:
#: step-up 인 이유: 규칙은 사람이 안 보는 사이에 티켓을 바꾸고 **고객에게
#: 글을 보낸다.** 세션을 훔친 사람이 "모든 신규 티켓에 이 문구로 회신" 을
#: 하나 걸어 두면 그 뒤로 오는 모든 요청에 그 글이 배달된다 — 큐 조건을
#: 고치는 것과는 무게가 다르다.
AUTOMATION_MANAGE = "desk.automation.manage"

registry.register_many(
    [
        PermissionDef(PORTAL_MANAGE, PROJECT, "포털·요청 유형 정의 변경"),
        PermissionDef(QUEUE_WORK, PROJECT, "큐에서 티켓 처리"),
        PermissionDef(QUEUE_MANAGE, PROJECT, "큐·정형 응답 정의 변경"),
        PermissionDef(SLA_MANAGE, GLOBAL, "SLA 정책·업무 달력 관리", requires_step_up=True),
        # 고객 조직은 설치 전체에서 하나의 목록이고, 소속을 바꾸면 그 고객이
        # 보는 티켓의 범위가 바뀐다. 그래서 전역 + step-up 이다.
        PermissionDef(CUSTOMER_MANAGE, GLOBAL, "고객 조직·소속 관리", requires_step_up=True),
        PermissionDef(EMAIL_MANAGE, PROJECT, "메일 채널 관리", requires_step_up=True),
        PermissionDef(AUTOMATION_MANAGE, PROJECT, "자동화 규칙 관리", requires_step_up=True),
    ]
)

ALL = (
    PORTAL_MANAGE,
    QUEUE_WORK,
    QUEUE_MANAGE,
    SLA_MANAGE,
    CUSTOMER_MANAGE,
    EMAIL_MANAGE,
    AUTOMATION_MANAGE,
)

#: 상담원에게 기본으로 주는 묶음. 시드가 사용한다.
AGENT_DEFAULTS = (QUEUE_WORK,)

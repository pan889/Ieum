"""모든 도메인 이벤트를 한곳에서 모은다.

`core.events.registry` 는 이벤트 클래스가 import 될 때 채워진다. 그래서
"어쩌다 import 된 것만" 등록된 상태가 될 수 있고, 그러면 웹훅 설정에서
멀쩡한 이벤트 타입이 "등록되지 않았다"고 거부된다.

모델 목록을 `db.models` 에 모은 것과 같은 이유다. 새 모듈이 이벤트를
추가하면 여기 한 줄을 더한다.
"""

from __future__ import annotations

import ieum.modules.identity.events
import ieum.modules.issues.events  # noqa: F401
from ieum.core.events import events


def known_event_types() -> frozenset[str]:
    """등록된 이벤트 타입 전부. 웹훅 카탈로그가 이걸 쓴다."""
    return events.known_event_types()


__all__ = ["known_event_types"]

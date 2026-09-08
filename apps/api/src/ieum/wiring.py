"""기동 배선 — **앱과 워커가 같은 것을 쓴다.**

권한 리졸버와 객체 수준 관문, 첨부 소유자 리졸버는 `core` 가 모듈을 import
하지 않기 때문에 기동할 때 꽂아 넣어야 한다. 그 일을 두 곳(앱·워커)에서 각자
하면 등록이 한쪽에만 있는 날이 오고, 그날 **워커가 만드는 이슈만 제한을 안
본다** — 그런 종류의 어긋남은 화면에 안 보이고 사고로만 드러난다.

그래서 한 함수로 모으고 둘이 부른다.
"""

from __future__ import annotations

from ieum.config import Settings
from ieum.core.permissions import PermissionService, set_permission_service
from ieum.modules.issues import attachments as issue_attachments
from ieum.modules.issues.contracts import issue_model
from ieum.modules.issues.service import SecurityLevelGuard
from ieum.modules.org.repository import OrgPermissionResolver
from ieum.modules.wiki import attachments as wiki_attachments
from ieum.modules.wiki.contracts import page_model as wiki_page_model
from ieum.modules.wiki.service import PageRestrictionGuard


def install_permissions(settings: Settings) -> PermissionService:
    """권한 서비스를 만들어 전역에 꽂고 돌려준다."""
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
    return permissions


__all__ = ["install_permissions"]

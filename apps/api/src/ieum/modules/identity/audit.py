"""감사 로그의 어휘와 내보내기.

기록은 `AuditRepository.record()` 가 하고, 여기서는 **무엇을 기록하는가**를
한 곳에 모은다. 문자열을 부르는 자리마다 적어 두면 오타가 조용히 새 행동을
만들고, 조회 화면은 그 목록을 어디서도 알 수 없다.

행동 이름은 `<영역>.<대상>.<한 일>` 이다. 앞자리로 묶이므로 화면에서 영역별로
나눠 보여 줄 수 있고, 인덱스(`ix_audit_log_action_created_at`)도 접두사 검색에
그대로 쓰인다.
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.pagination import PageRequest
from ieum.modules.identity.models import AuditLog, User
from ieum.modules.identity.repository import AuditFilter, AuditRepository, UserRepository

# ── 인증 ────────────────────────────────────────────────────────
LOGIN_SUCCEEDED = "auth.login.succeeded"
LOGIN_FAILED = "auth.login.failed"
LOGOUT = "auth.logout"
REFRESH_REUSE_DETECTED = "auth.refresh.reuse_detected"
SESSION_REVOKED = "auth.session.revoked"
SESSION_REVOKED_ALL = "auth.session.revoked_all"

# ── 2FA ─────────────────────────────────────────────────────────
MFA_ENROLLED = "auth.mfa.enrolled"
MFA_VERIFIED = "auth.mfa.verified"
MFA_FAILED = "auth.mfa.failed"
MFA_BACKUP_CODES_ISSUED = "auth.mfa.backup_codes_issued"

# ── 계정 ────────────────────────────────────────────────────────
USER_INVITED = "identity.user.invited"
USER_ACTIVATED = "identity.user.activated"
USER_LOCALE_CHANGED = "identity.user.locale_changed"
USER_PASSWORD_CHANGED = "identity.user.password_changed"  # noqa: S105 - 행동 이름이다

# ── SSO ─────────────────────────────────────────────────────────
SSO_LOGIN_SUCCEEDED = "auth.sso.login_succeeded"
SSO_USER_PROVISIONED = "auth.sso.user_provisioned"
SSO_ACCOUNT_LINKED = "auth.sso.account_linked"

# ── 보안 정책 ───────────────────────────────────────────────────
SECURITY_MFA_POLICY_CHANGED = "org.security.mfa_policy_changed"

# ── 토큰 ────────────────────────────────────────────────────────
TOKEN_ISSUED = "identity.token.issued"  # noqa: S105 - 행동 이름이다
TOKEN_REVOKED = "identity.token.revoked"  # noqa: S105 - 행동 이름이다

#: 조회 화면의 필터 목록. 테이블에서 `DISTINCT action` 을 긁지 않는다 — 행이
#: 쌓이면 그 한 번이 인덱스를 통째로 훑고, 아직 한 번도 안 일어난 행동은
#: 목록에서 빠져 "그런 건 기록 안 하나" 로 읽힌다.
ACTIONS = (
    LOGIN_SUCCEEDED,
    LOGIN_FAILED,
    LOGOUT,
    REFRESH_REUSE_DETECTED,
    SESSION_REVOKED,
    SESSION_REVOKED_ALL,
    MFA_ENROLLED,
    MFA_VERIFIED,
    MFA_FAILED,
    MFA_BACKUP_CODES_ISSUED,
    USER_INVITED,
    USER_ACTIVATED,
    USER_LOCALE_CHANGED,
    USER_PASSWORD_CHANGED,
    SSO_LOGIN_SUCCEEDED,
    SSO_USER_PROVISIONED,
    SSO_ACCOUNT_LINKED,
    SECURITY_MFA_POLICY_CHANGED,
    TOKEN_ISSUED,
    TOKEN_REVOKED,
)

#: 한 번에 읽어 오는 페이지 크기. 커서 페이지네이션 상한과 같다.
PAGE_SIZE = 100

#: 내보내기 전체 상한. 무제한이면 감사 담당자가 자기 서버를 멈춘다.
MAX_ROWS = 100_000

COLUMNS = (
    "created_at",
    "action",
    "actor_email",
    "actor_id",
    "target_type",
    "target_id",
    "ip",
    "metadata",
)


async def stream_csv(session: AsyncSession, filters: AuditFilter) -> AsyncIterator[bytes]:
    """감사 로그를 CSV 로 흘려보낸다.

    첫 청크에 BOM 을 넣는다. 없으면 엑셀이 UTF-8 을 로컬 인코딩으로 읽어
    한국어가 통째로 깨진다 — 사용자는 "내보내기가 고장났다" 고 본다.

    권한은 부르는 쪽(`AuditService`)이 이미 봤다. 여기서 다시 보지 않는다 —
    두 곳에서 거르면 한 곳이 바뀔 때 다른 곳이 뒤처진다.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    yield b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")

    repository = AuditRepository(session)
    users = UserRepository(session)
    emails: dict[UUID, str] = {}
    cursor: str | None = None
    emitted = 0

    while True:
        page = await repository.list(filters, PageRequest(limit=PAGE_SIZE, cursor=cursor))
        if not page.items:
            break

        await _fill_emails(users, emails, page.items)

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        for row in page.items:
            writer.writerow(
                [
                    row.created_at.isoformat(),
                    row.action,
                    emails.get(row.actor_id, "") if row.actor_id else "",
                    str(row.actor_id) if row.actor_id else "",
                    row.target_type or "",
                    str(row.target_id) if row.target_id else "",
                    row.ip or "",
                    # 한 칸에 JSON 을 넣는다. 열로 펼치면 행동마다 열이 달라진다.
                    _flatten(row.audit_metadata),
                ]
            )
            emitted += 1
        yield buffer.getvalue().encode("utf-8")

        if page.next_cursor is None or emitted >= MAX_ROWS:
            break
        cursor = page.next_cursor


def _flatten(metadata: dict[str, object]) -> str:
    """`키=값` 을 공백으로 이어 붙인다. JSON 을 그대로 넣으면 엑셀이 따옴표를
    두 겹으로 만들어 사람이 못 읽는다."""
    return " ".join(f"{key}={value}" for key, value in sorted(metadata.items()))


async def _fill_emails(
    users: UserRepository, emails: dict[UUID, str], rows: list[AuditLog]
) -> None:
    """행위자 이메일을 채운다. id 만 있는 감사 로그는 읽을 수 없다."""
    missing = {row.actor_id for row in rows if row.actor_id is not None} - emails.keys()
    for actor_id in missing:
        user: User | None = await users.get(actor_id)
        # 지워진 계정도 로그에는 남는다. 빈 칸으로 두면 누구였는지 알 수 없다.
        emails[actor_id] = user.email if user else "(deleted)"


__all__ = [
    "ACTIONS",
    "COLUMNS",
    "LOGIN_FAILED",
    "LOGIN_SUCCEEDED",
    "LOGOUT",
    "MAX_ROWS",
    "MFA_BACKUP_CODES_ISSUED",
    "MFA_ENROLLED",
    "MFA_FAILED",
    "MFA_VERIFIED",
    "REFRESH_REUSE_DETECTED",
    "SECURITY_MFA_POLICY_CHANGED",
    "SESSION_REVOKED",
    "SESSION_REVOKED_ALL",
    "SSO_ACCOUNT_LINKED",
    "SSO_LOGIN_SUCCEEDED",
    "SSO_USER_PROVISIONED",
    "TOKEN_ISSUED",
    "TOKEN_REVOKED",
    "USER_ACTIVATED",
    "USER_INVITED",
    "USER_LOCALE_CHANGED",
    "USER_PASSWORD_CHANGED",
    "stream_csv",
]

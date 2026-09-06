"""일괄 편집.

**부분 성공을 허용한다.** 100건을 고르면 그중 몇 개는 권한이 없거나 검증에
걸릴 수 있다. 전부 실패시키면 한 건 때문에 99건이 막히고, 조용히 건너뛰면
사용자는 무엇이 안 됐는지 모른다. 건별 결과를 돌려준다.

각 건은 세이브포인트 안에서 돈다. 하나가 터져도 세션이 오염되지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import IeumError, ValidationError
from ieum.core.logging import get_logger
from ieum.core.permissions import PermissionService
from ieum.modules.issues.service import IssueService

log = get_logger(__name__)

#: 한 번에 다룰 수 있는 이슈 수. 상한이 없으면 사용자가 자기 서버를 멈춘다.
MAX_BATCH = 100


@dataclass(frozen=True, slots=True)
class BulkFailure:
    issue_id: UUID
    code: str
    message: str


@dataclass(slots=True)
class BulkResult:
    updated: list[UUID] = field(default_factory=list)
    failed: list[BulkFailure] = field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        return not self.failed


class BulkService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def edit(
        self,
        actor: Actor,
        issue_ids: list[UUID],
        *,
        changes: dict[str, Any] | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
        transition_id: UUID | None = None,
    ) -> BulkResult:
        unique = list(dict.fromkeys(issue_ids))
        _validate_batch(unique, changes, add_labels, remove_labels, transition_id)

        service = IssueService(self._s, self._perms)
        result = BulkResult()

        for issue_id in unique:
            # 세이브포인트. 한 건이 터져도 앞의 성공이 살아남고 세션도
            # 쓸 수 있는 상태로 남는다.
            savepoint = await self._s.begin_nested()
            try:
                if changes or add_labels or remove_labels:
                    labels = await self._merged_labels(
                        service, actor, issue_id, add_labels, remove_labels
                    )
                    await service.update(
                        actor,
                        issue_id,
                        dict(changes or {}),
                        labels=labels,
                    )
                if transition_id is not None:
                    await service.transition(actor, issue_id, transition_id)
                await savepoint.commit()
                result.updated.append(issue_id)
            except IeumError as exc:
                await savepoint.rollback()
                result.failed.append(
                    BulkFailure(issue_id=issue_id, code=exc.code, message=exc.message)
                )

        log.info(
            "issues.bulk_edit",
            actor=str(actor.user_id),
            requested=len(unique),
            updated=len(result.updated),
            failed=len(result.failed),
        )
        return result

    async def _merged_labels(
        self,
        service: IssueService,
        actor: Actor,
        issue_id: UUID,
        add: list[str] | None,
        remove: list[str] | None,
    ) -> list[str] | None:
        """라벨은 통째로 교체하는 API 라, 더하기·빼기를 여기서 합쳐 준다.

        고른 이슈마다 기존 라벨이 다르므로 클라이언트가 계산할 수 없다.
        """
        if add is None and remove is None:
            return None
        view = await service.get(actor, issue_id)
        current = set(view.labels)
        current |= set(add or [])
        current -= set(remove or [])
        return sorted(current)


def _validate_batch(
    issue_ids: list[UUID],
    changes: dict[str, Any] | None,
    add_labels: list[str] | None,
    remove_labels: list[str] | None,
    transition_id: UUID | None,
) -> None:
    if not issue_ids:
        raise ValidationError("이슈를 하나 이상 고른다.", code="issues.bulk_empty")
    if len(issue_ids) > MAX_BATCH:
        raise ValidationError(
            f"한 번에 {MAX_BATCH}건까지 바꿀 수 있다.",
            code="issues.bulk_too_many",
            details={"max": MAX_BATCH, "requested": len(issue_ids)},
        )
    if not changes and not add_labels and not remove_labels and transition_id is None:
        raise ValidationError("바꿀 내용이 없다.", code="issues.bulk_no_changes")


__all__ = ["MAX_BATCH", "BulkFailure", "BulkResult", "BulkService"]
